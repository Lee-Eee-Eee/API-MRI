"""Descriptive service measurements, separate from the distribution test."""

from __future__ import annotations

from collections import defaultdict
import json
import math
from statistics import median
from typing import Any, Sequence

from .records import AuditRecord

TOKEN_FIELDS = ("input_tokens", "output_tokens", "total_tokens")


def _reject_constant(value: str) -> None:
    raise ValueError("Non-finite JSON numbers are not supported")


def _json_object(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError):
        return None


def check_response(record: AuditRecord) -> bool | None:
    """Evaluate a declared narrow task; None means no supported valid check.

    Exact checks ignore surrounding whitespace; contains checks are case
    sensitive. JSON checks compare the complete object, independent of key
    order. An API error with a valid check is a failed task, not a skipped task.
    """
    check = record.metadata.get("probe_check")
    if not isinstance(check, dict):
        return None
    kind, expected = check.get("kind"), check.get("expected")
    if not isinstance(kind, str):
        return None
    if kind in {"exact", "contains"}:
        if not isinstance(expected, str) or (kind == "contains" and not expected):
            return None
    elif kind == "json_object":
        encoded_expected = _json_object(expected)
        if encoded_expected is None:
            return None
    else:
        return None
    if record.status != "success" or record.response is None:
        return False
    if kind == "exact":
        return record.response.strip() == expected.strip()
    if kind == "contains":
        return expected in record.response
    try:
        actual = json.loads(record.response, parse_constant=_reject_constant)
    except (ValueError, TypeError, RecursionError):
        return False
    return _json_object(actual) == encoded_expected


def summarize_records(records: Sequence[AuditRecord]) -> dict[str, Any]:
    successes = sum(row.status == "success" for row in records)
    elapsed = sorted(
        float(row.elapsed_seconds) for row in records
        if row.elapsed_seconds is not None and math.isfinite(row.elapsed_seconds)
        and row.elapsed_seconds >= 0
    )
    reported_tokens: dict[str, int | None] = {}
    reported_token_records: dict[str, int] = {}
    for field in TOKEN_FIELDS:
        values = [row.usage.get(field) for row in records]
        present = [value for value in values if type(value) is int and value >= 0]
        reported_tokens[field] = sum(present) if present else None
        reported_token_records[field] = len(present)
    checks = [check_response(row) for row in records]
    evaluated = sum(value is not None for value in checks)
    passed = sum(value is True for value in checks)
    return {
        "records": len(records), "successes": successes,
        "errors": sum(row.status == "error" for row in records),
        "success_rate": successes / len(records) if records else None,
        "elapsed_seconds": {
            "count": len(elapsed),
            "median": median(elapsed) if elapsed else None,
            "p95": elapsed[math.ceil(0.95 * len(elapsed)) - 1] if elapsed else None,
        },
        "reported_tokens": reported_tokens,
        "reported_token_records": reported_token_records,
        "checks": {"evaluated": evaluated, "passed": passed,
                   "pass_rate": passed / evaluated if evaluated else None},
    }


def build_diagnostics(reference: Sequence[AuditRecord], candidate: Sequence[AuditRecord],
                      *, prompt_ids: Sequence[str] = ()) -> dict[str, Any]:
    grouped: dict[str, dict[str, list[AuditRecord]]] = defaultdict(
        lambda: {"reference": [], "candidate": []}
    )
    for prompt_id in prompt_ids:
        grouped[prompt_id]
    for role, records in (("reference", reference), ("candidate", candidate)):
        for row in records:
            grouped[row.prompt_id][role].append(row)
    return {
        "roles": {"reference": summarize_records(reference), "candidate": summarize_records(candidate)},
        "prompts": [
            {"prompt_id": prompt_id,
             "roles": {role: summarize_records(grouped[prompt_id][role]) for role in ("reference", "candidate")}}
            for prompt_id in sorted(grouped)
        ],
        "measurement_notes": {
            "latency": "客户端观察的请求耗时，含网络与排队；汇总所有有耗时的成功或失败请求。P95采用最近秩法。",
            "usage": "token为来源自报计量；演示数据为人为设定值。仅累加实际报告的字段，缺失项保留null，另列已报告条数。",
            "checks": "通过率只描述指定答案或格式的限定任务；有有效检查规则的失败请求计为未通过。它不认证模型身份。",
        },
    }
