"""Single-endpoint checks against pinned KBF reference packs.

KBF's probes, prompts, parser, tolerances and statistical verdict are reused.
The transport is deliberately bounded; saved HTTP bodies are the source of
every replayed value. Hashes detect accidental changes, not authenticate APIs.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
from typing import Any, Callable
from urllib.parse import quote, urlsplit
from uuid import uuid4

import httpx

from .records import content_hash, file_hash
from .vendor.kbf import kbf_common as kbf
from .vendor.kbf.kbf_test import compute_verdict
from .vendor.kbf.protocols import build_request
from .reference_library import catalog_entries, load_reference, ReferenceValidationError

PACK_DIR = Path(__file__).parent / "data" / "reference_packs"
SCHEMA = "relay-fingerprint/1"
BATCH_SIZE = 10
MAX_ATTEMPTS = 3
SCREENING_BATCHES = 3
SCREENING_BUDGET = {"max_requests": 4, "max_output_tokens": 4096}
MAX_BODY = 64 * 1024
MAX_EVIDENCE = 3 * 1024 * 1024
SETTINGS = {
    "protocol": "openai-chat", "temperature": kbf.TEST_TEMP,
    "system_prompt": kbf.TEST_SYS_PROMPT, "batch_size": BATCH_SIZE,
    "max_tokens_per_probe": 80, "max_batch_attempts": MAX_ATTEMPTS,
    "alpha": kbf.KBF_ALPHA, "confidence": kbf.KBF_CONFIDENCE,
    "min_coverage": 0.5, "network_retries": 0,
    "discovery_strategies": list(kbf.THINKING_STRATEGIES),
    "expanded_token_fallback": False,
    "max_response_bytes": MAX_BODY, "max_evidence_bytes": MAX_EVIDENCE,
}
# Exact discovery questions from KBF discover_thinking_strategy (Apache-2.0).
DISCOVERY_PROMPT = kbf.USER_TPL.format(sentences="\n".join([
    "(1) The boiling point of ethanol at standard pressure in degrees Celsius is ___",
    "(2) The atomic number of gold is ___",
    "(3) The speed of light in vacuum in metres per second is ___",
    "(4) The melting point of iron in degrees Celsius is ___",
    "(5) The number of bones in an adult human body is ___",
    "(6) The atomic mass of carbon-12 in unified atomic mass units is ___",
    "(7) The boiling point of water at standard pressure in degrees Celsius is ___",
    "(8) The gravitational acceleration on Earth's surface in metres per second squared is ___",
    "(9) The atomic number of oxygen is ___",
    "(10) The melting point of pure ice at standard pressure in degrees Celsius is ___",
]))
STATUS_TEXT = {
    "difference_detected": "发现显著差异",
    "no_difference_detected": "未检出明显差异",
    "insufficient_evidence": "证据不足",
}


def _clean(value: Any, secrets: tuple[str, ...] = ()) -> Any:
    """Remove supplied credentials, including echoed dictionary keys."""
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return re.sub(r"\bsk-[A-Za-z0-9_-]{12,}", "[REDACTED]", value)
    if isinstance(value, list):
        return [_clean(v, secrets) for v in value]
    if isinstance(value, dict):
        return {_clean(str(k), secrets): "[REDACTED]" if re.sub(r"[^a-z]", "", str(k).lower()) in
                {"apikey", "authorization", "xapikey", "accesstoken", "secret", "password"}
                else _clean(v, secrets) for k, v in value.items()}
    return value


def _load_pack(pack_id: str, library_dir: Path | None = None) -> tuple[dict, dict]:
    return load_reference(pack_id, library_dir)


def _batches(data: dict) -> list[dict]:
    grouped: dict[str, list] = defaultdict(list)
    for index, probe in enumerate(data["probes"]):
        if probe["domain"] not in kbf.DOMAIN_CLOZE:
            raise ValueError("Reference pack has an unsupported domain")
        grouped[probe["domain"]].append((index, probe))
    batches = []
    for domain, probes in sorted(grouped.items()):
        for offset in range(0, len(probes), BATCH_SIZE):
            items = probes[offset:offset + BATCH_SIZE]
            sentences = [f'({i + 1}) {kbf.DOMAIN_CLOZE[domain].format(name=p["name"])}'
                         for i, (_, p) in enumerate(items)]
            batches.append({"id": len(batches), "domain": domain,
                            "indices": [i for i, _ in items], "items": items,
                            "prompt": kbf.USER_TPL.format(sentences="\n".join(sentences)),
                            "max_tokens": 80 * len(items)})
    return batches


def _selection(data: dict, profile: str) -> tuple[list[dict], dict]:
    """Select unchanged batches before collection, independently of answers."""
    all_batches = _batches(data)
    if profile == "full":
        selected = all_batches
    else:
        domains = sorted({b["domain"] for b in all_batches})
        # Spread the first round across the domain list, then visit the rest.
        positions = list(dict.fromkeys(i * len(domains) // min(SCREENING_BATCHES, len(domains))
                                       for i in range(min(SCREENING_BATCHES, len(domains)))))
        order = [domains[i] for i in positions] + [d for i, d in enumerate(domains) if i not in positions]
        queues = {d: [b for b in all_batches if b["domain"] == d] for d in domains}
        selected = []
        while len(selected) < min(SCREENING_BATCHES, len(all_batches)):
            for domain in order:
                if queues[domain]:
                    selected.append(queues[domain].pop(0))
                    if len(selected) == min(SCREENING_BATCHES, len(all_batches)):
                        break
    return selected, {"method": "all_batches_v1" if profile == "full" else "domain_round_robin_v1",
                      "batch_ids": [b["id"] for b in selected],
                      "probe_indices": [i for b in selected for i in b["indices"]],
                      "domains": list(dict.fromkeys(b["domain"] for b in selected))}


def _protocol_limits(batches: list[dict], profile: str) -> dict:
    attempts = MAX_ATTEMPTS if profile == "full" else 1
    return {"max_requests": len(kbf.THINKING_STRATEGIES) + attempts * len(batches),
            "max_output_tokens": 800 * len(kbf.THINKING_STRATEGIES) + attempts * sum(b["max_tokens"] for b in batches)}


def _attempts(profile: str) -> int:
    return MAX_ATTEMPTS if profile == "full" else 1


def _settings(config: dict) -> dict:
    return {**SETTINGS, "timeout": config["timeout"], "max_batch_attempts": _attempts(config["profile"])}


class _BudgetStop(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__({"budget_max_requests": "剩余请求次数配额不足以继续当前计划，保留已完成证据。",
                          "budget_max_output_tokens": "剩余输出配额不足以发送下一份完整请求，保留已完成证据。"}[code])


def _cost(requests: list[dict], synthetic: bool = False) -> dict:
    fields = ("prompt_tokens", "completion_tokens", "total_tokens")
    totals, counts = dict.fromkeys(fields, 0), dict.fromkeys(fields, 0)
    reported = 0
    for row in requests:
        raw = row.get("raw_response")
        usage = raw.get("usage") if isinstance(raw, dict) else None
        seen = False
        if isinstance(usage, dict) and not synthetic:
            for field in fields:
                value = usage.get(field)
                if type(value) is int and value >= 0:
                    totals[field] += value
                    counts[field] += 1
                    seen = True
        reported += int(seen)
    return {"request_count": len(requests),
            "real_requests": 0 if synthetic else len(requests),
            "requested_output_tokens": sum(row["request"]["max_tokens"] for row in requests),
            "usage": {**{field: totals[field] if counts[field] else None for field in fields},
                      "reported_requests": reported, "missing_requests": len(requests) - reported,
                      "reported_fields": counts,
                      "complete": bool(requests) and all(count == len(requests) for count in counts.values()),
                      "source": "provider_reported", "synthetic": synthetic},
            "note": "requested_output_tokens 为已发送请求的 max_tokens 之和；usage 是供应商已报告的小计，缺失项未知，均不等于完整账单。"}


def list_reference_packs(library_dir: Path | None = None) -> list[dict]:
    packs = []
    for row, directory in catalog_entries(library_dir):
        try:
            meta, data = _load_pack(row["id"], library_dir)
            packs.append({**meta, "batch_count": len(_batches(data))})
        except (ReferenceValidationError, OSError):
            if directory == PACK_DIR:
                raise
            # Cached data damage does not hide independently valid installed packs.
            continue
    return packs


def _config(payload: dict, library_dir: Path | None = None) -> tuple[dict, str | None, str | None]:
    try:
        if not isinstance(payload, dict) or set(payload) - {"reference_pack", "candidate", "timeout", "profile", "budget"}:
            raise ValueError
        candidate = payload["candidate"]
        if not isinstance(candidate, dict) or set(candidate) - {"api_base", "model", "api_key", "api_key_env"}:
            raise ValueError
        base, model = candidate["api_base"], candidate["model"]
        if not isinstance(base, str) or not isinstance(model, str) or not model.strip() or len(model) > 200:
            raise ValueError
        parts = urlsplit(base)
        if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.query or parts.fragment or parts.port == 0
                or re.search(r"\s", base) or any(ord(c) < 32 for c in model)):
            raise ValueError
        if parts.scheme == "http" and parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError
        base = base.rstrip("/")
        if not base.endswith("/chat/completions"):
            base += "/chat/completions"
        timeout = payload.get("timeout", 30)
        if type(timeout) is not int or not 1 <= timeout <= 300:
            raise ValueError
        key, env = candidate.get("api_key"), candidate.get("api_key_env")
        if key is not None and (not isinstance(key, str) or not 8 <= len(key) <= 4096 or re.search(r"\s", key)):
            raise ValueError
        if env is not None and (not isinstance(env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", env)):
            raise ValueError
        if key and env:
            raise ValueError
        _, data = _load_pack(payload["reference_pack"], library_dir)
        profile = payload.get("profile", "screening")
        if profile not in {"screening", "full"}:
            raise ValueError
        batches, selection = _selection(data, profile)
        defaults = SCREENING_BUDGET if profile == "screening" else _protocol_limits(batches, profile)
        supplied_budget = payload.get("budget", {})
        if not isinstance(supplied_budget, dict) or set(supplied_budget) - set(defaults):
            raise ValueError
        budget = {**defaults, **supplied_budget}
        if (type(budget["max_requests"]) is not int or not 1 <= budget["max_requests"] <= 2000
                or type(budget["max_output_tokens"]) is not int or not 1 <= budget["max_output_tokens"] <= 2_000_000):
            raise ValueError
        return {"reference_pack": payload["reference_pack"], "candidate": {"api_base": base, "model": model.strip()},
                "timeout": timeout, "profile": profile, "budget": budget, "selection": selection}, key, env
    except (KeyError, ValueError, TypeError):
        raise ValueError("Invalid fingerprint configuration; use a known pack, HTTPS URL, model name, timeout 1–300, screening/full profile and positive integer budgets; raw values omitted") from None


def plan_fingerprint(payload: dict, *, library_dir: Path | None = None) -> dict:
    config, key, _ = _config(payload, library_dir)
    meta, data = _load_pack(config["reference_pack"], library_dir)
    batches, selection = _selection(data, config["profile"])
    limits = _protocol_limits(batches, config["profile"])
    budget = config["budget"]
    maximum = min(limits["max_requests"], budget["max_requests"], budget["max_output_tokens"] // min(800, *(b["max_tokens"] for b in batches)))
    minimum_requests, minimum_output = len(batches) + 1, 800 + sum(b["max_tokens"] for b in batches)
    return _clean({"kind": "fingerprint", "profile": config["profile"], "reference": meta, "candidate": config["candidate"],
                   "probe_count": len(selection["probe_indices"]), "batch_count": len(batches),
                   "reference_probe_count": len(data["probes"]), "reference_batch_count": len(_batches(data)),
                   "selection": selection, "budget": budget,
                   "minimum_requests": minimum_requests, "minimum_output_tokens_total": minimum_output,
                   "budget_can_complete_minimum": budget["max_requests"] >= minimum_requests and budget["max_output_tokens"] >= minimum_output,
                   "request_count": maximum, "max_requests": maximum,
                   "discovery_max_requests": min(len(kbf.THINKING_STRATEGIES), maximum),
                   "max_output_tokens_total": min(limits["max_output_tokens"], budget["max_output_tokens"], maximum * 800),
                   "batch_size": BATCH_SIZE, "settings": _settings(config),
                   "max_batch_attempts": _attempts(config["profile"]),
                   "notes": ["初筛只描述选定题目的回答差异，暂不作统计检验或身份判断。" if config["profile"] == "screening" else "完整比较使用原始题目与统计判据；实际误报率及模型识别能力仍待独立校准。",
                             "参数试探和格式尝试都计入预算；剩余额度不够时停止并保存结果。",
                             "最低请求量假设首次参数试探成功且每批只发送一次；预算不足时会保存部分证据。",
                             "输出 token 上限不等于费用上限；输入、隐藏推理和供应商计费另计。",
                             "模型别名原样发送；参考库之外的模型不作身份识别。"]}, (key,) if key else ())


def _request(model: str, prompt: str, max_tokens: int, strategy: str, provider: str | None = None) -> dict:
    _, body, _, _, messages = build_request("openai-chat", model, kbf.TEST_SYS_PROMPT, prompt,
                                          kbf.TEST_TEMP, max_tokens, "not-persisted", provider)
    kbf.apply_thinking_strategy(strategy, body, messages)
    return body


def _provider(candidate: dict, meta: dict) -> str | None:
    return meta["provider"] if urlsplit(candidate["api_base"]).hostname == "openrouter.ai" else None


def _manifest(config: dict, meta: dict, synthetic: bool = False) -> dict:
    return {"type": "manifest", "schema": SCHEMA, "run_id": uuid4().hex,
            "reference_pack": meta["id"], "reference_sha256": meta["sha256"],
            "candidate": config["candidate"], "settings": _settings(config),
            "profile": config["profile"], "budget": config["budget"], "selection": config["selection"],
            "synthetic": synthetic, "created_at": datetime.now(timezone.utc).isoformat()}


def _append(path: Path, records: list[dict], value: dict) -> dict:
    row = {**value, "sequence": len(records), "previous_hash": records[-1]["record_hash"] if records else None}
    row["record_hash"] = content_hash(row)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
        stream.flush()
    records.append(row)
    return row


def _raw_text(row: dict) -> str | None:
    if row.get("http_status") != 200 or row.get("error"):
        return None
    try:
        value = row["raw_response"]["choices"][0]["message"].get("content")
        return value if isinstance(value, str) else None
    except (KeyError, TypeError, IndexError, AttributeError):
        return None


def _finish_reason(row: dict) -> str | None:
    try:
        value = row["raw_response"]["choices"][0].get("finish_reason")
        return value if isinstance(value, str) else None
    except (KeyError, TypeError, IndexError, AttributeError):
        return None


def _truncated(row: dict) -> bool:
    return _finish_reason(row) in {"length", "max_tokens", "content_filter", "error"}


def _discovery_ok(row: dict) -> bool:
    if _truncated(row):
        return False
    valid = sum(v is not None for v in kbf.extract_nums(_raw_text(row), 10, (-1e9, 1e9)))
    try:
        reasoning = int(row["raw_response"].get("usage", {}).get("completion_tokens_details", {}).get("reasoning_tokens", 0))
    except (ValueError, TypeError, KeyError, AttributeError):
        reasoning = 0
    return valid >= 5 and (row["strategy"] == "bare" or reasoning == 0)


def _call(client: httpx.Client, config: dict, key: str, body: dict, secrets: tuple[str, ...],
          cancelled: Callable | None = None) -> dict:
    started = time.monotonic()
    status, raw, error = None, None, None
    try:
        with client.stream("POST", config["candidate"]["api_base"], headers={"Authorization": f"Bearer {key}"},
                           json=body) as response:
            status = response.status_code
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                # httpx read timeouts are idle timeouts. Check the whole request
                # deadline between chunks too, so a slow drip cannot run forever.
                # A read already in progress remains bounded by the read timeout.
                if cancelled and cancelled():
                    error = "request_cancelled"
                    break
                if time.monotonic() - started > config["timeout"]:
                    error = "request_timeout"
                    break
                size += len(chunk)
                if size > MAX_BODY:
                    raise ValueError("response_size_limit")
                chunks.append(chunk)
            if error:
                return {"http_status": status, "raw_response": None, "error": error,
                        "elapsed_seconds": round(time.monotonic() - started, 6)}
            content = b"".join(chunks).decode("utf-8", errors="replace")
            try:
                raw = json.loads(content, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            except ValueError:
                raw = {"unparsed_body": content}
                error = "invalid_json_response"
            if status != 200:
                error = f"http_{status}"
    except httpx.TimeoutException:
        error = "request_timeout"
    except httpx.HTTPError:
        error = "network_error"
    except (ValueError, RecursionError):
        error = "response_size_limit"
    return _clean({"http_status": status, "raw_response": raw, "error": error,
                   "elapsed_seconds": round(time.monotonic() - started, 6)}, secrets)


def run_fingerprint(payload: dict, out: Path, progress: Callable | None = None,
                    cancelled: Callable | None = None, *, library_dir: Path | None = None) -> dict:
    config, direct_key, env = _config(payload, library_dir)
    key = direct_key or os.environ.get(env or "")
    if not isinstance(key, str) or not 8 <= len(key) <= 4096 or re.search(r"\s", key):
        raise ValueError("API key missing or invalid; supply a key or configured environment variable")
    secrets = (key, quote(key, safe=""))
    config = _clean(config, secrets)
    meta, data = _load_pack(config["reference_pack"], library_dir)
    batches, _ = _selection(data, config["profile"])
    output = Path(out)
    output.mkdir(parents=True, exist_ok=False)
    path = output / "fingerprint_records.jsonl"
    records: list[dict] = []
    manifest = _manifest(config, meta)
    _append(path, records, manifest)
    _write_json(output / "sampling_plan.json", plan_fingerprint(payload, library_dir=library_dir))
    state, reason, strategy, stop_code = "complete", None, None, None
    def stopped() -> bool:
        return bool(cancelled and cancelled())
    def announce(completed: int, phase: str) -> None:
        if progress:
            progress({"completed": completed, "total": len(batches), "phase": phase,
                      "requests": sum(r["type"] == "request" for r in records),
                      "profile": config["profile"], "budget": config["budget"],
                      "cost": _cost([r for r in records if r["type"] == "request"]),
                      "message": "正在匹配接口参数" if phase == "discovery" else f"已保存 {completed}/{len(batches)} 批证据"})
    def request(stage: str, strategy: str, batch: dict | None, attempt: int) -> dict:
        prompt = batch["prompt"] if batch else DISCOVERY_PROMPT
        body = _request(config["candidate"]["model"], prompt, batch["max_tokens"] if batch else 800,
                        strategy, _provider(config["candidate"], meta))
        used = _cost([r for r in records if r["type"] == "request"])
        if used["request_count"] >= config["budget"]["max_requests"]:
            raise _BudgetStop("budget_max_requests")
        if used["requested_output_tokens"] + body["max_tokens"] > config["budget"]["max_output_tokens"]:
            raise _BudgetStop("budget_max_output_tokens")
        row = {"type": "request", "run_id": manifest["run_id"], "stage": stage,
               "batch_id": batch["id"] if batch else None, "attempt": attempt, "strategy": strategy,
               "request": body, **_call(client, config, key, body, secrets, cancelled)}
        if path.stat().st_size + len(json.dumps(row, ensure_ascii=False).encode("utf-8")) > MAX_EVIDENCE:
            row.update(raw_response=None, error="evidence_size_limit", http_status=None)
        return _append(path, records, _clean(row, secrets))
    completed = 0
    with httpx.Client(timeout=config["timeout"], follow_redirects=False, trust_env=False) as client:
        announce(0, "discovery")
        for index, trial in enumerate(kbf.THINKING_STRATEGIES):
            if stopped():
                state, reason = "cancelled", "用户停止了采集。"
                break
            try:
                # A repeated parameter trial must leave room for at least one
                # original probe batch if it succeeds. Never shrink the request.
                if index:
                    used = _cost([r for r in records if r["type"] == "request"])
                    if used["request_count"] + 2 > config["budget"]["max_requests"]:
                        raise _BudgetStop("budget_max_requests")
                    if used["requested_output_tokens"] + 800 + batches[0]["max_tokens"] > config["budget"]["max_output_tokens"]:
                        raise _BudgetStop("budget_max_output_tokens")
                row = request("discovery", trial, None, index)
            except _BudgetStop as exc:
                state, reason, stop_code = "partial", str(exc), exc.code
                break
            if row["error"] == "request_cancelled":
                state, reason = "cancelled", "用户停止了采集。"
                break
            if _discovery_ok(row):
                strategy = trial
                break
            if row["error"] and row["http_status"] not in {400, 422}:
                reason = "接口请求失败；错误响应已保存。"
                break
        if strategy is None and state != "cancelled":
            state, reason = "partial", reason or "接口未能在固定输出预算内返回可解析答案。"
        if strategy:
            for batch in batches:
                if stopped():
                    state, reason = "cancelled", "用户停止了采集。"
                    break
                for attempt in range(_attempts(config["profile"])):
                    if stopped():
                        state, reason = "cancelled", "用户停止了采集。"
                        break
                    try:
                        row = request("batch", strategy, batch, attempt)
                    except _BudgetStop as exc:
                        state, reason, stop_code = "partial", str(exc), exc.code
                        break
                    if row["error"] == "request_cancelled":
                        state, reason = "cancelled", "用户停止了采集。"
                        break
                    if row["error"] or _truncated(row) or _raw_text(row) is None:
                        state, reason = "partial", "请求失败、输出截断或响应格式异常；保留已完成证据。"
                        break
                    values = kbf.extract_nums(_raw_text(row), len(batch["indices"]), kbf.DOMAIN_RANGES[batch["domain"]])
                    okay, _ = kbf.validate_batch(values, batch["items"], [kbf.probe_consensus(p) for _, p in batch["items"]])
                    if okay:
                        break
                if state != "complete":
                    break
                completed += 1
                announce(completed, "collecting")
    _append(path, records, {"type": "finish", "run_id": manifest["run_id"], "state": state, "reason": reason,
                           "stop_code": stop_code,
                           "cost": _cost([r for r in records if r["type"] == "request"])})
    report = _evaluate(meta, data, records)
    _write_report(output, report)
    announce(completed, "complete" if state == "complete" else state)
    return report


def _validate_records(pack_id: str, records: list[dict], library_dir: Path | None = None) -> list[dict]:
    meta, data = _load_pack(pack_id, library_dir)
    if not isinstance(records, list) or not records or len(records) > 2000:
        raise ValueError("Fingerprint evidence must contain a bounded manifest and request sequence")
    manifest = records[0]
    if not isinstance(manifest, dict) or manifest.get("type") != "manifest" or manifest.get("schema") != SCHEMA:
        raise ValueError("Fingerprint evidence manifest is missing or unsupported")
    if manifest.get("reference_pack") != pack_id or manifest.get("reference_sha256") != meta["sha256"]:
        raise ValueError("Fingerprint evidence belongs to a different reference pack")
    if type(manifest.get("synthetic")) is not bool:
        raise ValueError("Fingerprint evidence must state synthetic provenance")
    metadata = {"profile", "budget", "selection"} & set(manifest)
    if metadata and metadata != {"profile", "budget", "selection"}:
        raise ValueError("Fingerprint collection profile metadata is incomplete")
    config, _, _ = _config({"reference_pack": pack_id, "candidate": manifest.get("candidate"),
                           "timeout": manifest.get("settings", {}).get("timeout"),
                           "profile": manifest.get("profile", "full"),
                           "budget": manifest.get("budget", {})}, library_dir)
    if metadata and (manifest["selection"] != config["selection"] or manifest["budget"] != config["budget"]):
        raise ValueError("Fingerprint probe selection or budget changed")
    if manifest.get("settings") != _settings(config):
        raise ValueError("Fingerprint settings do not match the pinned protocol")
    batches, _ = _selection(data, config["profile"])
    run = manifest.get("run_id")
    if not isinstance(run, str) or not re.fullmatch(r"[0-9a-f]{32}", run):
        raise ValueError("Invalid evidence run identifier")
    previous = None
    discovery_index, expected_batch, expected_attempt = 0, 0, 0
    selected = "bare" if manifest["synthetic"] else None
    finished, terminal, prior_values = False, False, None
    checked_requests = []
    for index, row in enumerate(records):
        if not isinstance(row, dict) or row.get("sequence") != index or row.get("previous_hash") != previous:
            raise ValueError("Evidence sequence or hash chain is inconsistent")
        if row.get("record_hash") != content_hash({k: v for k, v in row.items() if k != "record_hash"}):
            raise ValueError("Evidence content hash does not match")
        previous = row["record_hash"]
        if index == 0:
            continue
        if row.get("run_id") != run or finished:
            raise ValueError("Evidence mixes runs or contains records after completion")
        if row.get("type") == "finish":
            if row.get("state") not in {"complete", "partial", "cancelled"}:
                raise ValueError("Invalid collection outcome")
            if metadata:
                if row.get("cost") != _cost(checked_requests, manifest["synthetic"]):
                    raise ValueError("Saved cost differs from the request ledger")
                if row.get("stop_code") not in {None, "budget_max_requests", "budget_max_output_tokens"}:
                    raise ValueError("Unknown budget stop code")
                if row.get("stop_code") and row.get("state") != "partial":
                    raise ValueError("Budget-limited collection must be partial")
            finished = True
            continue
        if row.get("type") != "request" or terminal:
            raise ValueError("Unexpected evidence record")
        if row.get("stage") == "discovery":
            if selected or discovery_index >= len(kbf.THINKING_STRATEGIES):
                raise ValueError("Unexpected discovery request")
            strategy = kbf.THINKING_STRATEGIES[discovery_index]
            if row.get("strategy") != strategy or row.get("attempt") != discovery_index or row.get("batch_id") is not None:
                raise ValueError("Discovery order changed")
            expected = _request(config["candidate"]["model"], DISCOVERY_PROMPT, 800, strategy, _provider(config["candidate"], meta))
            discovery_index += 1
            if _discovery_ok(row):
                selected = strategy
            elif row.get("error") and row.get("http_status") not in {400, 422}:
                terminal = True
        elif row.get("stage") == "batch":
            if selected is None or expected_batch >= len(batches):
                raise ValueError("Batch started without valid parameter discovery")
            batch = batches[expected_batch]
            if row.get("batch_id") != batch["id"] or row.get("attempt") != expected_attempt or row.get("strategy") != selected:
                raise ValueError("Batch order, retry count or generation strategy changed")
            expected = _request(config["candidate"]["model"], batch["prompt"], batch["max_tokens"], selected,
                                _provider(config["candidate"], meta))
            prior_values = kbf.extract_nums(_raw_text(row), len(batch["indices"]), kbf.DOMAIN_RANGES[batch["domain"]])
            okay, _ = kbf.validate_batch(prior_values, batch["items"], [kbf.probe_consensus(p) for _, p in batch["items"]])
            if row.get("error") or _truncated(row) or _raw_text(row) is None:
                terminal = True
            elif okay or expected_attempt == _attempts(config["profile"]) - 1:
                expected_batch += 1
                expected_attempt = 0
            else:
                expected_attempt += 1
        else:
            raise ValueError("Unknown evidence request stage")
        if row.get("request") != expected:
            raise ValueError("Saved request differs from the reference probe or generation settings")
        checked_requests.append(row)
        cost = _cost(checked_requests, manifest["synthetic"])
        if cost["request_count"] > config["budget"]["max_requests"] or cost["requested_output_tokens"] > config["budget"]["max_output_tokens"]:
            raise ValueError("Evidence exceeds its declared request or output budget")
        if not isinstance(row.get("elapsed_seconds"), (float, int)) or not math.isfinite(row["elapsed_seconds"]) or row["elapsed_seconds"] < 0:
            raise ValueError("Invalid request duration")
        if row.get("http_status") is not None and (type(row["http_status"]) is not int or not 100 <= row["http_status"] <= 599):
            raise ValueError("Invalid HTTP status")
    return records


def parse_fingerprint_records(text: str, pack_id: str, *, library_dir: Path | None = None) -> list[dict]:
    if not isinstance(text, str) or len(text.encode("utf-8")) > 20 * 1024 * 1024:
        raise ValueError("Fingerprint evidence exceeds the 20 MiB import limit")
    try:
        rows = [json.loads(line, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                for line in text.splitlines() if line.strip()]
        return _validate_records(pack_id, rows, library_dir)
    except (KeyError, TypeError, IndexError, AttributeError, ValueError):
        raise ValueError("Invalid fingerprint evidence: pack, hash chain, request order or original response is inconsistent") from None


def _evaluate(meta: dict, data: dict, records: list[dict]) -> dict:
    manifest = records[0]
    profile = manifest.get("profile", "full")
    batches, selection = _selection(data, profile)
    values = [None] * len(data["probes"])
    selected_batches, selected_rows = {}, {}
    failures = []
    requests = [r for r in records if r["type"] == "request"]
    for batch in batches:
        attempts = [r for r in requests if r["stage"] == "batch" and r["batch_id"] == batch["id"]]
        best, best_count, best_row = None, -1, None
        for row in attempts:
            if row.get("error") or _truncated(row) or _raw_text(row) is None:
                failures.append(batch["id"])
                continue
            parsed = kbf.extract_nums(_raw_text(row), len(batch["indices"]), kbf.DOMAIN_RANGES[batch["domain"]])
            valid = sum(v is not None for v in parsed)
            if valid > best_count:
                best, best_count, best_row = parsed, valid, row
            okay, _ = kbf.validate_batch(parsed, batch["items"], [kbf.probe_consensus(p) for _, p in batch["items"]])
            if okay:
                best, best_row = parsed, row
                break
        if best is not None:
            selected_batches[batch["id"]] = best
            selected_rows[batch["id"]] = best_row
            for i, value in zip(batch["indices"], best):
                values[i] = value
    probes, domains = [], {}
    batch_ids = {i: b["id"] for b in batches for i in b["indices"]}
    for i, (probe, value) in enumerate(zip(data["probes"], values)):
        if i not in batch_ids:
            continue
        reference = kbf.probe_consensus(probe)
        match = None if reference is None or value is None else kbf.check_match(reference, value, probe["domain"])
        source_row = selected_rows.get(batch_ids[i])
        raw_text = _raw_text(source_row) if source_row else None
        probes.append({"index": i, "prompt_id": f"kbf-{i:04d}", "domain": probe["domain"], "name": probe["name"],
                       "prompt": kbf.DOMAIN_CLOZE[probe["domain"]].format(name=probe["name"]),
                       "reference_value": reference, "candidate_value": value,
                       "reference_answer": reference, "candidate_answer": value,
                       "raw_response": raw_text[:500] if raw_text else None,
                       "raw_response_excerpt": bool(raw_text and len(raw_text) > 500),
                       "selected_request_sequence": source_row["sequence"] if source_row else None,
                       "status": "success" if value is not None else "unparsed" if source_row else "missing",
                       "match": match, "batch_id": batch_ids[i]})
        domain = domains.setdefault(probe["domain"], {"domain": probe["domain"], "total": 0, "answered": 0, "matches": 0, "mismatches": 0})
        domain["total"] += 1
        domain["answered"] += int(match is not None)
        domain["matches"] += int(match is True)
        domain["mismatches"] += int(match is False)
    for domain in domains.values():
        domain["coverage"] = domain["answered"] / domain["total"]
    answered = sum(p["match"] is not None for p in probes)
    matches = sum(p["match"] is True for p in probes)
    scored = compute_verdict({"total": answered, "correct": matches, "hamming": answered - matches}, data) if profile == "full" else None
    finish = records[-1] if records[-1]["type"] == "finish" else {"state": "partial", "reason": "采集未写入完成记录。"}
    complete = finish["state"] == "complete" and len(selected_batches) == len(batches) and not failures
    status = {"DIFF": "difference_detected", "SAME": "no_difference_detected"}.get(scored["verdict"] if scored else None, "insufficient_evidence")
    reasons = []
    if not complete:
        reasons.append("incomplete_collection")
    if answered != len(probes):
        reasons.append("missing_answers")
    if profile == "screening":
        reasons.append("screening_not_calibrated")
    if reasons:
        status = "insufficient_evidence"
    explanation = {
        "difference_detected": "当前回答与参考包的差异超过其自测波动。模型版本、运行配置和请求路径均可能影响结果；需要结合服务承诺复核。",
        "no_difference_detected": "在本次探针和阈值下未检出明显差异。这是有限行为一致性证据，不能认证模型权重或上游身份。",
        "insufficient_evidence": "采集未完整完成或存在缺失答案。漏答可能改变比较结果，已保存回答仅作描述，暂不作一致性判断。",
    }[status]
    screening = None
    if profile == "screening":
        outcome = "incomplete" if not complete or answered != len(probes) else "differences_observed" if answered > matches else "no_differences_observed"
        explanation = {"incomplete": "低成本初筛未取得全部计划答案。可检查已保存的差异和失败原因，本次不计算统计判决。",
                       "differences_observed": "初筛中观察到回答差异。这些预先选定的少量题目尚未校准识别能力，可用于决定是否值得进一步核验。",
                       "no_differences_observed": "初筛回答与所选参考题一致。少量题目相符仍不足以判断模型身份，本次不计算统计判决。"}[outcome]
        screening = {"outcome": outcome, "calibrated": False, "selection_fixed_before_collection": True}
    ref_error = meta["self_hamming"] / meta["self_total"]
    report = {"schema_version": "0.5", "kind": "fingerprint", "status": status, "profile": profile,
              "budget": manifest.get("budget", _protocol_limits(batches, profile)),
              "cost": _cost(requests, manifest["synthetic"]),
              "synthetic": manifest["synthetic"], "candidate": manifest["candidate"],
              "created_at": manifest["created_at"], "run_id": manifest["run_id"],
              "collection": {"state": finish["state"] if complete or finish["state"] != "complete" else "partial",
                             "completed": len(selected_batches), "total": len(batches), "requests": len(requests), "reason": finish.get("reason"),
                             "real_requests": 0 if manifest["synthetic"] else len(requests),
                             "cancelled": finish["state"] == "cancelled",
                             "stop_code": finish.get("stop_code"),
                             "failure": finish.get("reason") if not manifest["synthetic"] and finish["state"] == "partial" and not finish.get("stop_code") else None},
              "fingerprint": {"reference": meta, "selection": selection, "screening": screening,
                  "assessment_reasons": reasons,
                  "summary": {"total": len(probes), "answered": answered, "matches": matches,
                              "mismatches": answered - matches, "coverage": answered / len(probes),
                              "missing": len(probes) - answered, "reference_total": len(data["probes"]),
                              "p_value": scored.get("p_value_binomial") if scored and not reasons else None, "error_rate": (answered - matches) / answered if answered else None,
                              "reference_error_rate": ref_error, "p0": scored.get("p0_cp99") if scored else None},
                  "domains": list(domains.values()), "probes": probes,
                  "settings": {**manifest["settings"],
                               "selected_strategy": next((r["strategy"] for r in requests if r["stage"] == "batch"), None),
                               "reference_thinking_strategy": "not_recorded"}, "verdict_explanation": explanation,
                  "upstream_verdict": scored["verdict"] if scored else None, "upstream_statistics": scored,
                  "integrity_note": "参考文件与记录哈希校验通过；哈希用于复算一致性，不认证响应的真实来源。"},
              "artifacts": ["report.json", "report.md", "prompts.csv", "fingerprint_records.jsonl"]}
    if manifest["synthetic"] and manifest.get("demo_variant") in {"match", "different", "incomplete"}:
        report["fingerprint"]["demo_variant"] = manifest["demo_variant"]
    return report


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def markdown_fingerprint_report(report: dict) -> str:
    from .report import _cell as cell
    fp, summary = report["fingerprint"], report["fingerprint"]["summary"]
    ref = fp["reference"]
    mode = "人工演示数据" if report["synthetic"] else "待测端点采集记录"
    profile_label = "低成本初筛" if report.get("profile") == "screening" else "完整参考检查"
    p_value = summary["p_value"] if summary["p_value"] is not None else "不适用（初筛未校准）" if report.get("profile") == "screening" else "不作判断（证据不完整）"
    cost = report.get("cost", {"request_count": report["collection"]["requests"], "requested_output_tokens": "未记录",
                              "usage": {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "reported_requests": "未记录"},
                              "note": "旧版报告未记录预算与 usage 汇总，可用原始记录离线复算补全。"})
    budget = report.get("budget", {"max_requests": "未记录", "max_output_tokens": "未记录"})
    usage = cost["usage"]
    known = lambda field: usage[field] if usage[field] is not None else "未知"
    result_label = STATUS_TEXT[report["status"]]
    if report.get("profile") == "screening":
        result_label = "初筛未完成" if fp["screening"]["outcome"] == "incomplete" else "初筛完成，尚不能判断模型身份"
    lines = ["# 单 API 指纹检查", "", f"**{result_label}** · {mode}", "",
             f"模式：{profile_label}；所选 {summary['total']} 题 / 参考全库 {summary.get('reference_total', summary['total'])} 题。", "",
             fp["verdict_explanation"], "", f"待测模型字段：{cell(report['candidate']['model'])}。",
             f"参考：{ref['label']}；采集日期 {ref['generated_at'][:10]}；参考渠道字段 {ref['provider']}。",
             f"参考端点：{ref['source_endpoint']}；来源为作者记录，未经本组独立认证。", "",
             "| 指标 | 结果 |", "| --- | --- |",
             f"| 可解析覆盖率 | {summary['answered']}/{summary['total']} ({summary['coverage']:.1%}) |",
             f"| 匹配 / 不匹配 | {summary['matches']} / {summary['mismatches']} |",
             f"| 单侧二项检验 p 值 | {p_value} |",
             f"| 参考自测不匹配率 | {summary['reference_error_rate']:.2%} |",
             f"| {'模拟请求记录数' if report['synthetic'] else '实际 HTTP 请求数'} | {report['collection']['requests']} / 配额 {budget['max_requests']} |",
             f"| 已发送请求输出上限之和 | {cost['requested_output_tokens']} / 配额 {budget['max_output_tokens']} |",
             f"| 供应商已报告输入 / 输出 / 总 token 小计 | {known('prompt_tokens')} / {known('completion_tokens')} / {known('total_tokens')} |",
             f"| 含 usage 的响应 | {usage['reported_requests']}/{cost['request_count']} |", "",
             cost["note"], "",
             f"原始参考文件：[KBF]({ref['source_url']})。SHA-256：`{ref['sha256']}`。", "",
             "KBF 的 SAME 表示本检验未拒绝相容性假设；不等于模型身份证明。逐题参考值是模型回答，不是真实知识答案。",
             "复算使用 fingerprint_records.jsonl 中的原始响应，重新运行 KBF 解析和评分。", "",
             "## 各领域结果", "", "| 领域 | 可解析 | 匹配 | 不匹配 |", "| --- | ---: | ---: | ---: |"]
    lines.extend(f"| {d['domain']} | {d['answered']}/{d['total']} | {d['matches']} | {d['mismatches']} |" for d in fp["domains"])
    study = report.get("study", {})
    if study:
        lines += ["", "## 服务档案与研究问题", "", "以下档案由研究者填写，公开声明与本次观察分别列出。", ""]
        for field, label in (("case_id", "案例编号"), ("service_name", "服务名称"), ("product", "套餐或产品"),
                             ("observed_on", "资料观察日期"), ("source_url", "资料来源"),
                             ("research_question", "研究问题"), ("notes", "研究备注")):
            if study.get(field):
                lines.append(f"- {label}：{cell(study[field])}")
        for item in report.get("claim_matrix", []):
            lines += ["", f"### {cell(item['label'])}", "", f"**声明**：{cell(item['claim'])}", "",
                      f"**本次观察**：{cell(item['observation'])}", "", f"**下一步证据**：{cell(item['next_evidence'])}"]
    return "\n".join(lines) + "\n"


def _write_report(output: Path, report: dict) -> None:
    _write_json(output / "report.json", report)
    (output / "report.md").write_text(markdown_fingerprint_report(report), encoding="utf-8")
    fields = ["index", "domain", "name", "prompt", "reference_value", "candidate_value", "match", "batch_id"]
    with (output / "prompts.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for probe in report["fingerprint"]["probes"]:
            writer.writerow({k: "'" + probe[k] if isinstance(probe[k], str) and probe[k].lstrip().startswith(("=", "+", "-", "@")) else probe[k] for k in fields})


def replay_fingerprint(pack_id: str, records: list[dict] | Path, out: Path, *, library_dir: Path | None = None) -> dict:
    if isinstance(records, (Path, str)):
        records = parse_fingerprint_records(Path(records).read_text(encoding="utf-8"), pack_id, library_dir=library_dir)
    else:
        records = _validate_records(pack_id, records, library_dir)
    output = Path(out)
    output.mkdir(parents=True, exist_ok=False)
    meta, data = _load_pack(pack_id, library_dir)
    report = _evaluate(meta, data, records)
    (output / "fingerprint_records.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False, allow_nan=False) for r in records) + "\n", encoding="utf-8")
    _write_report(output, report)
    return report


def demo_fingerprint(pack_id: str, out: Path, variant: str = "match", *, library_dir: Path | None = None,
                     profile: str = "full", budget: dict | None = None) -> dict:
    if variant not in {"match", "different", "incomplete"}:
        raise ValueError("Unknown fingerprint demo variant")
    meta, data = _load_pack(pack_id, library_dir)
    config, _, _ = _config({"reference_pack": pack_id,
                            "candidate": {"api_base": "https://synthetic.example.invalid/v1/chat/completions", "model": meta["model"]},
                            "timeout": 30, "profile": profile, "budget": budget or {}}, library_dir)
    output = Path(out)
    output.mkdir(parents=True, exist_ok=False)
    records: list[dict] = []
    path = output / "fingerprint_records.jsonl"
    manifest = _manifest(config, meta, synthetic=True)
    manifest["demo_variant"] = variant
    _append(path, records, manifest)
    batches, _ = _selection(data, profile)
    state, reason, stop_code = "complete", None, None
    limit = max(1, len(batches) // 4) if variant == "incomplete" else len(batches)
    for batch in batches[:limit]:
        values = []
        for _, probe in batch["items"]:
            value = kbf.probe_consensus(probe)
            if variant == "different":
                low, high = kbf.DOMAIN_RANGES[probe["domain"]]
                value = low if not kbf.check_match(value, low, probe["domain"]) else high
            values.append(value)
        raw = "\n".join(f"({i + 1}) {v}" for i, v in enumerate(values))
        # Preserve the same retry behavior for deliberately mismatching fixtures.
        okay, _ = kbf.validate_batch(values, batch["items"], [kbf.probe_consensus(p) for _, p in batch["items"]])
        for attempt in range(1 if okay else _attempts(profile)):
            cost = _cost([r for r in records if r["type"] == "request"], True)
            if cost["request_count"] >= config["budget"]["max_requests"]:
                stop_code = "budget_max_requests"
            elif cost["requested_output_tokens"] + batch["max_tokens"] > config["budget"]["max_output_tokens"]:
                stop_code = "budget_max_output_tokens"
            if stop_code:
                state, reason = "partial", str(_BudgetStop(stop_code))
                break
            _append(path, records, {"type": "request", "run_id": manifest["run_id"], "stage": "batch",
                    "batch_id": batch["id"], "attempt": attempt, "strategy": "bare",
                    "request": _request(meta["model"], batch["prompt"], batch["max_tokens"], "bare"),
                    "http_status": 200, "raw_response": {"choices": [{"message": {"content": raw}, "finish_reason": "stop"}]},
                    "elapsed_seconds": 0.0, "error": None})
        if stop_code:
            break
    if variant == "incomplete" and state == "complete":
        state, reason = "partial", "人工构造的缺失样本情境。"
    _append(path, records, {"type": "finish", "run_id": manifest["run_id"],
                           "state": state, "reason": reason, "stop_code": stop_code,
                           "cost": _cost([r for r in records if r["type"] == "request"], True)})
    _validate_records(pack_id, records, library_dir)
    report = _evaluate(meta, data, records)
    report["fingerprint"]["demo_variant"] = variant
    _write_report(output, report)
    return report
