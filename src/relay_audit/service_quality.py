"""Bounded client-side service measurements and a harmless tool-call circuit.

The timings describe this client's HTTP/SSE observations.  They do not isolate
DNS, TLS, the provider's queue, or inference time.  SSE events are not tokens.
"""
from __future__ import annotations

from collections import Counter
import codecs
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import statistics
import time
from urllib.parse import quote, urlsplit
from uuid import uuid4

import httpx

from .fingerprint import _append, _clean
from .records import content_hash

SCHEMA = "api-mri-service/1"
DEFAULT_BUDGET = {"max_requests": 6, "max_output_tokens": 768}
MAX_RESPONSE_BYTES = 64 * 1024
MAX_EVENTS = 512
MAX_RECORDS_BYTES = 6 * 1024 * 1024
HEADER_NAMES = ("content-type", "x-request-id", "request-id", "x-api2d-cache", "x-cache", "cf-cache-status", "retry-after")
TRANSPORT_ERRORS = {"request_cancelled", "request_timeout", "network_error", "response_size_limit", "invalid_stream_response"}
HALTING_ERRORS = {"authentication_error", "permission_error", "rate_limited", "network_error", "request_timeout", "response_size_limit"}
TOOL = {"type": "function", "function": {"name": "lookup_sample", "description": "Read one value from the local synthetic sample table.",
    "parameters": {"type": "object", "properties": {"sample_id": {"type": "string", "enum": ["MRI-17"]},
        "field": {"type": "string", "enum": ["temperature"]}}, "required": ["sample_id", "field"], "additionalProperties": False}}}
TOOL_DISTRACTOR = {"type": "function", "function": {"name": "lookup_unit_legend",
    "description": "Read the definition of a unit symbol. This table has no measured values for samples.",
    "parameters": {"type": "object", "properties": {"unit": {"type": "string", "enum": ["C"]}},
        "required": ["unit"], "additionalProperties": False}}}


def _config(payload: dict) -> tuple[dict, str | None, str | None]:
    allowed = {"candidate", "checks", "samples", "max_tokens", "timeout", "budget", "token_parameter", "stream_usage"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("服务检测配置包含不支持的字段。")
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
    key, env = candidate.get("api_key"), candidate.get("api_key_env")
    if key is not None and (not isinstance(key, str) or not 8 <= len(key) <= 4096 or re.search(r"\s", key)):
        raise ValueError("API密钥格式无效。")
    if env is not None and (not isinstance(env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", env)):
        raise ValueError("API密钥环境变量名称无效。")
    if key and env:
        raise ValueError("api_key与api_key_env二选一。")
    checks = payload.get("checks", ["stream", "tools"])
    if not isinstance(checks, list) or not checks or len(checks) > 2 or any(not isinstance(c, str) or c not in {"stream", "tools"} for c in checks) or len(set(checks)) != len(checks):
        raise ValueError("checks请选择stream、tools中的一项或两项。")
    values = {"samples": (2, 1, 12), "max_tokens": (128, 32, 512), "timeout": (30, 1, 300)}
    clean = {"candidate": {"api_base": base, "model": model}, "checks": sorted(checks)}
    token_parameter = payload.get("token_parameter", "max_tokens")
    if not isinstance(token_parameter, str) or token_parameter not in {"max_tokens", "max_completion_tokens"}:
        raise ValueError("token_parameter请选择max_tokens或max_completion_tokens。")
    stream_usage = payload.get("stream_usage", True)
    if type(stream_usage) is not bool:
        raise ValueError("stream_usage须为布尔值。")
    clean.update(token_parameter=token_parameter, stream_usage=stream_usage)
    for name, (default, lower, upper) in values.items():
        value = payload.get(name, default)
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError(f"{name}须为{lower}至{upper}整数。")
        clean[name] = value
    budget = payload.get("budget", {})
    if not isinstance(budget, dict) or set(budget) - set(DEFAULT_BUDGET):
        raise ValueError("budget包含不支持的字段。")
    clean["budget"] = {**DEFAULT_BUDGET, **budget}
    for name, limit in (("max_requests", 32), ("max_output_tokens", 8192)):
        value = clean["budget"][name]
        if type(value) is not int or not 1 <= value <= limit:
            raise ValueError(f"{name}须为1至{limit}整数。")
    return clean, key, env


def plan_service_quality(payload: dict) -> dict:
    """Validate and display a worst-case plan without opening a connection."""
    config, _, _ = _config(payload)
    stages = ([{"id": "stream", "label": "流式响应", "requests": config["samples"]}] if "stream" in config["checks"] else [])
    if "tools" in config["checks"]:
        stages += [{"id": "tool_forced", "label": "指定工具与参数", "requests": 1},
                   {"id": "tool_roundtrip", "label": "读取工具结果", "requests": 1, "conditional": True},
                   {"id": "tool_auto", "label": "自主选择工具", "requests": 1},
                   {"id": "tool_no_call", "label": "无需工具时直接回答", "requests": 1}]
    total = sum(stage["requests"] for stage in stages)
    capacity = min(config["budget"]["max_requests"], config["budget"]["max_output_tokens"] // config["max_tokens"], total)
    return {"kind": "service_quality", "schema": SCHEMA, "candidate": config["candidate"], "checks": config["checks"],
            "samples": config["samples"], "max_tokens": config["max_tokens"], "timeout": config["timeout"],
            "token_parameter": config["token_parameter"], "stream_usage": config["stream_usage"],
            "stages": stages, "planned_requests": total, "maximum_requests": capacity,
            "maximum_requested_output_tokens": capacity * config["max_tokens"], "budget": config["budget"],
            "network_retries": 0, "concurrency": 1,
            "notes": ["请求串行发送；工具只查询本机合成表。",
                      "配额按每次所选输出参数累加；输入费用和供应商另列用量另记。",
                      "响应块可含多个tokens；只用供应商返回的usage计算token速率。"]}


def _number(value):
    return value if type(value) is int and 0 <= value <= 1_000_000_000 else None


def _usage(raw) -> dict:
    value = raw if isinstance(raw, dict) else {}
    details = value.get("completion_tokens_details")
    return {**{name: _number(value.get(name)) for name in ("prompt_tokens", "completion_tokens", "total_tokens")},
            "reasoning_tokens": _number(details.get("reasoning_tokens")) if isinstance(details, dict) else None}


def _http_error(status: int | None) -> str | None:
    if status is None or 200 <= status < 300:
        return None
    return {401: "authentication_error", 403: "permission_error", 429: "rate_limited"}.get(status, "server_error" if status >= 500 else "http_error")


def _derive_error(row, body=None):
    """Classify saved protocol observations; a supplied verdict is never input."""
    body = body if body is not None else row["request"]
    status, transport = row.get("http_status"), row.get("transport_error")
    if status is not None and (type(status) is not int or not 100 <= status <= 599):
        raise ValueError("invalid_http_status")
    if transport is not None and transport not in TRANSPORT_ERRORS:
        raise ValueError("invalid_transport_error")
    if _http_error(status):
        return _http_error(status)
    if transport:
        return transport
    if status is None:
        raise ValueError("missing_transport_outcome")
    raw = row.get("raw_response")
    streaming = (body.get("stream") and "text/event-stream" in row.get("response_headers", {}).get("content-type", "").lower())
    if streaming:
        finishes = []
        for event in row.get("events", []):
            choices = event["data"].get("choices")
            choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
            finish = choice.get("finish_reason")
            if isinstance(finish, str):
                finishes.append(finish)
        if "error" in finishes:
            return "generation_error"
        if "content_filter" in finishes:
            return "content_filtered"
        if not row.get("terminal_event") or not row.get("stream_complete"):
            return "incomplete_stream"
        if not row.get("content"):
            return "empty_content"
        return None
    if isinstance(raw, dict) and "unparsed_body" in raw:
        return "invalid_json_response"
    if not isinstance(raw, dict):
        return "invalid_chat_response"
    if raw.get("error"):
        return "api_error"
    choices = raw.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    if not choice or not message:
        return "invalid_chat_response"
    if choice.get("finish_reason") == "error":
        return "generation_error"
    if choice.get("finish_reason") == "content_filter":
        return "content_filtered"
    if body.get("stream"):
        return "stream_not_supported"
    return None


def _sse_lines(response, check_deadline):
    """Bound bytes and check cancellation even before a partial line ends."""
    decoder, pending, size = codecs.getincrementaldecoder("utf-8")("replace"), "", 0
    for chunk in response.iter_bytes():
        check_deadline()
        size += len(chunk)
        if size > MAX_RESPONSE_BYTES:
            raise OverflowError
        pending += decoder.decode(chunk)
        while True:
            boundary = re.search(r"\r\n|\n|\r(?!$)", pending)
            if boundary is None:
                break
            yield pending[:boundary.start()]
            pending = pending[boundary.end():]
    pending += decoder.decode(b"", final=True)
    if pending:
        yield pending.rstrip("\r")


def _request(client, config, key, body, cancelled=None, clock=None) -> dict:
    """Read bounded JSON or SSE; timings are relative to this request start."""
    clock = clock or time.monotonic
    start = clock()
    row = {"http_status": None, "error": None, "transport_error": None, "response_headers": {}, "raw_response": None,
           "events": [], "usage": _usage(None), "content": "", "reasoning_observed": False,
           "finish_reason": None, "stream_complete": False, "terminal_event": None,
           "timing": {"headers_ms": None, "first_content_ms": None, "first_reasoning_ms": None,
                      "last_content_ms": None, "response_ms": None, "content_event_gaps_ms": []}}
    raw_usage = None
    content_times = []
    def elapsed():
        return round(max(0.0, clock() - start) * 1000, 6)
    def check_deadline():
        if cancelled and cancelled():
            raise InterruptedError
        if clock() - start > config["timeout"]:
            raise TimeoutError
    try:
        with client.stream("POST", config["candidate"]["api_base"], headers={"Authorization": f"Bearer {key}"}, json=body) as response:
            row["http_status"] = response.status_code
            row["timing"]["headers_ms"] = elapsed()
            row["response_headers"] = {name: response.headers[name][:300] for name in HEADER_NAMES if name in response.headers}
            streaming = body.get("stream") and "text/event-stream" in response.headers.get("content-type", "").lower() and response.is_success
            if not streaming:
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    check_deadline()
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise OverflowError
                    chunks.append(chunk)
                text = b"".join(chunks).decode("utf-8", errors="replace")
                try:
                    raw = json.loads(text, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                except (ValueError, RecursionError):
                    raw = {"unparsed_body": text[:2048]}
                    row["error"] = "invalid_json_response"
                row["raw_response"] = raw
                row["error"] = _http_error(response.status_code) or row["error"]
                if not row["error"] and isinstance(raw, dict):
                    choices = raw.get("choices")
                    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
                    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
                    row["content"] = message.get("content") if isinstance(message.get("content"), str) else ""
                    row["finish_reason"] = choice.get("finish_reason") if isinstance(choice.get("finish_reason"), str) else None
                    raw_usage = raw.get("usage")
                    if body.get("stream"):
                        row["error"] = "stream_not_supported"
                    elif not choice or not message:
                        row["error"] = "invalid_chat_response"
                elif not row["error"]:
                    row["error"] = "invalid_chat_response"
            else:
                data_lines = []
                def consume_event():
                    nonlocal raw_usage
                    if not data_lines:
                        return
                    data = "\n".join(data_lines)
                    data_lines.clear()
                    at = elapsed()
                    if data.strip() == "[DONE]":
                        row["stream_complete"] = True
                        row["terminal_event"] = {"at_ms": at, "data": "[DONE]"}
                        return
                    try:
                        value = json.loads(data, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                    except (ValueError, RecursionError):
                        raise ValueError("invalid_sse_json") from None
                    if not isinstance(value, dict) or value.get("error"):
                        raise ValueError("sse_api_error")
                    if len(row["events"]) >= MAX_EVENTS:
                        raise OverflowError
                    row["events"].append({"at_ms": at, "data": value})
                    if isinstance(value.get("usage"), dict):
                        raw_usage = value["usage"]
                    choices = value.get("choices")
                    if not isinstance(choices, list) or not choices:
                        return
                    choice = choices[0]
                    if not isinstance(choice, dict):
                        raise ValueError("invalid_sse_choice")
                    delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
                    text = delta.get("content")
                    if isinstance(text, str) and text:
                        row["content"] += text
                        content_times.append(at)
                        if row["timing"]["first_content_ms"] is None:
                            row["timing"]["first_content_ms"] = at
                        row["timing"]["last_content_ms"] = at
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if isinstance(reasoning, str) and reasoning:
                        row["reasoning_observed"] = True
                        if row["timing"]["first_reasoning_ms"] is None:
                            row["timing"]["first_reasoning_ms"] = at
                    if isinstance(choice.get("finish_reason"), str):
                        row["finish_reason"] = choice["finish_reason"]
                for line in _sse_lines(response, check_deadline):
                    check_deadline()
                    if not line:
                        consume_event()
                        if row["stream_complete"]:
                            break
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].lstrip(" "))
                consume_event()
                if not row["stream_complete"] and row["finish_reason"] is None:
                    row["error"] = "incomplete_stream"
                if not row["content"] and not row["error"]:
                    row["error"] = "empty_content"
    except InterruptedError:
        row["transport_error"] = "request_cancelled"
    except (TimeoutError, httpx.TimeoutException):
        row["transport_error"] = "request_timeout"
    except httpx.HTTPError:
        row["transport_error"] = "network_error"
    except OverflowError:
        row["transport_error"] = "response_size_limit"
    except (ValueError, TypeError, RecursionError):
        row["transport_error"] = "invalid_stream_response"
    row["timing"]["response_ms"] = elapsed()
    row["timing"]["content_event_gaps_ms"] = [round(b - a, 6) for a, b in zip(content_times, content_times[1:])]
    row["usage"] = _usage(raw_usage)
    row["raw_usage"] = raw_usage if isinstance(raw_usage, dict) else None
    row["error"] = _derive_error(row, body)
    row["metrics"] = _rates(row)
    return row


def _rates(row: dict) -> dict:
    timing, usage = row["timing"], row["usage"]
    duration = timing.get("response_ms")
    tokens = usage.get("completion_tokens")
    accounting_rate = round(tokens * 1000 / duration, 6) if tokens is not None and duration and not row.get("error") else None
    # Standard SSE does not reveal how many tokens were in the first event.
    # Even explicit zero reasoning usage cannot establish an exact decode rate.
    return {"output_tokens_per_second": accounting_rate, "visible_decode_tokens_per_second": None,
            "characters_per_second": round(len(row["content"]) * 1000 / duration, 6) if duration and row["content"] and not row.get("error") else None}


def _redact_result(value, secrets):
    cleaned = _clean(value, secrets)
    # A malicious stream can echo a key across several JSON string values.
    # Redact the affected channel as a whole, then rebuild the saved text from
    # the saved events so replay has exactly the same observable material.
    if value.get("events"):
        channels = ("content", "reasoning_content", "reasoning")
        for channel in channels:
            originals = []
            for event in value["events"]:
                choices = event["data"].get("choices")
                choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
                delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
                if isinstance(delta.get(channel), str):
                    originals.append(delta[channel])
            joined = "".join(originals)
            if _clean(joined, secrets) != joined:
                for event in cleaned["events"]:
                    choices = event["data"].get("choices")
                    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
                    delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
                    if isinstance(delta.get(channel), str) and delta[channel]:
                        delta[channel] = "[REDACTED]"
        pieces = []
        for event in cleaned["events"]:
            choices = event["data"].get("choices")
            choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            if isinstance(delta.get("content"), str):
                pieces.append(delta["content"])
        cleaned["content"] = "".join(pieces)
    return cleaned


def _tool_check(row: dict) -> dict:
    """Validate exactly one call before executing the local fixture."""
    if row.get("error"):
        return {"status": "error", "reason": row["error"]}
    raw = row.get("raw_response")
    try:
        message = raw["choices"][0]["message"]
        calls = message.get("tool_calls")
        if not isinstance(calls, list) or len(calls) != 1:
            return {"status": "failed", "reason": "expected_one_tool_call"}
        call = calls[0]
        identifier = call.get("id")
        if call.get("type") != "function" or not isinstance(identifier, str) or not identifier or len(identifier) > 200 or any(ord(c) < 32 for c in identifier):
            return {"status": "failed", "reason": "invalid_tool_call_id"}
        function = call["function"]
        if function.get("name") != "lookup_sample":
            return {"status": "failed", "reason": "wrong_tool"}
        args = json.loads(function["arguments"], parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if args != {"sample_id": "MRI-17", "field": "temperature"}:
            return {"status": "failed", "reason": "invalid_arguments"}
        if row.get("finish_reason") in {"length", "max_tokens", "content_filter", "error"}:
            return {"status": "failed", "reason": "truncated_tool_call"}
        safe_call = {"id": identifier, "type": "function", "function": {"name": "lookup_sample", "arguments": function["arguments"]}}
        return {"status": "passed", "reason": "name_and_arguments_valid", "call": safe_call,
                "message": {"role": "assistant", "content": message.get("content") if isinstance(message.get("content"), str) else None, "tool_calls": [safe_call]}}
    except (KeyError, TypeError, ValueError, IndexError, AttributeError, RecursionError):
        return {"status": "failed", "reason": "invalid_tool_arguments_json"}


def _assessment(stage, row, receipt=None):
    if stage in {"tool_forced", "tool_auto"}:
        checked = _tool_check(row)
        return {name: checked[name] for name in ("status", "reason")}
    if stage == "tool_roundtrip":
        raw = row.get("raw_response")
        try:
            extra_calls = raw["choices"][0]["message"].get("tool_calls")
        except (TypeError, KeyError, IndexError, AttributeError):
            extra_calls = True
        okay = (row["content"].strip() == f"21.5 C {receipt}" and not row["error"]
                and not extra_calls
                and row["finish_reason"] not in {"length", "max_tokens", "content_filter", "error"})
        return {"status": "error" if row["error"] else "passed" if okay else "failed",
                "reason": row["error"] or ("returned_fixture_read" if okay else "tool_result_not_reproduced")}
    if stage == "tool_no_call":
        raw = row.get("raw_response") or {}
        try:
            calls = raw["choices"][0]["message"].get("tool_calls")
            okay = (not calls and row["content"].strip() == "HELLO" and not row["error"]
                    and row["finish_reason"] not in {"length", "max_tokens", "content_filter", "error"})
        except (KeyError, IndexError, TypeError, AttributeError):
            okay = False
        return {"status": "error" if row["error"] else "passed" if okay else "failed",
                "reason": row["error"] or ("direct_answer_without_tool" if okay else "unneeded_call_or_wrong_answer")}
    return None


def _stages(config):
    return (["stream"] * config["samples"] if "stream" in config["checks"] else []) + (["tool_forced", "tool_roundtrip", "tool_auto", "tool_no_call"] if "tools" in config["checks"] else [])


def _body(config, stage, marker, previous=None, receipt=None):
    body = {"model": config["candidate"]["model"], config["token_parameter"]: config["max_tokens"]}
    if stage == "stream":
        body.update(messages=[{"role": "user", "content": f"Measurement {marker}. Write the integers 1 through 40, one per line, and no other text."}], stream=True)
        if config["stream_usage"]:
            body["stream_options"] = {"include_usage": True}
    else:
        user = {"role": "user", "content": f"Measurement {marker}. Use lookup_sample to read the temperature of sample MRI-17. After the tool returns, reply exactly: value unit receipt, using the three returned fields."}
        body.update(messages=[user], tools=[TOOL], tool_choice="auto", stream=False)
        if stage == "tool_forced":
            body["tool_choice"] = {"type": "function", "function": {"name": "lookup_sample"}}
        elif stage == "tool_roundtrip":
            body["messages"] += [previous["message"], {"role": "tool", "tool_call_id": previous["call"]["id"],
                "content": json.dumps({"value": 21.5, "unit": "C", "receipt": receipt}, separators=(",", ":"))}]
            body["tool_choice"] = "none"
        elif stage == "tool_auto":
            body["tools"] = [TOOL, TOOL_DISTRACTOR]
            body["messages"] = [{"role": "user", "content": f"Measurement {marker}. What is sample MRI-17's measured temperature? Obtain the measured value from the available records. After a result is returned, reply with its value and unit."}]
        elif stage == "tool_no_call":
            body["tools"] = [TOOL, TOOL_DISTRACTOR]
            body["messages"] = [{"role": "user", "content": f"Measurement {marker}. Reply exactly HELLO. Do not use any tools."}]
    return body


def _percentiles(values):
    values = sorted(v for v in values if type(v) in {int, float} and math.isfinite(v))
    def percentile(p):
        position = (len(values) - 1) * p
        lower = int(position)
        return round(values[lower] + (values[min(lower + 1, len(values) - 1)] - values[lower]) * (position - lower), 6)
    return {"count": len(values), "median": round(statistics.median(values), 6) if values else None,
            "p95": percentile(.95) if values else None, "min": min(values) if values else None, "max": max(values) if values else None}


def _cost(rows, config, synthetic=False):
    usage = {}
    for name in ("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens"):
        supplied = [row["usage"][name] for row in rows if row["usage"].get(name) is not None]
        usage[name] = sum(supplied) if supplied and not synthetic else None
        usage[name + "_reported_requests"] = len(supplied) if not synthetic else 0
    return {"request_count": len(rows) if not synthetic else 0, "simulated_request_count": len(rows) if synthetic else 0,
            "requested_output_tokens": sum(row["request"][config["token_parameter"]] for row in rows) if not synthetic else 0,
            "budget": config["budget"], "usage": usage,
            "note": "usage为已返回请求的分项小计；缺失项不补零，输出配额不含输入费用。"}


def _report(records):
    manifest, finish = records[0], records[-1]
    config = manifest["config"]
    rows = [row for row in records if row["type"] == "request"]
    streams = [row for row in rows if row["stage"] == "stream"]
    successful = [row for row in rows if not row.get("error")]
    stream_ok = [row for row in streams if not row.get("error")]
    tools = {}
    for stage in ("tool_forced", "tool_roundtrip", "tool_auto", "tool_no_call"):
        matches = [row for row in rows if row["stage"] == stage]
        if matches:
            tools[stage] = {**matches[0]["assessment"], "request_index": matches[0]["sequence"] - 1}
        else:
            tools[stage] = {"status": "skipped", "reason": "not_selected" if "tools" not in config["checks"] else "not_reached_or_dependency_failed"}
    request_rows = []
    for row in rows:
        request_rows.append({"index": row["sequence"] - 1, "stage": row["stage"], "http_status": row["http_status"], "error": row["error"],
            "timing": row["timing"], "metrics": row["metrics"], "usage": row["usage"], "assessment": row.get("assessment"),
            "content_excerpt": row["content"][:180], "finish_reason": row["finish_reason"], "response_headers": row["response_headers"],
            "timeline": [{"at_ms": event["at_ms"], "content_characters": _event_content_length(event)} for event in row["events"]]})
    failure = finish.get("failure")
    cancelled = finish["stop_code"] == "cancelled"
    return {"schema": SCHEMA, "kind": "service_quality", "status": "cancelled" if cancelled else "partial" if failure or finish["stop_code"].startswith("budget_") else "completed",
        "synthetic": manifest["synthetic"], "candidate": config["candidate"], "checks": config["checks"], "created_at": manifest["created_at"],
        "service_quality": {"connection": {"attempted_requests": len(rows), "http_successes": sum(row["http_status"] is not None and 200 <= row["http_status"] < 300 for row in rows),
                "successful_requests": len(successful), "success_rate": len(successful) / len(rows) if rows else None,
                "errors": dict(Counter(row["error"] for row in rows if row["error"])),
                "headers_ms": _percentiles([row["timing"]["headers_ms"] for row in rows]),
                "response_ms": _percentiles([row["timing"]["response_ms"] for row in successful])},
            "stream": {"attempted_samples": len(streams), "successful_samples": len(stream_ok),
                "headers_ms": _percentiles([row["timing"]["headers_ms"] for row in stream_ok]),
                "response_ms": _percentiles([row["timing"]["response_ms"] for row in stream_ok]),
                "first_content_ms": _percentiles([row["timing"]["first_content_ms"] for row in stream_ok]),
                "first_reasoning_ms": _percentiles([row["timing"]["first_reasoning_ms"] for row in stream_ok]),
                "output_tokens_per_second": _percentiles([row["metrics"]["output_tokens_per_second"] for row in stream_ok]),
                "visible_decode_tokens_per_second": _percentiles([row["metrics"]["visible_decode_tokens_per_second"] for row in stream_ok]),
                "content_event_gaps_ms": _percentiles([gap for row in stream_ok for gap in row["timing"]["content_event_gaps_ms"]]),
                "characters_per_second": _percentiles([row["metrics"]["characters_per_second"] for row in stream_ok]),
                "truncated_samples": sum(row["finish_reason"] in {"length", "max_tokens"} for row in streams)},
            "tools": tools, "requests": request_rows},
        "collection": {"planned_requests": len(_stages(config)), "completed_requests": len(rows), "cancelled": cancelled,
            "stop_code": finish["stop_code"], "failure": failure},
        "cost": _cost(rows, config, manifest["synthetic"]),
        "definitions": {"headers_ms": "发出请求到收到HTTP响应头，包含网络、排队与服务端处理。",
            "first_content_ms": "发出请求到首个非空正文事件；角色、空块和推理另记。",
            "output_tokens_per_second": "供应商返回的completion_tokens÷完整响应秒数，可能包含推理tokens。",
            "visible_decode_tokens_per_second": "标准SSE不提供首块token数，精确正文解码速率保留未知。",
            "content_event_gaps_ms": "相邻非空正文事件的接收间隔；一个事件可能含多个tokens。",
            "tools": "指定调用、自主选工具、不调用及读取结果分别判分，全部使用合成表。"}}


def _event_content_length(event):
    choices = event["data"].get("choices")
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    delta = choice.get("delta")
    text = delta.get("content") if isinstance(delta, dict) else None
    return len(text) if isinstance(text, str) else 0


def _write_report(out, report):
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (out / "report.md").write_text(markdown_service_report(report), encoding="utf-8")


def markdown_service_report(report):
    data = report["service_quality"]
    show = lambda v: "未知" if v is None else str(round(v, 3)) if isinstance(v, float) else str(v)
    lines = ["# API-MRI 服务测量", "", f"模型别名：{report['candidate']['model']}", "",
        f"状态：{report['status']}；{'合成演示' if report['synthetic'] else '客户端实测'}。", "",
        "| 观察 | 结果 |", "| --- | --- |",
        f"| 成功请求 | {data['connection']['successful_requests']}/{data['connection']['attempted_requests']} |",
        f"| 首正文中位数 | {show(data['stream']['first_content_ms']['median'])} ms |",
        f"| 返回用量／完整响应时间 | {show(data['stream']['output_tokens_per_second']['median'])} tokens/s |",
        f"| 正文事件间隔p95 | {show(data['stream']['content_event_gaps_ms']['p95'])} ms |", "",
        "| 工具检查 | 结果 |", "| --- | --- |"]
    for name, value in data["tools"].items():
        lines.append(f"| {name} | {value['status']} · {value['reason']} |")
    lines += ["", f"实际请求：{report['cost']['request_count']}；累计申请输出配额：{report['cost']['requested_output_tokens']}。", ""]
    lines += [f"- {value}" for value in report["definitions"].values()]
    return "\n".join(lines) + "\n"


def run_service_quality(payload, out, progress=None, cancelled=None, *, transport=None, clock=None, synthetic=False):
    """Collect at most the displayed budget.  No retries or tool code execution.

    ``transport`` and ``clock`` support deterministic offline HTTP fixtures.
    The workbench can call this synchronous runner in its existing worker.
    """
    config, direct, env = _config(payload)
    key = direct or os.environ.get(env or "")
    if not isinstance(key, str) or not 8 <= len(key) <= 4096 or re.search(r"\s", key):
        raise ValueError("API key missing or invalid")
    secrets = (key, quote(key, safe=""))
    config = _clean(config, secrets)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    plan = plan_service_quality(payload)
    (out / "sampling_plan.json").write_text(json.dumps(_clean(plan, secrets), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    records, path = [], out / "service_records.jsonl"
    marker, receipt = uuid4().hex[:12], "R" + uuid4().hex[:12]
    _append(path, records, {"type": "manifest", "schema": SCHEMA, "config": config, "synthetic": synthetic,
        "created_at": datetime.now(timezone.utc).isoformat(), "run_marker": marker})
    stop, failure, forced, request_count = "complete", None, None, 0
    with httpx.Client(timeout=config["timeout"], follow_redirects=False, trust_env=False, transport=transport) as client:
        for index, stage in enumerate(_stages(config)):
            if cancelled and cancelled():
                stop = "cancelled"
                break
            if stage == "tool_roundtrip" and (forced is None or forced.get("status") != "passed"):
                continue
            if request_count >= config["budget"]["max_requests"]:
                stop = "budget_max_requests"
                break
            if (request_count + 1) * config["max_tokens"] > config["budget"]["max_output_tokens"]:
                stop = "budget_max_output_tokens"
                break
            body = _body(config, stage, marker + "-" + str(index), forced, receipt)
            result = _request(client, config, key, body, cancelled, clock)
            saved = _redact_result({"type": "request", "stage": stage, "request": body, **result}, secrets)
            saved["assessment"] = _assessment(stage, saved, receipt)
            saved["metrics"] = _rates(saved)
            _append(path, records, saved)
            if stage == "tool_forced":
                forced = _tool_check(saved)
            request_count += 1
            if progress:
                progress({"phase": "service_quality", "completed": request_count, "total": plan["maximum_requests"],
                    "message": f"已完成{request_count}次服务请求", "cost": _cost([r for r in records if r["type"] == "request"], config, synthetic)})
            if result["error"] == "request_cancelled":
                stop = "cancelled"
                break
            # Authentication, rate and transport failures make further paid
            # probes unhelpful; preserve the first failure without retrying.
            if result["error"] in HALTING_ERRORS:
                stop, failure = "request_failed", result["error"]
                break
    _append(path, records, {"type": "finish", "stop_code": stop, "failure": failure})
    report = _report(records)
    _write_report(out, report)
    return report


def _validate_observations(row):
    """Re-derive display fields from captured JSON/SSE and timing observations."""
    timing = row["timing"]
    if "transport_error" not in row or row.get("error") != _derive_error(row):
        raise ValueError
    required = {"headers_ms", "first_content_ms", "first_reasoning_ms", "last_content_ms", "response_ms", "content_event_gaps_ms"}
    if set(timing) != required or not isinstance(row.get("content"), str) or type(row.get("reasoning_observed")) is not bool:
        raise ValueError
    if type(row.get("stream_complete")) is not bool or row.get("http_status") is not None and type(row["http_status"]) is not int:
        raise ValueError
    for name, value in timing.items():
        if name == "content_event_gaps_ms":
            if not isinstance(value, list) or len(value) > MAX_EVENTS:
                raise ValueError
            values = value
        else:
            values = [] if value is None else [value]
        if any(type(v) not in {int, float} or not math.isfinite(v) or v < 0 for v in values):
            raise ValueError
    if timing["response_ms"] is None or timing["headers_ms"] is not None and timing["headers_ms"] > timing["response_ms"]:
        raise ValueError
    if not isinstance(row.get("events"), list) or len(row["events"]) > MAX_EVENTS:
        raise ValueError
    content, content_times, reasoning_times, finish, raw_usage = "", [], [], None, None
    if row.get("raw_response") is None:
        previous_at = timing["headers_ms"] or 0
        for event in row["events"]:
            at, data = event["at_ms"], event["data"]
            if type(at) not in {int, float} or not math.isfinite(at) or not previous_at <= at <= timing["response_ms"] or not isinstance(data, dict):
                raise ValueError
            previous_at = at
            if isinstance(data.get("usage"), dict):
                raw_usage = data["usage"]
            choices = data.get("choices")
            choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            if isinstance(delta.get("content"), str) and delta["content"]:
                content += delta["content"]
                content_times.append(at)
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if isinstance(reasoning, str) and reasoning:
                reasoning_times.append(at)
            if isinstance(choice.get("finish_reason"), str):
                finish = choice["finish_reason"]
        terminal = row.get("terminal_event")
        if terminal is not None and (terminal.get("data") != "[DONE]" or type(terminal.get("at_ms")) not in {int, float}
                or not previous_at <= terminal["at_ms"] <= timing["response_ms"]):
            raise ValueError
        if row["stream_complete"] != (terminal is not None):
            raise ValueError
    else:
        if row["events"] or row.get("terminal_event") is not None or row["stream_complete"]:
            raise ValueError
        raw = row["raw_response"]
        if isinstance(raw, dict):
            choices = raw.get("choices")
            choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
            message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
            if not _http_error(row["http_status"]) and "unparsed_body" not in raw:
                content = message.get("content") if isinstance(message.get("content"), str) else ""
                finish = choice.get("finish_reason") if isinstance(choice.get("finish_reason"), str) else None
                raw_usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else None
    if (row["content"] != content or row["finish_reason"] != finish or row["raw_usage"] != raw_usage
            or row["reasoning_observed"] != bool(reasoning_times)
            or timing["first_content_ms"] != (content_times[0] if content_times else None)
            or timing["last_content_ms"] != (content_times[-1] if content_times else None)
            or timing["first_reasoning_ms"] != (reasoning_times[0] if reasoning_times else None)
            or timing["content_event_gaps_ms"] != [round(b - a, 6) for a, b in zip(content_times, content_times[1:])]
            or row["usage"] != _usage(raw_usage) or row["metrics"] != _rates(row)):
        raise ValueError


def parse_service_records(text):
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_RECORDS_BYTES:
        raise ValueError("服务记录应为不超过6 MB的JSONL。")
    try:
        records = [json.loads(line, parse_constant=lambda _: (_ for _ in ()).throw(ValueError())) for line in text.lstrip("\ufeff").splitlines() if line.strip()]
        if not 2 <= len(records) <= 34:
            raise ValueError
        manifest = records[0]
        if manifest.get("type") != "manifest" or manifest.get("schema") != SCHEMA or type(manifest.get("synthetic")) is not bool:
            raise ValueError
        config, _, _ = _config(manifest["config"])
        if config != manifest["config"] or records[-1].get("type") != "finish" or records[-1].get("stop_code") not in {"complete", "cancelled", "budget_max_requests", "budget_max_output_tokens", "request_failed"}:
            raise ValueError
        if not isinstance(manifest.get("run_marker"), str) or not re.fullmatch(r"[0-9a-f]{12}", manifest["run_marker"]):
            raise ValueError
        previous, requests, expected_index, forced = None, [], 0, None
        stages = _stages(config)
        for index, row in enumerate(records):
            if not isinstance(row, dict) or row.get("sequence") != index or row.get("previous_hash") != previous:
                raise ValueError
            checked = {k: v for k, v in row.items() if k != "record_hash"}
            if content_hash(checked) != row.get("record_hash"):
                raise ValueError
            previous = row["record_hash"]
            if index not in {0, len(records) - 1}:
                if row.get("type") != "request" or row.get("stage") not in set(_stages(config)) or row.get("request", {}).get(config["token_parameter"]) != config["max_tokens"]:
                    raise ValueError
                if not isinstance(row.get("timing"), dict) or not isinstance(row.get("usage"), dict) or not isinstance(row.get("metrics"), dict):
                    raise ValueError
                _validate_observations(row)
                if stages[expected_index] == "tool_roundtrip" and (forced is None or forced.get("status") != "passed"):
                    expected_index += 1
                if row["stage"] != stages[expected_index]:
                    raise ValueError
                receipt = None
                if row["stage"] == "tool_roundtrip":
                    fixture = json.loads(row["request"]["messages"][-1]["content"])
                    receipt = fixture["receipt"]
                    if not isinstance(receipt, str) or not re.fullmatch(r"R[0-9a-f]{12}", receipt):
                        raise ValueError
                expected_body = _body(config, row["stage"], manifest["run_marker"] + "-" + str(expected_index), forced, receipt)
                if row["request"] != expected_body or row["assessment"] != _assessment(row["stage"], row, receipt):
                    raise ValueError
                if row["stage"] == "tool_forced":
                    forced = _tool_check(row)
                expected_index += 1
                requests.append(row)
        if len(requests) > config["budget"]["max_requests"] or len(requests) * config["max_tokens"] > config["budget"]["max_output_tokens"]:
            raise ValueError
        finish = records[-1]
        halt_rows = [row for row in requests if row["error"] in HALTING_ERRORS or row["error"] == "request_cancelled"]
        if halt_rows:
            last = halt_rows[-1]
            if len(halt_rows) != 1 or last is not requests[-1]:
                raise ValueError
            expected_stop = "cancelled" if last["error"] == "request_cancelled" else "request_failed"
            expected_failure = None if expected_stop == "cancelled" else last["error"]
            if finish["stop_code"] != expected_stop or finish.get("failure") != expected_failure:
                raise ValueError
        elif finish["stop_code"] == "request_failed" or finish.get("failure") is not None:
            raise ValueError
        if finish["stop_code"] == "complete" and expected_index != len(stages):
            raise ValueError
        if finish["stop_code"] == "budget_max_requests" and (expected_index == len(stages) or len(requests) < config["budget"]["max_requests"]):
            raise ValueError
        if finish["stop_code"] == "budget_max_output_tokens" and (expected_index == len(stages)
            or len(requests) >= config["budget"]["max_requests"] or (len(requests) + 1) * config["max_tokens"] <= config["budget"]["max_output_tokens"]):
            raise ValueError
        _report(records)
        return records
    except (ValueError, KeyError, TypeError, IndexError, AttributeError, RecursionError, OverflowError):
        raise ValueError("服务记录的格式、哈希、用量或请求顺序不完整。") from None


def replay_service_quality(records, out):
    if isinstance(records, (str, Path)):
        text = records.read_text(encoding="utf-8") if isinstance(records, Path) else records
    else:
        text = "\n".join(json.dumps(row, ensure_ascii=False, allow_nan=False) for row in records)
    rows = parse_service_records(text)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    (out / "service_records.jsonl").write_text(text.rstrip() + "\n", encoding="utf-8")
    report = _report(rows)
    _write_report(out, report)
    return report


def demo_service_quality(out, *, checks=None):
    """A deterministic synthetic HTTP exchange, never a network call."""
    class Clock:
        value = 0.0
        def __call__(self):
            self.value += .01
            return self.value
    usage = {"prompt_tokens": 48, "completion_tokens": 80, "total_tokens": 128, "completion_tokens_details": {"reasoning_tokens": 0}}
    def handler(request):
        body = json.loads(request.content)
        if body.get("stream"):
            events = [{"choices": [{"delta": {"role": "assistant"}}]}]
            events += [{"choices": [{"delta": {"content": "\n".join(str(v) for v in range(start, start + 10)) + "\n"}}]} for start in (1, 11, 21, 31)]
            events += [{"choices": [{"delta": {}, "finish_reason": "stop"}]}, {"choices": [], "usage": usage}]
            data = "".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n"
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=data)
        message = {"role": "assistant", "content": "HELLO"}
        if body.get("tool_choice") == "none":
            fixture = json.loads(body["messages"][-1]["content"])
            message["content"] = f"{fixture['value']} {fixture['unit']} {fixture['receipt']}"
        elif body["messages"][0]["content"].endswith("Do not use any tools."):
            pass
        else:
            message.update(content=None, tool_calls=[{"id": "fixture-call", "type": "function", "function": {"name": "lookup_sample", "arguments": '{"sample_id":"MRI-17","field":"temperature"}'}}])
        return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}], "usage": usage})
    payload = {"candidate": {"api_base": "https://synthetic.example/v1", "model": "synthetic-service", "api_key": "synthetic-not-a-real-key"}}
    if checks is not None:
        payload["checks"] = checks
    return run_service_quality(payload, out, transport=httpx.MockTransport(handler), clock=Clock(), synthetic=True)
