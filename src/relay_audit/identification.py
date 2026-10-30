"""Experimental single-endpoint candidate retrieval with shared short probes.

The reference is derived from Bruckner's CC-BY-4.0 PAMELA corpus.  Distances
are descriptive Jensen-Shannon divergences, never identity probabilities.
KBF reference packs are deliberately NOT merged: their full prompts differ.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import re
import statistics
import unicodedata
from urllib.parse import quote, urlsplit
from uuid import uuid4

import httpx

from . import fingerprint as fp
from .records import content_hash

SCHEMA = "api-mri-identification/1"
BANK_PATH = Path(__file__).parent / "data" / "identification" / "one-token-202607.json"
DEFAULT_BUDGET = {"max_requests": 32, "max_output_tokens": 512}
BLOCK = 4
TOKEN_CAP = 16
MIN_CELLS = 3
STOP_POLICIES = {"fixed", "stable_shortlist"}
COIN = {"en": {"heads": "h", "tails": "t"}, "ru": {"орёл": "h", "орел": "h", "решка": "t"},
        "zh": {"正面": "h", "正": "h", "反面": "t", "反": "t"}, "ar": {"صورة": "h", "كتابة": "t"}}
_PUNCT = re.compile(r'''[«»"“”„'’‘`().,!?。！？、：:;؛؟\[\]{}*_#-]+''')
_REFUSAL = re.compile(r"i can.?t|i cannot|i'm sorry|as an ai|не могу|извин|抱歉|无法|لا أستطيع|عذراً|آسف", re.I)
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_ZH = dict(zip("零一二两三四五六七八九", (0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))
_GAIN_CACHE: dict[tuple, float] = {}


def normalize_answer(raw: str | None, cell: dict) -> tuple[str | None, str]:
    """Independent Python port of published 01-normalize.js text rules (MIT).

    Color canonicalization is omitted: comparisons are within the same language.
    Post-reasoning and transport checks occur before this text-only operation.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None, "empty"
    if _REFUSAL.search(raw):
        return None, "refusal"
    text = re.sub(r"\s+", " ", _PUNCT.sub(" ", unicodedata.normalize("NFC", raw))).strip().translate(_DIGITS)
    if not text:
        return None, "empty"
    kind, lang = cell["normalize_as"], cell["lang"]
    if kind == "integer":
        found = re.search(r"-?[0-9]+", text)
        number = int(found[0]) if found else None
        if number is None and lang == "zh":
            match = re.fullmatch(r"([零一二两三四五六七八九])?十?([零一二两三四五六七八九])?", text)
            if match and (match[1] or match[2] or "十" in text):
                number = ((_ZH.get(match[1], 1) * 10 + _ZH.get(match[2], 0)) if "十" in text
                          else _ZH.get(match[1]))
        if number is None:
            return None, "invalid"
        bounds = re.search(r"([0-9]+)-([0-9]+)", cell["answer_space"])
        okay = bounds is None or int(bounds[1]) <= number <= int(bounds[2])
        return str(number), "valid" if okay else "invalid"
    words = text.lower().split(" ")
    if kind == "binary":
        value = COIN.get(lang, {}).get(words[0])
        return value, "valid" if value else "invalid"
    if kind == "word" and len(words) > 3:
        return None, "invalid"
    if kind == "grapheme" and len(words[0]) > 1 and lang != "zh":
        singles = [word for word in words if len(word) == 1]
        return (singles[0], "valid") if singles else (None, "invalid")
    return words[0], "valid"


def jsd(left: dict, right: dict) -> float:
    """Jensen-Shannon divergence in bits, 0..1; requires nonempty counts."""
    nl, nr = sum(left.values()), sum(right.values())
    if not nl or not nr:
        raise ValueError("JSD requires two nonempty distributions")
    score = 0.0
    for answer in left.keys() | right.keys():
        p, q = left.get(answer, 0) / nl, right.get(answer, 0) / nr
        mean = (p + q) / 2
        if p:
            score += p * math.log2(p / mean) / 2
        if q:
            score += q * math.log2(q / mean) / 2
    return max(0.0, min(1.0, score))


@lru_cache(maxsize=1)
def load_identification_bank() -> dict:
    raw = BANK_PATH.read_bytes()
    if len(raw) > 8_000_000:
        raise ValueError("Identification bank exceeds the size limit")
    bank = json.loads(raw)
    if bank.get("schema") != "api-mri-identification-bank/1" or len(bank.get("models", [])) < 2:
        raise ValueError("Unsupported identification bank")
    return bank


def _config(payload: dict) -> tuple[dict, str | None, str | None]:
    allowed = {"candidate", "candidate_models", "budget", "timeout", "api2d_no_cache", "stop_policy"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("识别配置包含不支持的字段。")
    candidate = payload.get("candidate")
    if not isinstance(candidate, dict) or set(candidate) - {"api_base", "model", "api_key", "api_key_env"}:
        raise ValueError("candidate需要api_base和model。")
    base, model = candidate.get("api_base"), candidate.get("model")
    try:
        if not isinstance(base, str) or not isinstance(model, str) or not model.strip() or len(model) > 200:
            raise ValueError
        parts = urlsplit(base)
        if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.query or parts.fragment or parts.port == 0
                or re.search(r"\s", base) or any(ord(c) < 32 for c in model)):
            raise ValueError
        if parts.scheme == "http" and parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError
    except ValueError:
        raise ValueError("API地址须为HTTPS接口根地址或本机HTTP地址；不含凭据、查询串。") from None
    base = base.rstrip("/")
    if not base.endswith("/chat/completions"):
        base += "/chat/completions"
    timeout = payload.get("timeout", 30)
    if type(timeout) is not int or not 1 <= timeout <= 300:
        raise ValueError("timeout须为1至300秒整数。")
    key, env = candidate.get("api_key"), candidate.get("api_key_env")
    if key is not None and (not isinstance(key, str) or not 8 <= len(key) <= 4096 or re.search(r"\s", key)):
        raise ValueError("API密钥格式无效。")
    if env is not None and (not isinstance(env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", env)):
        raise ValueError("API密钥环境变量名称无效。")
    if key and env:
        raise ValueError("api_key与api_key_env二选一。")
    supplied = payload.get("budget", {})
    if not isinstance(supplied, dict) or set(supplied) - set(DEFAULT_BUDGET):
        raise ValueError("budget只接受max_requests和max_output_tokens。")
    budget = {**DEFAULT_BUDGET, **supplied}
    if (type(budget["max_requests"]) is not int or not 1 <= budget["max_requests"] <= 32
            or type(budget["max_output_tokens"]) is not int or not 1 <= budget["max_output_tokens"] <= 512):
        raise ValueError("实验识别须设1至32次请求、1至512输出配额。")
    ids = [m["model"] for m in load_identification_bank()["models"]]
    chosen = payload.get("candidate_models", ids)
    if (not isinstance(chosen, list) or any(not isinstance(m, str) for m in chosen) or len(chosen) < 2 or len(chosen) > len(ids)
            or len(set(chosen)) != len(chosen) or any(m not in ids for m in chosen)):
        raise ValueError("candidate_models须为库中至少两个不重复的模型标识。")
    bypass = payload.get("api2d_no_cache", False)
    if type(bypass) is not bool:
        raise ValueError("api2d_no_cache须为布尔值。")
    policy = payload.get("stop_policy", "fixed")
    if not isinstance(policy, str) or policy not in STOP_POLICIES:
        raise ValueError("stop_policy须为fixed或stable_shortlist。")
    config = {"candidate": {"api_base": base, "model": model.strip()}, "timeout": timeout, "budget": budget,
              "candidate_models": sorted(chosen), "api2d_no_cache": bypass}
    # Omitted policy is kept omitted so v0.6 hashed manifests still replay exactly.
    if "stop_policy" in payload:
        config["stop_policy"] = policy
    return config, key, env


def _models(bank: dict, model_ids: list[str]) -> list[dict]:
    selected = set(model_ids)
    return [m for m in bank["models"] if m["model"] in selected]


def _common_cells(bank: dict, models: list[dict]) -> list[dict]:
    return [c for c in bank["cells"] if all(sum(m["counts"].get(c["id"], {}).values()) >= 5 for m in models)]


def rank_candidates(bank: dict, models: list[dict], observed: dict[str, Counter]) -> list[dict]:
    cells = sorted(c for c, counts in observed.items() if sum(counts.values()) >= BLOCK)
    if not cells:
        return []
    ranked = []
    for model in models:
        if any(sum(model["counts"].get(c, {}).values()) < 5 for c in cells):
            continue
        distance = statistics.fmean(jsd(observed[c], model["counts"][c]) for c in cells)
        ranked.append({"model": model["model"], "label": model["label"], "family": model["family"],
                       "distance": round(distance, 6), "compared_cells": len(cells)})
    ranked.sort(key=lambda m: (m["distance"], m["model"]))
    return [{**m, "rank": i + 1} for i, m in enumerate(ranked)]


def select_next_cell(bank: dict, models: list[dict], observed: dict[str, Counter]) -> dict | None:
    """Select among intact shared prompts; no nonce or batch reshaping.

    Mean pairwise JSD separates reference candidates.  Selection after each
    four-response block uses the nearest 12 candidates, with a fixed cost proxy.
    """
    available = [c for c in _common_cells(bank, models) if c["id"] not in observed]
    if not available:
        return None
    ranked = rank_candidates(bank, models, observed)
    shortlist = {r["model"] for r in ranked[:12]} if ranked else {m["model"] for m in models}
    active = [m for m in models if m["model"] in shortlist]
    def gain(cell):
        cache_key = (id(bank), tuple(m["model"] for m in active), cell["id"])
        if cache_key in _GAIN_CACHE:
            return _GAIN_CACHE[cache_key]
        pairs = [jsd(a["counts"][cell["id"]], b["counts"][cell["id"]])
                 for i, a in enumerate(active) for b in active[i + 1:]]
        # UTF-8 length is a reproducible planning proxy, not a billed tokenizer.
        cost = len((cell["system"] + cell["user"]).encode("utf-8")) / 4 + TOKEN_CAP
        value = statistics.fmean(pairs) / cost if pairs else 0.0
        if len(_GAIN_CACHE) < 100_000:
            _GAIN_CACHE[cache_key] = value
        return value
    selected = max(available, key=lambda c: (gain(c), c["id"]))
    return selected if gain(selected) > 1e-12 else None


def _bank_summary(bank: dict) -> dict:
    return {k: bank[k] for k in ("id", "source", "collected_at", "built_on", "reference_split", "normalization")}


def _stage(bank: dict, models: list[dict], observed: dict[str, Counter]) -> dict:
    """A descriptive stage.  Fixed-horizon gates are never confidence values."""
    ranking = rank_candidates(bank, models, observed)
    completed = sum(sum(v.values()) >= BLOCK for v in observed.values())
    gate = (bank.get("calibration", {}).get("open_set_gates", {}).get(str(completed))
            if len(models) == len(bank["models"]) else None)
    gap = ranking[1]["distance"] - ranking[0]["distance"] if len(ranking) > 1 else None
    return {"completed_cells": completed, "requests": completed * BLOCK,
            "first": ranking[0]["model"] if ranking else None,
            "top3": [r["model"] for r in ranking[:3]],
            "distance": ranking[0]["distance"] if ranking else None, "gap": gap,
            "fixed_horizon_gate_passed": (bool(ranking and gap is not None
                and ranking[0]["distance"] <= gate["max_distance"] and gap >= gate["min_gap"])
                if gate else None)}


def should_stop_stable(stages: list[dict]) -> bool:
    """Fixed economy policy: two agreeing gates, earliest 24 requests.

    Its operating characteristics must be evaluated as a sequential policy,
    separately from each fixed-budget gate.  This stops a shortlist, not identity.
    """
    return (len(stages) >= 2 and stages[-1]["completed_cells"] >= 6
            and stages[-1]["completed_cells"] == stages[-2]["completed_cells"] + 1
            and stages[-1]["first"] == stages[-2]["first"]
            and all(s.get("fixed_horizon_gate_passed") is True for s in stages[-2:]))


def identification_evidence(bank: dict, models: list[dict], observed: dict[str, Counter],
                            cell_order: list[str] | None = None) -> dict:
    """Explain a ranking using the existing answers; no additional sampling.

    Leaving out a probe holds the collected probe set fixed.  The resulting
    stability is a sensitivity analysis, not another independent accuracy test.
    """
    complete = {c: v for c, v in observed.items() if sum(v.values()) >= BLOCK}
    order = [c for c in (cell_order or list(observed)) if c in complete]
    # A recorded request order can repeat each cell four times.
    order = list(dict.fromkeys(order))
    by_cell, by_model = {c["id"]: c for c in bank["cells"]}, {m["model"]: m for m in models}
    ranking = rank_candidates(bank, models, complete)
    heads = ranking[:3]
    leader = ranking[0]["model"] if ranking else None
    probes, stages, running = [], [], {}
    for cell_id in order:
        cell = by_cell[cell_id]
        running[cell_id] = complete[cell_id]
        stages.append(_stage(bank, models, running))
        distances = [{"model": row["model"], "label": row["label"],
                      "distance": round(jsd(complete[cell_id], by_model[row["model"]]["counts"][cell_id]), 6),
                      "reference_counts": by_model[row["model"]]["counts"][cell_id]}
                     for row in heads]
        probes.append({"cell_id": cell_id, "task_id": cell.get("task_id"), "lang": cell["lang"],
                       "prompt": cell["user"], "observed_counts": dict(complete[cell_id]),
                       "reference_comparison": distances,
                       "leader_margin": round(distances[1]["distance"] - distances[0]["distance"], 6)
                           if len(distances) > 1 else None})
    leave_out = []
    if len(complete) >= 3:
        for omitted in order:
            reduced = rank_candidates(bank, models, {c: v for c, v in complete.items() if c != omitted})
            leave_out.append({"omitted_cell": omitted, "first": reduced[0]["model"] if reduced else None,
                              "top3": [r["model"] for r in reduced[:3]],
                              "same_leader": bool(reduced and reduced[0]["model"] == leader)})
    return {"per_probe": probes, "trajectory": stages,
            "leave_one_probe_out": {"trials": leave_out, "evaluated": len(leave_out),
                "same_leader_count": sum(r["same_leader"] for r in leave_out),
                "same_leader_fraction": (sum(r["same_leader"] for r in leave_out) / len(leave_out)) if leave_out else None,
                "interpretation": "删除一个已采探针后重排；保持采样题序固定，不是身份概率或独立验证。"},
            "provenance": [{"model": row["model"], "providers": by_model[row["model"]].get("providers", []),
                            "provider_labels_source": "author_record"} for row in heads],
            "analysis_api_requests": 0}


def plan_identification(payload: dict) -> dict:
    config, key, _ = _config(payload)
    bank = load_identification_bank()
    models = _models(bank, config["candidate_models"])
    cells = _common_cells(bank, models)
    first = select_next_cell(bank, models, {})
    return fp._clean({"schema": SCHEMA, "kind": "identification", "bank": _bank_summary(bank),
        "candidate_count": len(models), "candidates": [{k: m[k] for k in ("model", "label", "family")} for m in models],
        "shared_cell_count": len(cells), "first_probe": first, "budget": config["budget"],
        "max_completed_cells": min(len(cells), config["budget"]["max_requests"] // BLOCK,
                                    config["budget"]["max_output_tokens"] // (TOKEN_CAP * BLOCK)),
        "protocol": {"temperature": 1, "max_tokens": TOKEN_CAP, "reasoning": {"enabled": False},
                     "samples_per_cell": BLOCK, "retries": 0},
        "selection_method": "shared_prompt_pairwise_jsd_per_utf8_cost_v1",
        "stop_policy": config.get("stop_policy", "fixed"),
        "validation": {"reference_labels": "author_provided", "reference_provider_split": "pooled",
            "fixed_budget_evaluation": "same_collection_disjoint_repetitions",
            "stable_stop_earliest_requests": 24, "live_accuracy_evaluated": False},
        "config": config, "notes": ["候选距离为JSD原值，数值较小表示本轮回答分布更接近；它不是身份概率。",
            "统一参考采于2026年7月；未收录或已更新模型可能靠近某个旧标签。",
            "每4次有效回答完成一个单元；整段回答缓存或隐藏推理会停止采样。",
            "输出配额不包括输入与额外推理费用；报告记录供应商自报用量。"]}, (key,) if key else ())


def _body(config: dict, cell: dict) -> dict:
    return {"model": config["candidate"]["model"], "messages": [{"role": "system", "content": cell["system"]},
             {"role": "user", "content": cell["user"]}], "temperature": 1, "max_tokens": TOKEN_CAP,
            "reasoning": {"enabled": False}, "usage": {"include": True}}


def _sample(row: dict, cell: dict) -> tuple[str | None, str]:
    raw = row.get("raw_response") or {}
    if row.get("error") or fp._truncated(row) or fp._raw_text(row) is None:
        return None, "transport_or_format_error"
    message = raw.get("choices", [{}])[0].get("message", {})
    usage = raw.get("usage")
    if usage is not None and not isinstance(usage, dict):
        return None, "transport_or_format_error"
    details = (usage or {}).get("completion_tokens_details")
    if details is not None and not isinstance(details, dict):
        return None, "transport_or_format_error"
    details = details or {}
    if message.get("reasoning") or message.get("reasoning_content") or details.get("reasoning_tokens", 0):
        return None, "reasoning_returned"
    headers = row.get("response_headers") or {}
    cache = raw.get("cache")
    if cache is True or (isinstance(cache, str) and cache.lower() in {"true", "hit", "1"}) or str(headers.get("x-api2d-cache", "")).lower() in {"true", "hit", "1"}:
        return None, "response_cache_hit"
    return normalize_answer(fp._raw_text(row), cell)


def _identification_cost(requests: list[dict], synthetic: bool = False) -> dict:
    cost = fp._cost(requests, synthetic)
    reported = []
    if not synthetic:
        for row in requests:
            raw = row.get("raw_response")
            usage = raw.get("usage") if isinstance(raw, dict) else None
            details = usage.get("completion_tokens_details") if isinstance(usage, dict) else None
            value = details.get("reasoning_tokens") if isinstance(details, dict) else None
            if type(value) is int and value >= 0:
                reported.append(value)
    cost["usage"].update(reasoning_tokens=sum(reported) if reported else None,
                         reasoning_reported_requests=len(reported),
                         reasoning_missing_requests=len(requests) - len(reported))
    return cost


def _rank_stability(bank: dict, models: list[dict], observed: dict[str, Counter]) -> dict:
    """Measure whether the leading candidate survives leave-one-cell-out checks."""
    cells = [cell for cell, counts in observed.items() if sum(counts.values()) >= BLOCK]
    if len(cells) < 2:
        return {"tested_cells": 0, "top_model": None, "top_model_rate": None, "top_models": []}
    top_models = []
    for excluded in cells:
        reduced = {cell: counts for cell, counts in observed.items() if cell != excluded}
        ranking = rank_candidates(bank, models, reduced)
        if ranking:
            top_models.append(ranking[0]["model"])
    if not top_models:
        return {"tested_cells": 0, "top_model": None, "top_model_rate": None, "top_models": []}
    counts = Counter(top_models)
    model, count = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0]
    return {"tested_cells": len(top_models), "top_model": model,
            "top_model_rate": count / len(top_models), "top_models": top_models}


def _report(bank: dict, config: dict, records: list[dict]) -> dict:
    observed, invalid = {}, Counter()
    by_id = {c["id"]: c for c in bank["cells"]}
    requests = [r for r in records if r["type"] == "request"]
    for row in requests:
        value, status = _sample(row, by_id[row["cell_id"]])
        if status == "valid":
            observed.setdefault(row["cell_id"], Counter())[value] += 1
        else:
            invalid[status] += 1
    ranking = rank_candidates(bank, _models(bank, config["candidate_models"]), observed)
    rank_stability = _rank_stability(bank, _models(bank, config["candidate_models"]), observed)
    completed = sum(sum(c.values()) >= BLOCK for c in observed.values())
    finish = records[-1] if records[-1]["type"] == "finish" else {}
    stop = finish.get("stop_reason", "incomplete_records")
    status = "candidate_shortlist" if completed >= MIN_CELLS and ranking else "insufficient_evidence"
    calibration = bank.get("calibration", {})
    calibrated = len(config["candidate_models"]) == len(bank["models"]) and MIN_CELLS <= completed <= 8
    gate_config = calibration.get("open_set_gates", {}).get(str(completed)) if calibrated else None
    gate, gap, envelope = None, None, None
    if gate_config is not None and len(ranking) >= 2:
        gap = ranking[1]["distance"] - ranking[0]["distance"]
        envelope = ranking[0]["distance"] <= gate_config["max_distance"]
        gate = envelope and gap >= gate_config["min_gap"]
        if not envelope:
            status = "unknown_or_changed"
        elif not gate:
            status = "insufficient_evidence"
    if completed >= MIN_CELLS and gate_config is None:
        status = "insufficient_evidence"
    if invalid or finish.get("state") in {"partial", "cancelled"}:
        status = "insufficient_evidence"
    families = {}
    for row in ranking:
        family = families.setdefault(row["family"], {"family": row["family"], "distance": row["distance"], "models": []})
        family["models"].append(row["model"])
    cost = _identification_cost(requests, records[0].get("synthetic", False))
    evidence = identification_evidence(bank, _models(bank, config["candidate_models"]), observed,
                                       [r["cell_id"] for r in requests])
    return {"schema": SCHEMA, "kind": "identification", "synthetic": records[0].get("synthetic", False),
        "status": status, "identity_decision": "abstained", "probability": None,
        "bank": _bank_summary(bank), "candidate": config["candidate"], "budget": config["budget"],
        "ranking": ranking[:20], "family_ranking": sorted(families.values(), key=lambda f: (f["distance"], f["family"]))[:10],
        "coverage": {"completed_cells": completed, "valid_answers": sum(map(lambda c: sum(c.values()), observed.values())),
                     "compared_candidates": len(ranking), "candidate_count": len(config["candidate_models"]),
                     "invalid_answers": dict(invalid), "partial_cells": [c for c, v in observed.items() if sum(v.values()) < BLOCK],
                     "cells": {c: dict(v) for c, v in observed.items()}},
        "calibration": {**calibration, "applicable_gate": gate_config, "ranking_gap": gap,
                        "inside_distance_envelope": envelope, "open_set_gate_passed": gate, "scope": "same_collection_offline_only",
                        "rank_stability": rank_stability,
                        "interpretation": "距离包络来自同轮公开采集的独立重复；现网准确率尚未测量。"},
        "cost": cost, "stop_reason": stop, "records_file": "identification_records.jsonl",
        "evidence": evidence,
        "validation": {"reference_labels": "author_provided", "reference_provider_split": "pooled",
            "fixed_budget_evaluation": "same_collection_disjoint_repetitions",
            "stop_policy_evaluation": ("exploratory_historical_holdout_reanalysis"
                                      if config.get("stop_policy") == "stable_shortlist" else "fixed_horizon"),
            "live_accuracy_evaluated": False,
            "source_stratum_audit": "docs/identification-stress.json"},
        "stopping": {"policy": config.get("stop_policy", "fixed"),
            "early_stopped": stop == "stable_shortlist",
            "sequential_experimental": config.get("stop_policy", "fixed") == "stable_shortlist",
            "fixed_horizon_metrics_apply_to_policy": (config.get("stop_policy", "fixed") == "fixed"
                and len(config["candidate_models"]) == len(bank["models"]) and MIN_CELLS <= completed <= 8),
            "unused_request_budget": max(0, config["budget"]["max_requests"] - len(requests)),
            "unused_output_token_cap": max(0, config["budget"]["max_output_tokens"] - len(requests) * TOKEN_CAP)},
        "collection": {"state": finish.get("state", "partial"), "failure": finish.get("failure")},
        "notes": ["候选排名预测与库中哪些服务标签更接近；当前版本保留身份拒判，距离不换算为概率。",
                  "排名稳定性采用逐个完整单元留出复算；单元数不足时保持未知，不能替代独立验证集。",
                  "家族按作者family_guess合并，展示各家族最接近的标签距离；不代表模型谱系证明。",
                  "未收录、更新、动态路由或服务配置变化都可能导致库外结果。",
                  "已完成回答同时比较全部候选；复算不再次发送模型请求。"]}


def markdown_identification_report(report: dict) -> str:
    labels = {"candidate_shortlist": "已得到库内候选", "unknown_or_changed": "库外模型或服务行为变化", "insufficient_evidence": "采样证据不足"}
    lines = ["# API-MRI · 模型候选识别", "", labels[report["status"]], "",
        f"已完成{report['coverage']['completed_cells']}个单元，比较{report['coverage']['compared_candidates']}个候选；停止原因：{report['stop_reason']}。", "",
        "| 候选 | 家族 | JSD距离（越小越接近） |", "| --- | --- | ---: |"]
    lines += [f"| {r['label']} | {r['family']} | {r['distance']:.6f} |" for r in report["ranking"][:10]]
    lines += ["", *report["notes"], "", f"参考采集：{report['bank']['collected_at']}；[原始公开数据]({report['bank']['source']['data']})。", "",
        f"发送请求：{report['cost']['real_requests']}；累计申请输出配额：{report['cost']['requested_output_tokens']}；实际输入/输出见report.json中的供应商用量。", ""]
    evidence = report.get("evidence", {})
    stability = evidence.get("leave_one_probe_out", {})
    if stability.get("evaluated"):
        lines += ["", f"逐一删除一个已采探针后，首位保持{stability['same_leader_count']}/{stability['evaluated']}次。这是本次排名对探针的敏感性分析。", ""]
    if evidence.get("per_probe"):
        lines += ["| 探针 | 语言 | 首位对第二位的距离优势 |", "| --- | --- | ---: |"]
        lines += [f"| {r['task_id']} | {r['lang']} | {r['leader_margin']:.6f} |"
                  for r in evidence["per_probe"] if r["leader_margin"] is not None]
        lines += [""]
    if report.get("stopping", {}).get("early_stopped"):
        lines += [f"连续两阶段保持同一候选并通过对应门槛，已提前结束；未使用{report['stopping']['unused_request_budget']}次请求预算。顺序停止性能见单独验证。", ""]
    return "\n".join(lines)


def _write_report(out: Path, report: dict):
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (out / "report.md").write_text(markdown_identification_report(report), encoding="utf-8")


def run_identification(payload: dict, out: Path, progress=None, cancelled=None) -> dict:
    config, direct, env = _config(payload)
    key = direct or os.environ.get(env or "")
    if not isinstance(key, str) or not 8 <= len(key) <= 4096 or re.search(r"\s", key):
        raise ValueError("API key missing or invalid")
    secrets = (key, quote(key, safe=""))
    config = fp._clean(config, secrets)
    bank = load_identification_bank()
    models = _models(bank, config["candidate_models"])
    output = Path(out)
    output.mkdir(parents=True, exist_ok=False)
    path = output / "identification_records.jsonl"
    records = []
    fp._append(path, records, {"type": "manifest", "schema": SCHEMA, "run_id": uuid4().hex,
        "bank_sha256": hashlib.sha256(BANK_PATH.read_bytes()).hexdigest(), "config": config,
        "synthetic": False, "created_at": datetime.now(timezone.utc).isoformat()})
    fp._write_json(output / "sampling_plan.json", plan_identification(payload))
    observed, stop, failure, stages = {}, "budget_exhausted", None, []
    with httpx.Client(timeout=config["timeout"], follow_redirects=False, trust_env=False) as client:
        while True:
            cell = select_next_cell(bank, models, observed)
            if cell is None:
                stop = "no_discriminating_shared_probe"
                break
            used = fp._cost([r for r in records if r["type"] == "request"])
            if (used["request_count"] + BLOCK > config["budget"]["max_requests"]
                    or used["requested_output_tokens"] + TOKEN_CAP * BLOCK > config["budget"]["max_output_tokens"]):
                stop = "budget_exhausted"
                break
            counts = Counter()
            for _ in range(BLOCK):
                if cancelled and cancelled():
                    stop = "cancelled"
                    break
                used = fp._cost([r for r in records if r["type"] == "request"])
                if (used["request_count"] >= config["budget"]["max_requests"]
                        or used["requested_output_tokens"] + TOKEN_CAP > config["budget"]["max_output_tokens"]):
                    stop = "budget_exhausted"
                    break
                body = _body(config, cell)
                transport_config = {**config, "_headers": {"x-api2d-no-cache": "1"} if config["api2d_no_cache"] else {}}
                row = fp._append(path, records, fp._clean({"type": "request", "cell_id": cell["id"],
                    "request": body, "request_headers": transport_config["_headers"],
                    **fp._call(client, transport_config, key, body, secrets, cancelled)}, secrets))
                value, status = _sample(row, cell)
                if status != "valid":
                    stop, failure = status, status
                    break
                counts[value] += 1
                if progress:
                    progress({"phase": "identifying", "completed": used["request_count"] + 1,
                        "total": config["budget"]["max_requests"], "message": f"已采集{used['request_count'] + 1}次短回答",
                        "cost": _identification_cost([r for r in records if r["type"] == "request"])})
            if counts:
                observed[cell["id"]] = counts
            if len(counts) == 0 or sum(counts.values()) < BLOCK or failure or stop == "cancelled":
                break
            stages.append(_stage(bank, models, observed))
            if config.get("stop_policy") == "stable_shortlist" and should_stop_stable(stages):
                stop = "stable_shortlist"
                break
    fp._append(path, records, {"type": "finish", "state": "cancelled" if stop == "cancelled" else "partial" if failure else "complete",
                              "stop_reason": stop, "failure": failure})
    report = _report(bank, config, records)
    _write_report(output, report)
    return report


def parse_identification_records(text: str) -> list[dict]:
    if not isinstance(text, str) or len(text.encode("utf-8")) > 4 * 1024 * 1024:
        raise ValueError("Identification evidence exceeds 4 MiB")
    try:
        records = [json.loads(line, parse_constant=lambda _: (_ for _ in ()).throw(ValueError())) for line in text.splitlines() if line.strip()]
        manifest = records[0]
        if (len(records) > 34 or manifest["type"] != "manifest" or manifest["schema"] != SCHEMA
                or manifest["bank_sha256"] != hashlib.sha256(BANK_PATH.read_bytes()).hexdigest()
                or type(manifest["synthetic"]) is not bool):
            raise ValueError
        config, _, _ = _config(manifest["config"])
        if config != manifest["config"]:
            raise ValueError
        cells = {c["id"]: c for c in _common_cells(load_identification_bank(), _models(load_identification_bank(), config["candidate_models"]))}
        previous = None
        count = 0
        observed, current_cell, current_count, stages = {}, None, 0, []
        terminal = False
        stable_stop = False
        for i, row in enumerate(records):
            if (row["sequence"] != i or row["previous_hash"] != previous
                    or row["record_hash"] != content_hash({k: v for k, v in row.items() if k != "record_hash"})):
                raise ValueError
            previous = row["record_hash"]
            if row["type"] == "request":
                if terminal:
                    raise ValueError
                if current_cell is None or current_count == BLOCK:
                    if (count + BLOCK > config["budget"]["max_requests"]
                            or (count + BLOCK) * TOKEN_CAP > config["budget"]["max_output_tokens"]):
                        raise ValueError
                    expected = select_next_cell(load_identification_bank(), _models(load_identification_bank(), config["candidate_models"]), observed)
                    if expected is None or row["cell_id"] != expected["id"]:
                        raise ValueError
                    current_cell, current_count = expected["id"], 0
                    observed[current_cell] = Counter()
                if row["cell_id"] != current_cell:
                    raise ValueError
                count += 1
                if row["request"] != _body(config, cells[row["cell_id"]]):
                    raise ValueError
                if row.get("request_headers", {}) != ({"x-api2d-no-cache": "1"} if config["api2d_no_cache"] else {}):
                    raise ValueError
                value, status = _sample(row, cells[row["cell_id"]])
                if status == "valid":
                    observed[current_cell][value] += 1
                    current_count += 1
                    if current_count == BLOCK:
                        stages.append(_stage(load_identification_bank(), _models(load_identification_bank(), config["candidate_models"]), observed))
                        if config.get("stop_policy") == "stable_shortlist" and should_stop_stable(stages):
                            terminal, stable_stop = True, True
                else:
                    terminal = True
            elif i not in {0, len(records) - 1}:
                raise ValueError
        if records[-1]["type"] != "finish" or count > config["budget"]["max_requests"] or count * TOKEN_CAP > config["budget"]["max_output_tokens"]:
            raise ValueError
        finish = records[-1]
        if stable_stop != (finish.get("stop_reason") == "stable_shortlist"):
            raise ValueError
        if stable_stop and (finish.get("state") != "complete" or finish.get("failure") is not None):
            raise ValueError
        return records
    except (KeyError, TypeError, IndexError, ValueError, AttributeError, RecursionError):
        raise ValueError("识别记录的协议、参考哈希或记录链不一致。") from None


def replay_identification(records: list[dict] | Path, out: Path) -> dict:
    text = records.read_text(encoding="utf-8") if isinstance(records, Path) else "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
    checked = parse_identification_records(text)
    output = Path(out)
    output.mkdir(parents=True, exist_ok=False)
    (output / "identification_records.jsonl").write_text(text + "\n", encoding="utf-8")
    result = _report(load_identification_bank(), checked[0]["config"], checked)
    _write_report(output, result)
    return result


def demo_identification(out: Path, model_id: str | None = None, budget: dict | None = None) -> dict:
    """Deterministic process fixture; it reuses reference counts, not held-out tests."""
    bank = load_identification_bank()
    target = next((m for m in bank["models"] if m["model"] == model_id), bank["models"][0])
    if model_id is not None and target["model"] != model_id:
        raise ValueError("示例模型不在参考库。")
    config, _, _ = _config({"candidate": {"api_base": "https://offline.example/v1", "model": "offline-demo"}, "budget": budget or DEFAULT_BUDGET})
    output = Path(out)
    output.mkdir(parents=True, exist_ok=False)
    records, observed = [], {}
    path = output / "identification_records.jsonl"
    fp._append(path, records, {"type": "manifest", "schema": SCHEMA, "run_id": uuid4().hex,
        "bank_sha256": hashlib.sha256(BANK_PATH.read_bytes()).hexdigest(), "config": config, "synthetic": True,
        "demo_model": target["model"], "created_at": datetime.now(timezone.utc).isoformat()})
    while len([r for r in records if r["type"] == "request"]) + BLOCK <= min(config["budget"]["max_requests"], config["budget"]["max_output_tokens"] // TOKEN_CAP):
        cell = select_next_cell(bank, bank["models"], observed)
        if cell is None:
            break
        values = [value for value, count in sorted(target["counts"][cell["id"]].items()) for _ in range(count)]
        observed[cell["id"]] = Counter()
        for i in range(BLOCK):
            value = values[(i * len(values)) // BLOCK]
            raw = next((k for k, v in COIN.get(cell["lang"], {}).items() if v == value), value) if cell["normalize_as"] == "binary" else value
            fp._append(path, records, {"type": "request", "cell_id": cell["id"], "request": _body(config, cell), "http_status": 200,
                "raw_response": {"choices": [{"message": {"content": raw}, "finish_reason": "stop"}]}, "error": None, "elapsed_seconds": 0.0})
            observed[cell["id"]][value] += 1
    fp._append(path, records, {"type": "finish", "state": "complete", "stop_reason": "demo_budget_exhausted", "failure": None})
    result = _report(bank, config, records)
    result["demo"] = {"model": target["model"], "kind": "reference_derived_fixture_not_accuracy_evaluation"}
    _write_report(output, result)
    return result
