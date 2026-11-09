"""Small, manually budgeted reference captures using the shared short protocol."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
from urllib.parse import quote, urlsplit
from uuid import uuid4

import httpx

from . import fingerprint as fp
from . import identification as ident
from . import identification_library as library

SCHEMA = "api-mri-reference-capture/1"
CAPTURE_POLICY = "fixed_attempts_v2"
SEMANTIC_INVALID = {"invalid", "refusal", "empty", "normalized_answer_too_long"}
REFERENCE_REPETITIONS = 8
HOLDOUT_REPETITIONS = 4
MAX_REQUESTS = 256
MAX_OUTPUT_TOKENS = 4096
MAX_EVIDENCE_BYTES = 4 * 1024 * 1024
PROFILE = {"cells": 8, "reference_per_cell": REFERENCE_REPETITIONS,
           "holdout_per_cell": HOLDOUT_REPETITIONS, "max_tokens": ident.TOKEN_CAP}


def _text(value, field, limit=200):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError(f"{field}格式无效。")
    return value.strip()


def _config(payload):
    if not isinstance(payload, dict) or set(payload) - {"candidate", "source", "timeout", "budget"}:
        raise ValueError("参考采集配置包含不支持的字段。")
    settings, direct, env = ident._config({"candidate": payload.get("candidate"), "timeout": payload.get("timeout", 30)})
    if len(settings["candidate"]["api_base"]) > 2048:
        raise ValueError("API地址过长。")
    source = payload.get("source")
    if not isinstance(source, dict) or set(source) - {"kind", "label", "provider", "model_alias", "url"}:
        raise ValueError("请填写参考服务的来源声明。")
    if source.get("kind") not in {"operator_declared", "first_party_route_claim"}:
        raise ValueError("来源请选择个人服务或原厂通道声明。")
    source_url = source.get("url")
    source = {"kind": source["kind"], "label": _text(source.get("label"), "型号名称"),
              "provider": _text(source.get("provider"), "服务名称"),
              "model_alias": _text(source.get("model_alias"), "模型别名")}
    if source_url is not None:
        source_url = _text(source_url, "来源网址", 1000)
        parsed = urlsplit(source_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("来源网址应为不含凭据和查询参数的HTTPS链接。")
        source["url"] = source_url
    if len("service/" + quote(source["provider"], safe="") + "/" + quote(source["model_alias"], safe="")) > 200:
        raise ValueError("服务名称与模型别名编码后过长，请缩短后采集。")
    supplied = payload.get("budget", {})
    if not isinstance(supplied, dict) or set(supplied) - {"max_requests", "max_output_tokens"}:
        raise ValueError("预算只接受请求数与输出配额。")
    budget = {"max_requests": 96, "max_output_tokens": 1536, **supplied}
    if (type(budget["max_requests"]) is not int or not 1 <= budget["max_requests"] <= MAX_REQUESTS
            or type(budget["max_output_tokens"]) is not int or not 1 <= budget["max_output_tokens"] <= MAX_OUTPUT_TOKENS):
        raise ValueError("每轮最多256次请求、4096输出配额。")
    return {"candidate": settings["candidate"], "source": source, "timeout": settings["timeout"], "budget": budget}, direct, env


def _seed_records(seed_records, config):
    if seed_records is None:
        return None, []
    text = seed_records if isinstance(seed_records, str) else "".join(
        json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in seed_records)
    seed = parse_reference_records(text, _allow_resume=False)
    manifest = seed[0]
    previous = manifest["config"]
    requests = [r for r in seed if r["type"] == "request"]
    if previous["source"] != config["source"] or previous["candidate"]["model"] != config["candidate"]["model"]:
        raise ValueError("续采的模型和来源声明必须与原采集一致。")
    before, after = urlsplit(previous["candidate"]["api_base"]), urlsplit(config["candidate"]["api_base"])
    if before != after:
        dsh = (re.fullmatch(r"dsh:[^:]+:[^:]+", config["candidate"]["model"])
               and requests and all(r["response_headers"].get("x-api-mri-transport") == "dsh-adapter" for r in requests)
               and before.scheme == after.scheme == "http" and before.hostname == after.hostname
               and before.hostname in {"localhost", "127.0.0.1", "::1"} and before.path == after.path)
        if not dsh:
            raise ValueError("续采接口地址必须与原采集一致。")
    if not 0 < len(requests) < 96:
        raise ValueError("只有已尝试1至95条的采集可以续采。")
    cells = {c["id"]: c for c in library.core_cells()}
    if any(_sample(r, cells[r["cell_id"]])[1] not in {"valid"} | SEMANTIC_INVALID for r in requests):
        raise ValueError("通道、推理或缓存失败的采集不能直接续采。")
    return text, requests


def plan_reference_capture(payload, *, seed_records=None):
    config, _, _ = _config(payload)
    _, reused = _seed_records(seed_records, config)
    cells = library.core_cells()
    full = len(cells) * (REFERENCE_REPETITIONS + HOLDOUT_REPETITIONS)
    maximum = min(full - len(reused), config["budget"]["max_requests"], config["budget"]["max_output_tokens"] // ident.TOKEN_CAP)
    return {"mode": "reference_collect", "profile": PROFILE, "candidate": config["candidate"],
        "source": config["source"], "budget": config["budget"], "maximum_requests": maximum,
        "maximum_output_tokens": maximum * ident.TOKEN_CAP, "complete_capture_requests": full,
        "reused_requests": len(reused), "capture_attempts": full,
        "cells": [{"id": c["id"], "user": c["user"], "language": c["lang"]} for c in cells],
        "input_utf8_bytes": sum(len((cells[n // 12]["system"] + cells[n // 12]["user"]).encode("utf8"))
                                for n in range(len(reused), len(reused) + maximum)),
        "protocol": {"temperature": 1, "max_tokens": ident.TOKEN_CAP, "reasoning": {"enabled": False}},
        "capture_policy": CAPTURE_POLICY,
        "notes": ["每题固定尝试8条参考、4条独立留出；无效回答保留，留出不进入参考。",
                  "语义无效继续下一次尝试；通道、推理、缓存失败即停止，不重试。",
                  "来源身份由操作者声明，模型回答不认证来源。输入费用另计。",
                  "采集完成后由操作者发布为参考，后台不会自动调用模型。"]}


def _model(config, rows, cells):
    references, holdouts, valid, invalid = {}, {}, 0, Counter()
    for row in rows:
        value, status = _sample(row, cells[row["cell_id"]])
        if status != "valid":
            invalid[status] += 1
            continue
        valid += 1
        target = references if row["split"] == "reference" else holdouts
        target.setdefault(row["cell_id"], Counter())[value] += 1
    times = [r["requested_at"] for r in rows]
    source = config["source"]
    full_holdouts = sum(sum(holdouts.get(c, {}).values()) == HOLDOUT_REPETITIONS for c in cells)
    eligible_cells = sum(sum(references.get(c, {}).values()) >= 5 for c in cells)
    ready = eligible_cells >= 6 and full_holdouts >= 3
    return {"model": "service/" + quote(source["provider"], safe="") + "/" + quote(source["model_alias"], safe=""),
        "label": source["label"], "family": source["provider"], "aliases": list(dict.fromkeys([source["model_alias"], config["candidate"]["model"]])),
        "providers": [source["provider"]], "counts": {c: dict(references.get(c, {})) for c in cells},
        "holdout_counts": {c: dict(holdouts.get(c, {})) for c in cells},
        "collected_at": {"first": min(times) if times else None, "last": max(times) if times else None},
        "provenance": {"kind": source["kind"], "label": source["label"], "provider": source["provider"],
            **({"url": source["url"]} if "url" in source else {}),
            "route": config["candidate"]["model"], "note": "操作者选择的服务来源；动态别名与实际权重版本未独立认证。"},
        "capture_quality": {"valid_answers": valid, "invalid_answers": dict(invalid),
                            "reference_eligible_cells": eligible_cells,
                            "full_holdout_cells": full_holdouts, "can_publish": ready}}


def _cost(rows):
    new = [r for r in rows if not r.get("reused", False)]
    reused = len(rows) - len(new)
    return {**ident._identification_cost(new), "new_requests": len(new), "reused_requests": reused,
            "reused_requested_output_tokens": reused * ident.TOKEN_CAP, "capture_attempts": len(rows)}


def _report(records):
    manifest, finish = records[0], records[-1]
    config = manifest["config"]
    cells = {c["id"]: c for c in library.core_cells()}
    rows = [r for r in records if r["type"] == "request"]
    model = _model(config, rows, cells)
    quality = model.pop("capture_quality")
    quality["can_publish"] = quality["can_publish"] and finish.get("stop_reason") == "complete" and not finish.get("failure")
    completed = sum(sum(model["counts"].get(c, {}).values()) >= 5 for c in cells)
    distances = [{"cell_id": c, "reference_n": sum(model["counts"].get(c, {}).values()),
        "holdout_n": sum(model["holdout_counts"].get(c, {}).values()),
        "distance": ident.jsd(model["counts"][c], model["holdout_counts"][c])}
        for c in cells if model["counts"].get(c) and model["holdout_counts"].get(c)]
    return {"schema": SCHEMA, "kind": "reference_collection", "status": "ready_to_publish" if quality["can_publish"] else "partial",
        "can_resume": bool(0 < len(rows) < 96 and not manifest.get("resume_receipt")
            and all(ident._sample(row, cells[row["cell_id"]])[1] in {"valid"} | SEMANTIC_INVALID for row in rows)),
        "candidate": config["candidate"], "source": config["source"], "profile": PROFILE,
        "capture_policy": manifest.get("capture_policy", "invalid_stop_v1"),
        "collection": {"completed_cells": completed, "failure": finish.get("failure"), "cancelled": finish.get("stop_reason") == "cancelled",
            "stop_code": finish["stop_reason"], "completed_requests": len(rows), "planned_requests": 96},
        "reference": {"model": model["model"], "label": model["label"], "provider": config["source"]["provider"],
            "cells": completed, "reference_answers": sum(sum(v.values()) for v in model["counts"].values()),
            "holdout_answers": sum(sum(v.values()) for v in model["holdout_counts"].values()), **quality},
        "model_reference": model, "cost": _cost(rows),
        "evaluation": {"kind": "same_capture_disjoint_repetitions", "identity_confirmed": False,
            "cells": distances, "note": "逐题参考/留出距离用于检查采样波动；开放集识别门槛未校准。"},
        "records_file": "reference_records.jsonl"}


def _write_report(out, report):
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf8")
    r = report["reference"]
    (out / "report.md").write_text(f"# 参考采集 · {r['label']}\n\n"
        f"达到5条有效参考的单元：{r['cells']}/8；有效参考：{r['reference_answers']}条；有效留出：{r['holdout_answers']}条。\n\n"
        f"无效回答：{json.dumps(r['invalid_answers'], ensure_ascii=False)}。\n\n"
        f"请求：{report['cost']['real_requests']}；申请输出配额：{report['cost']['requested_output_tokens']}。\n\n"
        "来源由操作者声明；留出回答未加入参考计数。\n", encoding="utf8")


def run_reference_capture(payload, out, progress=None, cancelled=None, *, seed_records=None):
    config, direct, env = _config(payload)
    seed_text, reused = _seed_records(seed_records, config)
    key = direct or os.environ.get(env or "")
    if not isinstance(key, str) or not 8 <= len(key) <= 4096 or re.search(r"\s", key):
        raise ValueError("API key missing or invalid")
    secrets = (key, quote(key, safe=""))
    config = fp._clean(config, secrets)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    path, records = out / "reference_records.jsonl", []
    cells = library.core_cells()
    run_id = uuid4().hex
    manifest = {"type": "manifest", "schema": SCHEMA, "run_id": run_id, "capture_policy": CAPTURE_POLICY,
        "resume_source_sha256": hashlib.sha256(seed_text.encode("utf8")).hexdigest() if seed_text else None,
        "resume_source_run_id": json.loads(seed_text.splitlines()[0])["run_id"] if seed_text else None,
        "resume_receipt": seed_text, "reused_requests": len(reused),
        "created_at": datetime.now(timezone.utc).isoformat(), "profile": PROFILE, "config": config,
        "core_sha256": hashlib.sha256(json.dumps(cells, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
    initial = [manifest]
    for old in reused:
        value = {k: v for k, v in old.items() if k not in {"sequence", "previous_hash", "record_hash"}}
        value.update(run_id=run_id, reused=True, original_run_id=old["run_id"], original_record_hash=old["record_hash"])
        initial.append(value)
    initial_bytes, previous = 0, None
    for n, value in enumerate(initial):
        preview = {**value, "sequence": n, "previous_hash": previous}
        preview["record_hash"] = fp.content_hash(preview)
        previous = preview["record_hash"]
        initial_bytes += len((json.dumps(preview, ensure_ascii=False, allow_nan=False) + "\n").encode("utf8"))
    if initial_bytes > MAX_EVIDENCE_BYTES - 65536:
        raise ValueError("续采来源记录过大，不能纳入4MiB证据。")
    for value in initial:
        fp._append(path, records, value)
    fp._write_json(out / "sampling_plan.json", plan_reference_capture(payload, seed_records=seed_text))
    failure, stop = None, "complete"
    with httpx.Client(timeout=config["timeout"], follow_redirects=False, trust_env=False) as client:
        for cell in cells:
            for rep in range(REFERENCE_REPETITIONS + HOLDOUT_REPETITIONS):
                if cells.index(cell) * 12 + rep < len(reused):
                    continue
                count = len(records) - 1
                new_count = count - len(reused)
                if cancelled and cancelled():
                    stop = "cancelled"
                    break
                if new_count >= config["budget"]["max_requests"] or (new_count + 1) * ident.TOKEN_CAP > config["budget"]["max_output_tokens"]:
                    stop = "budget_exhausted"
                    break
                body = ident._body(config, cell)
                value = fp._clean({"type": "request", "run_id": run_id, "reused": False, "cell_id": cell["id"],
                    "replicate": rep, "split": "reference" if rep < REFERENCE_REPETITIONS else "holdout",
                    "requested_at": datetime.now(timezone.utc).isoformat(), "request": body,
                    **fp._call(client, config, key, body, secrets, cancelled)}, secrets)
                # Keep room for the terminal record. A large provider envelope
                # stops this capture after the attempted request; never retry it.
                preview = {**value, "sequence": len(records), "previous_hash": records[-1]["record_hash"]}
                preview["record_hash"] = fp.content_hash(preview)
                if path.stat().st_size + len((json.dumps(preview, ensure_ascii=False, allow_nan=False) + "\n").encode("utf8")) > MAX_EVIDENCE_BYTES - 2048:
                    value.update(raw_response=None, error="evidence_size_limit")
                row = fp._append(path, records, value)
                _, outcome = _sample(row, cell)
                if progress:
                    progress({"phase": "reference_collect", "completed": count + 1, "total": 96,
                        "message": f"已尝试{count + 1}/96条短回答", "cost": _cost([r for r in records if r["type"] == "request"])})
                if outcome not in {"valid"} | SEMANTIC_INVALID:
                    failure, stop = outcome, "request_failed"
                    break
            if stop != "complete":
                break
    fp._append(path, records, {"type": "finish", "run_id": run_id, "stop_reason": stop, "failure": failure})
    report = _report(records)
    _write_report(out, report)
    return report


def parse_reference_records(text, *, _allow_resume=True):
    if not isinstance(text, str) or len(text.encode("utf8")) > MAX_EVIDENCE_BYTES:
        raise ValueError("参考采集记录超过4MiB。")
    try:
        rows = [json.loads(line, object_pairs_hook=_unique_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                for line in text.splitlines() if line.strip()]
        if (not 2 <= len(rows) <= 98 or rows[0]["type"] != "manifest"
                or rows[0]["schema"] != SCHEMA or rows[-1]["type"] != "finish"):
            raise ValueError
        manifest = rows[0]
        policy = manifest.get("capture_policy", "invalid_stop_v1")
        if policy not in {"invalid_stop_v1", CAPTURE_POLICY}:
            raise ValueError
        v2 = policy == CAPTURE_POLICY
        run_id = manifest["run_id"]
        if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id):
            raise ValueError
        timestamp = _timestamp(manifest["created_at"])
        config, _, _ = _config(manifest["config"])
        if config != manifest["config"] or manifest["profile"] != PROFILE:
            raise ValueError
        seed_requests = []
        if v2:
            receipt = manifest["resume_receipt"]
            if receipt is not None:
                if not _allow_resume or not isinstance(receipt, str):
                    raise ValueError
                seed_text, seed_requests = _seed_records(receipt, config)
                seed_manifest = json.loads(seed_text.splitlines()[0])
                if (manifest["resume_source_sha256"] != hashlib.sha256(seed_text.encode("utf8")).hexdigest()
                        or manifest["resume_source_run_id"] != seed_manifest["run_id"]):
                    raise ValueError
            elif manifest["resume_source_sha256"] is not None or manifest["resume_source_run_id"] is not None:
                raise ValueError
            if type(manifest["reused_requests"]) is not int or manifest["reused_requests"] != len(seed_requests):
                raise ValueError
        cells = library.core_cells()
        if manifest["core_sha256"] != hashlib.sha256(json.dumps(cells, ensure_ascii=False, sort_keys=True).encode()).hexdigest():
            raise ValueError
        previous, stopped = None, False
        requests = []
        for i, row in enumerate(rows):
            chain_keys = {"sequence", "previous_hash", "record_hash"}
            expected_keys = ({"type", "schema", "run_id", "created_at", "profile", "config", "core_sha256"} if i == 0 else
                {"type", "run_id", "stop_reason", "failure"} if i == len(rows) - 1 else
                {"type", "run_id", "cell_id", "replicate", "split", "requested_at", "request",
                 "http_status", "raw_response", "error", "response_headers", "elapsed_seconds"})
            if v2 and i == 0:
                expected_keys |= {"capture_policy", "resume_source_sha256", "resume_source_run_id", "resume_receipt", "reused_requests"}
            elif v2 and i != len(rows) - 1:
                expected_keys |= {"reused"}
                if i <= len(seed_requests):
                    expected_keys |= {"original_run_id", "original_record_hash"}
            if set(row) != expected_keys | chain_keys:
                raise ValueError
            if (type(row["sequence"]) is not int or row["sequence"] != i or row["run_id"] != run_id
                    or row["previous_hash"] != previous or row["record_hash"] != fp.content_hash({k: v for k, v in row.items() if k != "record_hash"})):
                raise ValueError
            previous = row["record_hash"]
            if row["type"] == "request":
                n = len(requests)
                cell, rep = cells[n // 12], n % 12
                if (i == 0 or i == len(rows) - 1 or stopped or row["cell_id"] != cell["id"]
                        or type(row["replicate"]) is not int or row["replicate"] != rep
                        or row["split"] != ("reference" if rep < 8 else "holdout")
                        or row["request"] != ident._body(config, cell)):
                    raise ValueError
                is_reused = n < len(seed_requests)
                if v2:
                    if type(row["reused"]) is not bool or row["reused"] != is_reused:
                        raise ValueError
                    if is_reused:
                        old = seed_requests[n]
                        expected = {k: v for k, v in old.items() if k not in chain_keys}
                        expected.update(run_id=run_id, reused=True, original_run_id=old["run_id"], original_record_hash=old["record_hash"])
                        if {k: v for k, v in row.items() if k not in chain_keys} != expected:
                            raise ValueError
                current = _timestamp(row["requested_at"])
                if not is_reused:
                    if current < timestamp:
                        raise ValueError
                    timestamp = current
                if (type(row["elapsed_seconds"]) not in {int, float}
                        or not math.isfinite(row["elapsed_seconds"]) or row["elapsed_seconds"] < 0
                        or row["http_status"] is not None and (type(row["http_status"]) is not int or not 100 <= row["http_status"] <= 599)
                        or row["error"] is not None and (not isinstance(row["error"], str) or not row["error"] or len(row["error"]) > 200)
                        or not isinstance(row["response_headers"], dict)
                        or any(not isinstance(k, str) or not isinstance(v, str) or len(k) > 100 or len(v) > 200
                               for k, v in row["response_headers"].items())
                        or row["raw_response"] is not None and not isinstance(row["raw_response"], dict)):
                    raise ValueError
                _, status = _sample(row, cell)
                stopped = status != "valid" and (not v2 or status not in SEMANTIC_INVALID)
                requests.append(row)
            elif row["type"] != ("manifest" if i == 0 else "finish" if i == len(rows) - 1 else None):
                raise ValueError
        new_count = len(requests) - len(seed_requests)
        if new_count < 0 or new_count > config["budget"]["max_requests"] or new_count * ident.TOKEN_CAP > config["budget"]["max_output_tokens"]:
            raise ValueError
        finish = rows[-1]
        expected_failure = None
        if requests:
            _, status = _sample(requests[-1], cells[(len(requests) - 1) // 12])
            if status != "valid" and (not v2 or status not in SEMANTIC_INVALID):
                expected_failure = status
        if finish.get("failure") != expected_failure:
            raise ValueError
        reason = finish["stop_reason"]
        if reason == "complete" and (len(requests) != 96 or expected_failure is not None):
            raise ValueError
        if reason not in {"complete", "request_failed", "cancelled", "budget_exhausted"}:
            raise ValueError
        if (reason == "request_failed") != (expected_failure is not None):
            raise ValueError
        if reason in {"budget_exhausted", "cancelled"} and len(requests) >= 96:
            raise ValueError
        if reason == "budget_exhausted" and (new_count < config["budget"]["max_requests"]
                and (new_count + 1) * ident.TOKEN_CAP <= config["budget"]["max_output_tokens"]):
            raise ValueError
        return rows
    except (KeyError, TypeError, IndexError, ValueError, AttributeError, RecursionError):
        raise ValueError("参考采集的协议、拆分或记录链不一致。") from None


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError
    return stamp


def _sample(row, cell):
    value, status = ident._sample(row, cell)
    if status == "valid" and (not isinstance(value, str) or not value or len(value) > 200
                              or any(ord(c) < 32 for c in value)):
        return None, "normalized_answer_too_long"
    return value, status


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def publish_capture(records_path, library_dir):
    path = Path(records_path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_EVIDENCE_BYTES:
        raise ValueError("参考采集记录文件无效或超过4MiB。")
    raw = path.read_bytes()
    records = parse_reference_records(raw.decode("utf8"))
    report = _report(records)
    if not report["reference"]["can_publish"]:
        raise ValueError("需完成96次尝试、至少6个单元各有5条有效参考，并有3个单元各有4条有效留出。")
    model = report["model_reference"]
    model["provenance"]["records_sha256"] = hashlib.sha256(raw).hexdigest()
    from .identification_library import active_bank_id, load_bank
    active = active_bank_id(library_dir)
    base = load_bank(active, library_dir) if active else ident.load_identification_bank()
    bank = library.build_rolling_bank([model], bank_id="rolling-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8], base_bank=base)
    return library.publish_bank(bank, library_dir)
