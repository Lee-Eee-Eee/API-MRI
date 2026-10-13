"""Bounded, interleaved pair collection with recoverable evidence checkpoints."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Literal
from urllib.parse import quote
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator, model_validator

from .collect import _EndpointConfig, _Prompt, _redact, _sanitize_directory, collect_endpoint_config
from .records import AuditRecord, file_hash
from .report import write_report
from .stats import compare_records

Progress = Callable[[dict[str, Any]], None]
Cancelled = Callable[[], bool]


class _PairEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    endpoint_id: str = Field(min_length=1, max_length=80)
    model_declared: str = Field(min_length=1, max_length=200)
    base_url: str = Field(min_length=1, max_length=2048)
    api_key: SecretStr | None = Field(default=None, exclude=True)
    api_key_env: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=128)
    reference_kind: Literal["official", "third_party", "unknown"] = "unknown"

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return _EndpointConfig.validate_url(value)

    @field_validator("endpoint_id", "model_declared")
    @classmethod
    def validate_label(cls, value: str) -> str:
        if not value.strip() or any(ord(character) < 32 for character in value):
            raise ValueError("labels must be nonempty single-line text")
        return value.strip()

    @field_validator("api_key")
    @classmethod
    def validate_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            secret = value.get_secret_value()
            if not 8 <= len(secret) <= 4096 or re.search(r"\s", secret):
                raise ValueError("API key must contain 8 to 4096 non-whitespace characters")
        return value


class _PairSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reference: _PairEndpoint
    candidate: _PairEndpoint
    prompts: list[_Prompt] = Field(min_length=1, max_length=20)
    repeats: int = Field(default=12, ge=1, le=100)
    max_tokens: int = Field(default=128, ge=1, le=4096)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0, allow_inf_nan=False)
    timeout: int = Field(default=30, ge=1, le=600)
    attempt_timeout: int = Field(default=15, ge=1, le=600)
    permutations: int = Field(default=199, ge=19, le=9999)
    seed: int = Field(default=11, ge=0, lt=2**63)
    alpha: float = Field(default=0.05, gt=0.0, lt=1.0, allow_inf_nan=False)
    min_samples: int = Field(default=8, ge=2, le=100)

    @model_validator(mode="after")
    def validate_plan(self) -> "_PairSettings":
        if self.reference.model_declared != self.candidate.model_declared:
            raise ValueError("reference and candidate must declare the same model")
        if len({prompt.id for prompt in self.prompts}) != len(self.prompts):
            raise ValueError("prompt IDs must be unique")
        if any(not prompt.id.strip() or not prompt.input.strip() for prompt in self.prompts):
            raise ValueError("prompts and IDs must not be blank")
        if self.attempt_timeout > self.timeout:
            raise ValueError("attempt_timeout cannot exceed timeout")
        requests = len(self.prompts) * self.repeats * 2
        if requests > 1000:
            raise ValueError("plan exceeds the 1000-request limit")
        if requests * self.max_tokens > 250000:
            raise ValueError("plan exceeds the 250000 maximum-output-token limit")
        if sum(len(prompt.input) for prompt in self.prompts) > 64000:
            raise ValueError("combined prompts exceed 64000 characters")
        return self


def _parse_pair(payload: dict[str, Any]) -> _PairSettings:
    try:
        return _PairSettings.model_validate(payload)
    except ValidationError as exc:
        # Pydantic normally includes offending input values. Keys and prompt
        # contents must never become part of a web error or terminal message.
        known_fields = set(_PairSettings.model_fields) | set(_PairEndpoint.model_fields) | set(_Prompt.model_fields) | {"kind", "expected"}
        issues = []
        for error in exc.errors(include_input=False, include_context=False)[:4]:
            # Unknown object member names can themselves contain a secret.
            location = ".".join(str(part) if isinstance(part, int) or part in known_fields else "unknown_field"
                                for part in error["loc"]) or "plan"
            issues.append(f"{location}: {error['msg']}")
        raise ValueError("Invalid pair configuration; " + "; ".join(issues) + "; raw values omitted") from None
    except (ValueError, TypeError):
        raise ValueError("Invalid pair configuration; raw values omitted") from None


def _plan(settings: _PairSettings) -> dict[str, Any]:
    count = len(settings.prompts) * settings.repeats * 2
    plan = {
        "request_count": count,
        "max_output_tokens_total": count * settings.max_tokens,
        "output_tokens_note": "Maximum requested output tokens only; input/reasoning/provider billing can differ.",
        "reference": settings.reference.model_dump(exclude_none=True),
        "candidate": settings.candidate.model_dump(exclude_none=True),
        "prompts_count": len(settings.prompts), "repeats": settings.repeats,
        "max_tokens": settings.max_tokens,
        "ordering": "For each prompt, reference then candidate in even rounds; candidate then reference in odd rounds.",
    }
    provided = tuple(endpoint.api_key.get_secret_value() for endpoint in (settings.reference, settings.candidate)
                     if endpoint.api_key is not None)
    return _redact(plan, provided)


def plan_pair(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate an entire request plan without network, files or key lookup."""
    return _plan(_parse_pair(payload))


def _credentials(settings: _PairSettings) -> dict[str, str]:
    keys = {}
    for role in ("reference", "candidate"):
        endpoint = getattr(settings, role)
        key = endpoint.api_key.get_secret_value() if endpoint.api_key is not None else os.environ.get(endpoint.api_key_env or "")
        if not isinstance(key, str) or not 8 <= len(key) <= 4096 or re.search(r"\s", key):
            raise ValueError(f"{role}: API key is missing or invalid; provide an in-memory key or configured environment variable")
        keys[role] = key
    return keys


def _progress(callback: Progress | None, **event: Any) -> None:
    if callback:
        callback(event)


def run_pair(payload: dict[str, Any], out: Path, progress: Progress | None = None,
             cancelled: Cancelled | None = None) -> dict[str, Any]:
    """Collect one request at a time and checkpoint every completed request.

    Cancellation stops before the next request. A currently running request
    finishes or times out; its evidence is retained. All exports omit keys.
    """
    settings = _parse_pair(payload)
    keys = _credentials(settings)
    secrets = tuple(dict.fromkeys([*keys.values(), *(quote(value, safe="") for value in keys.values())]))
    output = Path(out)
    if output.exists():
        raise FileExistsError("Output directory already exists; choose a new directory")
    output.mkdir(parents=True, exist_ok=False)
    identifier = uuid4().hex
    plan = _redact({**_plan(settings), "job_id": identifier,
                    "prompts": [prompt.model_dump(exclude_none=True) for prompt in settings.prompts],
                    "generation": {key: getattr(settings, key) for key in ("max_tokens", "temperature", "timeout", "attempt_timeout")},
                    "analysis": {key: getattr(settings, key) for key in ("permutations", "seed", "alpha", "min_samples")}}, secrets)
    with (output / "sampling_plan.json").open("x", encoding="utf-8") as stream:
        json.dump(plan, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    paths = {role: output / f"{role}.jsonl" for role in ("reference", "candidate")}
    for path in paths.values():
        path.touch(exist_ok=False)
    rows: dict[str, list[AuditRecord]] = {"reference": [], "candidate": []}
    completed, total = 0, plan["request_count"]
    was_cancelled = False
    failure: str | None = None
    _progress(progress, completed=completed, total=total, phase="collecting", message="准备采集；每次请求后保存证据。")
    for replicate in range(settings.repeats):
        order = ("reference", "candidate") if replicate % 2 == 0 else ("candidate", "reference")
        for prompt in settings.prompts:
            for role in order:
                if cancelled and cancelled():
                    was_cancelled = True
                    break
                endpoint = getattr(settings, role)
                batch_dir = output / "inspect" / f"{completed:04d}_{role}"
                config = {
                    **endpoint.model_dump(exclude_none=True),
                    "prompts": [prompt.model_dump(exclude_none=True)], "repeats": 1,
                    **{name: getattr(settings, name) for name in ("max_tokens", "temperature", "timeout", "attempt_timeout")},
                }
                try:
                    batch = collect_endpoint_config(config, batch_dir, api_key=keys[role])
                    # A server can echo either role's key. Sanitize both keys
                    # across native logs before computing final source hashes.
                    for key in dict.fromkeys(keys.values()):
                        _sanitize_directory(batch_dir, key)
                    if len(batch) != 1:
                        raise RuntimeError("Unexpected number of records in a single-request batch")
                    row = batch[0]
                    row.metadata.update({
                        "inspect_run_id": row.run_id,
                        "expected_prompt_ids": [item.id for item in settings.prompts],
                        "expected_repeats": settings.repeats,
                        "collection_schema_version": "0.2", "sampling_job_id": identifier,
                        "collection_order": completed, "collection_role": role,
                        "source_log_sha256": file_hash(Path(row.source_log)),
                    })
                    if prompt.check is not None:
                        row.metadata["probe_check"] = prompt.check.model_dump()
                    row.run_id = f"{identifier}:{role}"
                    row.replicate = replicate
                    row = AuditRecord.model_validate(_redact(row.model_dump(), secrets))
                    with paths[role].open("a", encoding="utf-8", newline="\n") as stream:
                        stream.write(row.model_dump_json() + "\n")
                        stream.flush()
                        os.fsync(stream.fileno())
                    rows[role].append(row)
                    completed += 1
                except Exception as exc:
                    # Even if collection failed after producing a valid log,
                    # remove credentials belonging to the other endpoint too.
                    for key in dict.fromkeys(keys.values()):
                        try:
                            _sanitize_directory(batch_dir, key)
                        except Exception:
                            pass  # The sanitizer replaces unsafe archives with a marker.
                    failure = f"collection_failed:{type(exc).__name__}"
                    # Preserve already checkpointed evidence and a generic
                    # failure category. Do not expose an upstream error body.
                    break
                _progress(progress, completed=completed, total=total, phase="collecting",
                          message=f"已完成 {completed}/{total} 次请求")
            if was_cancelled or failure:
                break
        if was_cancelled or failure:
            break
    _progress(progress, completed=completed, total=total, phase="analyzing", message="正在汇总已保存的证据。")
    result = compare_records(rows["reference"], rows["candidate"],
                             **{name: getattr(settings, name) for name in ("permutations", "seed", "alpha", "min_samples")})
    complete = completed == total
    if not complete:
        result["status"] = "insufficient_evidence"
        reason = "collection_cancelled" if was_cancelled else "collection_incomplete"
        result["reasons"] = list(dict.fromkeys([*result.get("reasons", []), reason, "planned_collection_not_completed"]))
        result["coverage"]["complete_observed_family"] = False
    result["workflow"] = "pair"
    result["collection"] = {"completed": completed, "total": total, "complete": complete,
                             "cancelled": was_cancelled, "failure": failure, "job_id": identifier,
                             "ordering": plan["ordering"], "sampling_plan_file": "sampling_plan.json"}
    result = write_report(result, output, reference=paths["reference"], candidate=paths["candidate"])
    _progress(progress, completed=completed, total=total,
              phase="cancelled" if was_cancelled else "failed" if failure else "complete",
              message="采集已停止，已保存部分证据。" if not complete else "采集和报告已完成。")
    return result
