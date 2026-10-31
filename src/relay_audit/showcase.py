"""Published visual examples and one explicitly budgeted Chat Completions trial.

Generated SVG/HTML remains text here. Rendering belongs in an isolated browser
preview; neither a published example nor a successful drawing identifies weights.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
from urllib.parse import quote
import xml.etree.ElementTree as ET

import httpx

from .fingerprint import _append, _clean
from .records import content_hash
from .service_quality import _config as _service_config, _http_error, _usage

DATA_DIR = Path(__file__).parent / "data" / "showcase"
SCHEMA = "api-mri-showcase/1"
CATALOG_SCHEMA = "api-mri-showcase-catalog/1"
MAX_RESPONSE_BYTES = 256 * 1024
MAX_EVIDENCE_BYTES = 3 * 1024 * 1024
ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,95}")
FORMATS = {"svg", "html"}
LICENSES = {"MIT", "Apache-2.0", "CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "BSD-2-Clause", "BSD-3-Clause", "ISC"}
TRANSPORT_ERRORS = {"request_cancelled", "request_timeout", "network_error", "response_size_limit",
                    "invalid_utf8_response", "unsupported_content_encoding"}
HEADER_NAMES = ("content-type", "content-encoding", "x-request-id", "request-id", "retry-after")


def _task_valid(task: dict, *, saved: bool = False) -> bool:
    return (isinstance(task, dict) and isinstance(task.get("id"), str)
            and (bool(ID_PATTERN.fullmatch(task["id"])) or saved and task["id"] == "[REDACTED]")
            and isinstance(task.get("title"), str) and 1 <= len(task["title"]) <= 200
            and isinstance(task.get("prompt"), str) and 1 <= len(task["prompt"]) <= 16000
            and task.get("format") in FORMATS)


def list_showcase() -> dict:
    """Read the bundled, attributed catalog without contacting any service."""
    path = DATA_DIR / "catalog.json"
    if not path.is_file():
        return {"schema": CATALOG_SCHEMA, "tasks": [], "examples": []}
    raw = path.read_bytes()
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("经典题目录过大。")
    catalog = json.loads(raw)
    if (not isinstance(catalog, dict) or catalog.get("schema") != CATALOG_SCHEMA
            or not isinstance(catalog.get("tasks"), list) or not isinstance(catalog.get("examples"), list)
            or len(catalog["tasks"]) > 100 or len(catalog["examples"]) > 1000):
        raise ValueError("经典题目录格式无效。")
    tasks = catalog["tasks"]
    if any(not _task_valid(task) for task in tasks) or len({task["id"] for task in tasks}) != len(tasks):
        raise ValueError("经典题的题词或标识无效。")
    identifiers = set()
    for example in catalog["examples"]:
        if (not isinstance(example, dict) or not isinstance(example.get("id"), str)
                or not ID_PATTERN.fullmatch(example["id"]) or example["id"] in identifiers
                or example.get("task_id") not in {task["id"] for task in tasks}):
            raise ValueError("公开作品条目的标识无效。")
        identifiers.add(example["id"])
    return catalog


def _task(task_id: str) -> dict:
    for task in list_showcase()["tasks"]:
        if task["id"] == task_id:
            return task
    raise ValueError("请选择目录中已有的经典题。")


def showcase_asset(example_id: str) -> Path:
    """Return only a licensed catalog asset with matching bytes and provenance."""
    if not isinstance(example_id, str) or not ID_PATTERN.fullmatch(example_id):
        raise ValueError("作品标识无效。")
    example = next((item for item in list_showcase()["examples"] if item["id"] == example_id), None)
    if example is None or not isinstance(example.get("asset"), str):
        raise ValueError("该作品仅提供来源链接。")
    license_value = example.get("license")
    license_id = license_value.get("spdx", license_value.get("id")) if isinstance(license_value, dict) else license_value
    asset = Path(example["asset"])
    root = DATA_DIR.resolve()
    if (license_id not in LICENSES or asset.is_absolute() or ".." in asset.parts
            or asset.suffix not in {".svg", ".html"} or "\\" in example["asset"]):
        raise ValueError("该作品没有可分发的本地资源。")
    path = root / asset
    if (path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file()
            or path.stat().st_size > MAX_RESPONSE_BYTES):
        raise ValueError("本地作品资源缺失或过大。")
    raw = path.read_bytes()
    if not isinstance(example.get("sha256"), str) or hashlib.sha256(raw).hexdigest() != example["sha256"]:
        raise ValueError("本地作品资源与来源哈希不一致。")
    try:
        raw.decode("utf-8-sig")
    except UnicodeError:
        raise ValueError("作品应为UTF-8源码。") from None
    return path


def _settings(payload: dict, *, saved: bool = False) -> tuple[dict, str | None, str | None]:
    allowed = {"task_id", "candidate", "max_tokens", "timeout", "token_parameter"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("经典题配置包含不支持的字段。")
    task_id = payload.get("task_id")
    if not isinstance(task_id, str) or not (ID_PATTERN.fullmatch(task_id) or saved and task_id == "[REDACTED]"):
        raise ValueError("请选择已有经典题。")
    # Reuse the existing endpoint, credential and protocol validation. Only the
    # output allowance differs from the much shorter connection probes.
    service, direct, env = _service_config({"candidate": payload.get("candidate"), "checks": ["stream"],
        "samples": 1, "timeout": payload.get("timeout", 60), "token_parameter": payload.get("token_parameter", "max_tokens")})
    cap = payload.get("max_tokens", 2048)
    if type(cap) is not int or not 256 <= cap <= 4096:
        raise ValueError("单次输出配额须为256至4096整数。")
    return {"task_id": task_id, "candidate": service["candidate"], "max_tokens": cap,
            "timeout": service["timeout"], "token_parameter": service["token_parameter"]}, direct, env


def _plan(config: dict, task: dict) -> dict:
    return {"kind": "showcase", "task": task, **config, "planned_requests": 1, "maximum_requests": 1,
            "maximum_requested_output_tokens": config["max_tokens"], "network_retries": 0,
            "concurrency": 1, "max_response_bytes": MAX_RESPONSE_BYTES,
            "protocol": "Chat Completions", "stream": False,
            "cost_note": "配额限制这一次请求的输出；输入与供应商另计的推理费用另记。"}


def plan_showcase(payload: dict) -> dict:
    config, key, _ = _settings(payload)
    return _clean(_plan(config, _task(config["task_id"])), (key, quote(key, safe="")) if key else ())


def _body(config: dict, task: dict) -> dict:
    return {"model": config["candidate"]["model"], "messages": [{"role": "user", "content": task["prompt"]}],
            "stream": False, config["token_parameter"]: config["max_tokens"]}


class _HTMLShape(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.tags = 0

    def handle_starttag(self, tag, attrs):
        self.tags += 1


def _artifact(output: str, kind: str, *, finished: bool) -> dict:
    source = output
    fenced = re.fullmatch(r"\s*```(?:svg|html|xml)?[^\n]*\n(.*?)\n?```\s*", output, re.S | re.I)
    if fenced:
        source = fenced[1]
    valid = False
    problem = None
    try:
        if kind == "svg":
            if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", source, re.I):
                problem = "xml_declaration_disallowed"
            else:
                root = ET.fromstring(source)
                valid = root.tag in {"svg", "{http://www.w3.org/2000/svg}svg"}
        else:
            parser = _HTMLShape()
            parser.feed(source)
            valid = parser.tags > 0
    except (ET.ParseError, ValueError, RecursionError):
        pass
    if not valid and problem is None:
        problem = "invalid_svg" if kind == "svg" else "no_html_element"
    return {"format": kind, "source": source, "sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(), "format_valid": valid,
            "complete": bool(valid and finished), "problem": problem, "executed": False}


def _derive(row: dict, task: dict) -> dict:
    error = _http_error(row["http_status"]) or row["transport_error"]
    raw = None
    try:
        raw = json.loads(row["body_text"], parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, RecursionError):
        if not error:
            error = "invalid_json_response"
    output, finish, reasoning, usage = "", None, "", _usage(None)
    if isinstance(raw, dict):
        usage = _usage(raw.get("usage"))
        choices = raw.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        if isinstance(message.get("content"), str):
            output = message["content"]
        reasoning = "".join(message[key] for key in ("reasoning", "reasoning_content") if isinstance(message.get(key), str))
        finish = choice.get("finish_reason") if isinstance(choice.get("finish_reason"), str) else None
        if not error:
            if raw.get("error"):
                error = "api_error"
            elif not choice or not message:
                error = "invalid_chat_response"
            elif message.get("refusal"):
                error = "refusal"
            elif finish in {"length", "max_tokens"}:
                error = "output_truncated"
            elif finish == "content_filter":
                error = "content_filtered"
            elif finish == "error":
                error = "generation_error"
            elif not output.strip():
                error = "empty_content"
            elif finish is None:
                error = "missing_finish_reason"
            elif finish != "stop":
                error = "unexpected_finish_reason"
    elif not error:
        error = "invalid_chat_response"
    artifact = _artifact(output, task["format"], finished=error is None)
    if not error and not artifact["format_valid"]:
        error = "invalid_artifact"
    reason = ("cancelled" if error == "request_cancelled" else "truncated" if error == "output_truncated"
              else "invalid_output" if error in {"invalid_artifact", "missing_finish_reason"}
              else "request_failed" if error else "completed")
    return {"output": output, "artifact": artifact, "finish_reason": finish, "error": error,
            "reasoning_returned": bool(reasoning or usage.get("reasoning_tokens")),
            "reasoning_characters": len(reasoning), "usage": usage, "reason": reason}


def _request(client, config, key, task, cancelled) -> dict:
    started = time.monotonic()
    row = {"type": "response", "request": _body(config, task), "attempt_started": False, "http_status": None, "response_headers": {},
           "body_text": "", "body_bytes_seen": 0, "body_truncated": False, "transport_error": None,
           "elapsed_seconds": 0.0, "headers_seconds": None}

    def checkpoint():
        if cancelled and cancelled():
            raise InterruptedError
        if time.monotonic() - started > config["timeout"]:
            raise TimeoutError

    try:
        checkpoint()
        row["attempt_started"] = True
        with client.stream("POST", config["candidate"]["api_base"], json=row["request"],
                           headers={"Authorization": "Bearer " + key, "Accept-Encoding": "identity"}) as response:
            row["http_status"] = response.status_code
            row["headers_seconds"] = max(0.0, time.monotonic() - started)
            row["response_headers"] = {name: response.headers[name][:300] for name in HEADER_NAMES if name in response.headers}
            if response.headers.get("content-encoding", "identity").strip().lower() not in {"", "identity"}:
                row["transport_error"] = "unsupported_content_encoding"
            else:
                chunks = []
                for chunk in response.iter_bytes(chunk_size=8192):
                    checkpoint()
                    row["body_bytes_seen"] += len(chunk)
                    if row["body_bytes_seen"] > MAX_RESPONSE_BYTES:
                        row["body_truncated"] = True
                        raise OverflowError
                    chunks.append(chunk)
                checkpoint()
                try:
                    row["body_text"] = b"".join(chunks).decode("utf-8-sig")
                except UnicodeError:
                    row["transport_error"] = "invalid_utf8_response"
    except InterruptedError:
        row["transport_error"] = "request_cancelled"
    except (TimeoutError, httpx.TimeoutException):
        row["transport_error"] = "request_timeout"
    except httpx.HTTPError:
        row["transport_error"] = "network_error"
    except OverflowError:
        # Do not persist a boundary fragment of an echoed credential.
        row["body_text"] = ""
        row["transport_error"] = "response_size_limit"
    row["elapsed_seconds"] = max(0.0, time.monotonic() - started)
    return row


def _redact_response(row: dict, secrets: tuple[str, ...]) -> dict:
    result = _clean(row, secrets)
    try:
        decoded = json.loads(row["body_text"], parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        # Parse before redaction so JSON Unicode escapes cannot hide an echo.
        result["body_text"] = json.dumps(_clean(decoded, secrets), ensure_ascii=False, allow_nan=False)
    except (ValueError, RecursionError):
        pass
    return result


def _report(records: list[dict]) -> dict:
    manifest, finish = records[0], records[-1]
    config, task = manifest["config"], manifest["task"]
    rows = records[1:-1]
    origin = manifest["origin"]
    response = rows[0] if rows else None
    if response and response["type"] == "public_example":
        artifact = _artifact(response["source"], task["format"], finished=True)
        observed = {"output": response["source"], "artifact": artifact, "usage": _usage(None),
                    "finish_reason": None, "reasoning_returned": False, "reasoning_characters": 0,
                    "error": None if artifact["format_valid"] else "invalid_artifact"}
    elif response:
        observed = _derive(response, task)
    else:
        observed = {"output": "", "artifact": _artifact("", task["format"], finished=False), "usage": _usage(None),
                    "finish_reason": None, "reasoning_returned": False, "reasoning_characters": 0, "error": "request_cancelled"}
    reason = finish["reason"]
    requests = int(response is not None and origin == "api_trial" and response["attempt_started"])
    return {"schema": SCHEMA, "kind": "showcase", "origin": origin, "public_source_demo": origin == "public_source_demo",
            "synthetic": False, "status": "cancelled" if reason == "cancelled" else "completed" if reason == "completed"
                else "partial" if reason in {"truncated", "invalid_output"} else "failed",
            "created_at": manifest["created_at"], "candidate": config["candidate"], "task": task,
            **{key: value for key, value in observed.items() if key != "reason"},
            "published_example": response.get("example") if response and origin == "public_source_demo" else None,
            "timing": {"elapsed_seconds": response.get("elapsed_seconds") if response and requests else None,
                       "headers_seconds": response.get("headers_seconds") if response and requests else None},
            "cost": {"request_count": requests, "real_requests": requests, "requested_output_tokens": requests * config["max_tokens"],
                     "maximum_requests": 1 if origin == "api_trial" else 0,
                     "maximum_requested_output_tokens": config["max_tokens"] if origin == "api_trial" else 0, "usage": observed["usage"]},
            "collection": {"completed_requests": requests, "cancelled": reason == "cancelled",
                           "stop_reason": reason, "stop_code": reason,
                           "failure": None if reason == "cancelled" else observed["error"]},
            "records_file": "showcase_records.jsonl",
            "interpretation": "公开作品与本次试作分别留档；图形外观不用于模型身份判定。"}


def _write(out: Path, report: dict) -> None:
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    escape = lambda value: re.sub(r"([\\`*_\[\]{}()#!|])", r"\\\1", str(value)).replace("<", "&lt;").replace(">", "&gt;")
    source = report["artifact"]["source"]
    fence = "`" * max(3, 1 + max((len(value) for value in re.findall(r"`{3,}", source)), default=0))
    lines = ["# API-MRI 经典题试作", "", escape(report["task"]["title"]), "",
             f"模型标签：{escape(report['candidate']['model'])}", "",
             "出处：" + ("已有公开作品" if report["public_source_demo"] else "用户主动发起的单次请求"), "",
             f"状态：{report['status']}；结束原因：{escape(report['collection']['stop_reason'])}。", "",
             f"实际请求：{report['cost']['request_count']}；申请输出配额：{report['cost']['requested_output_tokens']}。", "",
             report["interpretation"], "", "## 源码", "", fence + report["artifact"]["format"], source, fence, ""]
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")


def run_showcase(payload, out, progress=None, cancelled=None, *, transport=None):
    config, direct, env = _settings(payload)
    task = _task(config["task_id"])
    key = direct or os.environ.get(env or "")
    if not isinstance(key, str) or not 8 <= len(key) <= 4096 or any(not 33 <= ord(char) <= 126 for char in key):
        raise ValueError("API密钥缺失或无效。")
    secrets = (key, quote(key, safe=""))
    clean_config, clean_task = _clean(config, secrets), _clean(task, secrets)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    (out / "sampling_plan.json").write_text(json.dumps(_clean(_plan(config, task), secrets), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    records, path = [], out / "showcase_records.jsonl"
    _append(path, records, {"type": "manifest", "schema": SCHEMA, "origin": "api_trial", "config": clean_config,
            "task": clean_task, "created_at": datetime.now(timezone.utc).isoformat()})
    reason = "cancelled"
    if not cancelled or not cancelled():
        if progress:
            progress({"phase": "collecting", "completed": 0, "total": 1, "message": "发送一次经典题请求"})
        with httpx.Client(timeout=config["timeout"], follow_redirects=False, trust_env=False, transport=transport) as client:
            row = _redact_response(_request(client, config, key, task, cancelled), secrets)
        _append(path, records, row)
        reason = _derive(row, clean_task)["reason"]
    _append(path, records, {"type": "finish", "reason": reason})
    report = _report(records)
    _write(out, report)
    if progress:
        progress({"phase": "finished", "completed": int(len(records) == 3), "total": 1, "message": "试作记录已保存"})
    return report


def demo_showcase(out, task_id=None):
    catalog = list_showcase()
    if task_id is None:
        task_id = catalog["tasks"][0]["id"] if catalog["tasks"] else None
    task = _task(task_id)
    example = next((item for item in catalog["examples"] if item["task_id"] == task_id and item.get("asset")), None)
    if example is None:
        raise ValueError("该题没有可内置演示的授权作品，请查看来源链接。")
    source = showcase_asset(example["id"]).read_bytes().decode("utf-8")
    model = example.get("model", "作者未记录型号")
    if not isinstance(model, str):
        raise ValueError("公开作品的模型标签无效。")
    config, _, _ = _settings({"task_id": task_id, "candidate": {"api_base": "https://published.example.invalid/v1", "model": model}})
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    (out / "sampling_plan.json").write_text(json.dumps({"kind": "showcase", "task": task, "origin": "public_source_demo", "planned_requests": 0},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    records, path = [], out / "showcase_records.jsonl"
    _append(path, records, {"type": "manifest", "schema": SCHEMA, "origin": "public_source_demo", "config": config,
            "task": task, "created_at": datetime.now(timezone.utc).isoformat()})
    _append(path, records, {"type": "public_example", "example": example, "source": source,
            "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest()})
    _append(path, records, {"type": "finish", "reason": "completed" if _artifact(source, task["format"], finished=True)["format_valid"] else "invalid_output"})
    report = _report(records)
    _write(out, report)
    return report


def _validate_response(row: dict, config: dict, task: dict) -> None:
    required = {"type", "request", "attempt_started", "http_status", "response_headers", "body_text", "body_bytes_seen", "body_truncated",
                "transport_error", "elapsed_seconds", "headers_seconds", "sequence", "previous_hash", "record_hash"}
    if set(row) != required or row["request"] != _body(config, task):
        raise ValueError
    if row["http_status"] is not None and (type(row["http_status"]) is not int or not 100 <= row["http_status"] <= 599):
        raise ValueError
    if row["transport_error"] is not None and row["transport_error"] not in TRANSPORT_ERRORS:
        raise ValueError
    if row["http_status"] is None and row["transport_error"] is None:
        raise ValueError
    if type(row["attempt_started"]) is not bool:
        raise ValueError
    if not row["attempt_started"] and (row["http_status"] is not None or row["headers_seconds"] is not None
            or row["response_headers"] or row["body_text"] or row["body_bytes_seen"] != 0 or row["body_truncated"]
            or row["transport_error"] not in {"request_cancelled", "request_timeout"}):
        raise ValueError
    if (not isinstance(row["body_text"], str) or len(row["body_text"].encode("utf-8")) > MAX_RESPONSE_BYTES * 2
            or type(row["body_bytes_seen"]) is not int or not 0 <= row["body_bytes_seen"] <= MAX_RESPONSE_BYTES + 8192
            or type(row["body_truncated"]) is not bool
            or not isinstance(row["response_headers"], dict) or set(row["response_headers"]) - set(HEADER_NAMES)
            or any(not isinstance(value, str) or len(value) > 600 for value in row["response_headers"].values())):
        raise ValueError
    for key in ("headers_seconds", "elapsed_seconds"):
        value = row[key]
        if value is None and key == "headers_seconds":
            continue
        if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
            raise ValueError
    if row["headers_seconds"] is not None and row["headers_seconds"] > row["elapsed_seconds"]:
        raise ValueError
    if row["body_truncated"] != (row["transport_error"] == "response_size_limit"):
        raise ValueError
    if row["body_truncated"] and (row["body_text"] or row["body_bytes_seen"] <= MAX_RESPONSE_BYTES):
        raise ValueError
    if not row["body_truncated"] and row["body_bytes_seen"] > MAX_RESPONSE_BYTES:
        raise ValueError


def parse_showcase_records(text: str) -> list[dict]:
    try:
        if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_EVIDENCE_BYTES:
            raise ValueError
        records = [json.loads(line, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                   for line in text.lstrip("\ufeff").splitlines() if line.strip()]
        if len(records) not in {2, 3}:
            raise ValueError
        previous = None
        for number, row in enumerate(records):
            if not isinstance(row, dict) or type(row.get("sequence")) is not int or row["sequence"] != number or row.get("previous_hash") != previous:
                raise ValueError
            expected = content_hash({key: value for key, value in row.items() if key != "record_hash"})
            if row.get("record_hash") != expected:
                raise ValueError
            previous = expected
        manifest, finish = records[0], records[-1]
        if (set(manifest) != {"type", "schema", "origin", "config", "task", "created_at", "sequence", "previous_hash", "record_hash"}
                or manifest["type"] != "manifest" or manifest["schema"] != SCHEMA
                or manifest["origin"] not in {"api_trial", "public_source_demo"} or not _task_valid(manifest["task"], saved=True)
                or not isinstance(manifest["created_at"], str) or len(manifest["created_at"]) > 80):
            raise ValueError
        datetime.fromisoformat(manifest["created_at"].replace("Z", "+00:00"))
        config, direct, env = _settings(manifest["config"], saved=True)
        if direct is not None or env is not None or config != manifest["config"] or config["task_id"] != manifest["task"]["id"]:
            raise ValueError
        expected_reason = "cancelled"
        if len(records) == 3:
            row = records[1]
            if manifest["origin"] == "api_trial":
                if row.get("type") != "response":
                    raise ValueError
                _validate_response(row, config, manifest["task"])
                expected_reason = _derive(row, manifest["task"])["reason"]
            else:
                if (set(row) != {"type", "example", "source", "source_sha256", "sequence", "previous_hash", "record_hash"}
                        or row["type"] != "public_example" or not isinstance(row["source"], str)
                        or len(row["source"].encode("utf-8")) > MAX_RESPONSE_BYTES or not isinstance(row["example"], dict)
                        or row["example"].get("task_id") != config["task_id"]
                        or row["source_sha256"] != hashlib.sha256(row["source"].encode("utf-8")).hexdigest()):
                    raise ValueError
                expected_reason = "completed" if _artifact(row["source"], manifest["task"]["format"], finished=True)["format_valid"] else "invalid_output"
        elif manifest["origin"] != "api_trial":
            raise ValueError
        if set(finish) != {"type", "reason", "sequence", "previous_hash", "record_hash"} or finish["type"] != "finish" or finish["reason"] != expected_reason:
            raise ValueError
        _report(records)
        return records
    except (ValueError, KeyError, TypeError, IndexError, AttributeError, RecursionError, OverflowError):
        raise ValueError("经典题记录的格式、哈希或请求边界不完整。") from None


def replay_showcase(records, out):
    text = records.read_text(encoding="utf-8") if isinstance(records, Path) else records
    if not isinstance(text, str):
        text = "\n".join(json.dumps(row, ensure_ascii=False, allow_nan=False) for row in text)
    rows = parse_showcase_records(text)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    (out / "showcase_records.jsonl").write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")
    plan = (_plan(rows[0]["config"], rows[0]["task"]) if rows[0]["origin"] == "api_trial" else
            {"kind": "showcase", "task": rows[0]["task"], "origin": "public_source_demo", "planned_requests": 0})
    (out / "sampling_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = _report(rows)
    _write(out, report)
    return report
