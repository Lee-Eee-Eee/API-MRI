"""Collect evidence with Inspect, without implementing a second request runner.

Fixture answers are synthetic categorical draws, not outputs of a real LLM.
Endpoint collection makes real requests only when explicitly invoked by a caller.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import random
import re
from datetime import timezone
from importlib.metadata import version
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlsplit

import anyio
from inspect_ai import Task, eval_async
from inspect_ai.dataset import Sample
from inspect_ai.event import ModelEvent
from inspect_ai.log import EvalLog, read_eval_log, write_eval_log
from inspect_ai.model import GenerateConfig, ModelOutput, ModelUsage, get_model
from inspect_ai.scorer import exact
from inspect_ai.solver import generate
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .records import AuditRecord, content_hash, file_hash


FIXTURE_MODEL = "research-fixture/declared-a"
FIXTURE_PROMPTS = (
    ("color_blue", "Synthetic test: return only the word blue.", "blue"),
    ("color_green", "Synthetic test: return only the word green.", "green"),
    ("color_amber", "Synthetic test: return only the word amber.", "amber"),
)
_USAGE_FIELDS = (
    "input_tokens", "output_tokens", "total_tokens", "input_tokens_cache_read",
    "input_tokens_cache_write", "reasoning_tokens",
)
_SECRET_FIELDS = {"authorization", "proxyauthorization", "apikey", "xapikey", "accesskey", "token", "accesstoken", "refreshtoken"}


class _Prompt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    input: str = Field(min_length=1)


class _EndpointConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint_id: str = Field(min_length=1)
    model_declared: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    api_key_env: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    reference_kind: Literal["official", "third_party", "unknown"] = "unknown"
    prompts: list[_Prompt] = Field(min_length=1)
    repeats: int = Field(default=1, ge=1, le=10000, strict=True)
    max_tokens: int = Field(default=64, ge=1, le=100000, strict=True)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    timeout: int = Field(default=30, ge=1, le=3600, strict=True)
    attempt_timeout: int = Field(default=15, ge=1, le=3600, strict=True)

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError:
            raise ValueError("base_url must be an absolute HTTP(S) URL") from None
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or re.search(r"\s", value)
            or port == 0
        ):
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
            raise ValueError("base_url cannot contain userinfo, query parameters, or fragments")
        return value.rstrip("/")

    @model_validator(mode="after")
    def unique_prompt_ids(self) -> "_EndpointConfig":
        if len({prompt.id for prompt in self.prompts}) != len(self.prompts):
            raise ValueError("prompt IDs must be unique")
        if self.attempt_timeout > self.timeout:
            raise ValueError("attempt_timeout cannot exceed timeout")
        return self


def _generation_config(*, max_tokens: int = 64, temperature: float = 0.0,
                       timeout: int = 30, attempt_timeout: int = 15) -> GenerateConfig:
    return GenerateConfig(
        max_tokens=max_tokens, temperature=temperature, max_connections=1,
        adaptive_connections=False, max_retries=0, timeout=timeout,
        attempt_timeout=attempt_timeout,
    )


def _config_hash(config: GenerateConfig) -> str:
    # Endpoint names, URL, credentials, fixture seed and repetition count are
    # study metadata, not parameters of the model request being compared.
    return content_hash({
        "generation": config.model_dump(mode="json", exclude_none=True),
        "solver": "single_turn_generate", "cache": False,
        "responses_api": False, "stream": False, "sdk_max_retries": 0,
    })


def _prompt_hash(text: str) -> str:
    return content_hash({"messages": [{"role": "user", "content": text}], "tools": []})


def _fixture_callable(variant: Literal["honest", "substituted"], seed: int):
    rng = random.Random(seed)
    targets = {text: target for _, text, target in FIXTURE_PROMPTS}

    def output(input, tools, tool_choice, config):
        target = targets[input[-1].text]
        if variant == "honest":
            answer = rng.choices([target, target.upper(), f"{target}."], [0.8, 0.1, 0.1])[0]
        else:
            answer = rng.choice(["violet", "copper", "magenta"])
        result = ModelOutput.from_content(FIXTURE_MODEL, answer)
        # Explicit invented usage avoids first-run tokenizer downloads. These
        # counts only exercise evidence plumbing and have no billing meaning.
        result.usage = ModelUsage(input_tokens=12, output_tokens=2, total_tokens=14)
        result.metadata = {"synthetic_usage": True}
        return result

    return output


async def _execute(task: Task, model_name: str, config: GenerateConfig,
                   log_dir: Path, repeats: int, model_kwargs: dict[str, Any]) -> list[EvalLog]:
    async with get_model(model_name, config=config, memoize=False, **model_kwargs) as model:
        return await eval_async(
            task, model=model, log_dir=str(log_dir), log_format="eval",
            epochs=repeats, max_samples=1, max_tasks=1, fail_on_error=False,
            continue_on_fail=True, retry_on_error=0, task_retry_attempts=0,
            log_samples=True, log_model_api=True, log_images=False,
            log_realtime=False, log_shared=False, notification=False,
            acp_server=False, ctl_server=False, log_level="critical",
            log_level_transcript="critical", score=bool(task.scorer),
            **config.model_dump(exclude_none=True),
        )


def _run(task: Task, model_name: str, config: GenerateConfig, log_dir: Path,
         repeats: int, model_kwargs: dict[str, Any]) -> list[EvalLog]:
    if log_dir.exists() and any(log_dir.iterdir()):
        raise ValueError("Inspect log directory must be new or empty")
    log_dir.mkdir(parents=True, exist_ok=True)
    # The public entries are synchronous CLI functions. Suppress upstream
    # console text because error bodies can echo credentials. The sanitized
    # Inspect log and AuditRecord, rather than terminal text, are the evidence.
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return anyio.run(_execute, task, model_name, config, log_dir, repeats, model_kwargs)
    except Exception as exc:
        raise RuntimeError(f"Inspect collection failed ({type(exc).__name__}); raw error omitted") from None
    finally:
        key = model_kwargs.get("api_key")
        if key:
            _sanitize_directory(log_dir, key)


def _redact(value: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if re.sub(r"[^a-z]", "", str(key).lower()) in _SECRET_FIELDS
            else _redact(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "[REDACTED]")
        return value
    return value


def _sanitize_directory(log_dir: Path, key: str) -> None:
    """Sanitize own logs on success, failure or interruption, before returning."""
    root = log_dir.resolve()
    for source in root.rglob("*.eval"):
        if not source.resolve().is_relative_to(root):
            raise RuntimeError("Unexpected log path; export refused")
        try:
            loaded = read_eval_log(source, resolve_attachments=True)
            data = _redact(loaded.model_dump(mode="json"), (key, quote(key, safe="")))
            sanitized = EvalLog.model_validate(data)
            write_eval_log(sanitized, location=source, format="eval")
            if key in read_eval_log(source, resolve_attachments=True).model_dump_json():
                raise RuntimeError("Sanitation could not be verified")
        except BaseException:
            # This directory was verified new/empty before this run. An
            # incomplete log cannot safely be shared; retain a plain marker
            # instead of an unverified credential-bearing archive.
            source.write_bytes(b"Incomplete Inspect log discarded: credential sanitation could not be verified.\n")
            raise RuntimeError("Log sanitation failed; incomplete archive discarded, export refused") from None


def _read_sanitized_log(log: EvalLog, key: str | None) -> EvalLog:
    source = Path(log.location).resolve()
    loaded = read_eval_log(source, resolve_attachments=True)
    if key is not None:
        # Retain the original native format through Inspect's public reader and
        # writer, redacting all known key occurrences (including error bodies).
        data = _redact(loaded.model_dump(mode="json"), (key, quote(key, safe="")))
        loaded = EvalLog.model_validate(data)
        loaded.location = str(source)
        write_eval_log(loaded, location=source, format="eval")
        loaded = read_eval_log(source, resolve_attachments=True)
        if key in loaded.model_dump_json():
            raise RuntimeError("Credential redaction failed; evidence cannot be exported")
    return loaded


def _records(logs: list[EvalLog], *, endpoint_id: str, model_declared: str,
             reference_kind: str, synthetic: bool, prompts: list[_Prompt],
             config: GenerateConfig, repeats: int, metadata: dict[str, Any],
             key: str | None = None) -> list[AuditRecord]:
    prompt_inputs = {prompt.id: prompt.input for prompt in prompts}
    records: list[AuditRecord] = []
    for original in logs:
        log = _read_sanitized_log(original, key)
        source_hash = file_hash(Path(original.location).resolve())
        if not log.samples:
            raise RuntimeError("Inspect produced no sample evidence")
        for sample in log.samples:
            prompt_id = str(sample.id)
            if prompt_id not in prompt_inputs:
                raise RuntimeError("Inspect log contains an unexpected prompt ID")
            events = [event for event in sample.events if isinstance(event, ModelEvent)]
            output = sample.output
            failed = sample.error is not None or output is None or any(event.error for event in events)
            usage = output.usage if output is not None else None
            elapsed = output.time if output is not None else None
            if elapsed is None and events and events[-1].completed is not None:
                elapsed = (events[-1].completed - events[-1].timestamp).total_seconds()
            item_metadata = {
                **metadata,
                "expected_prompt_ids": list(prompt_inputs), "expected_repeats": repeats,
                "inspect_epoch": sample.epoch,
                "source_log_sha256": source_hash,
                "inspect_version": version("inspect-ai"),
                "collection_schema_version": "0.1",
                "call_started_at_utc": events[0].timestamp.astimezone(timezone.utc).isoformat() if events and events[0].timestamp.tzinfo else None,
                "call_completed_at_utc": events[-1].completed.astimezone(timezone.utc).isoformat() if events and events[-1].completed and events[-1].completed.tzinfo else None,
                "model_reported": output.model if output is not None else None,
                "response_id": output.response_id if output is not None else None,
                # Inspect 0.3.278 initializes retries to None and increments it
                # only in log_model_retry (log/_samples.py). Preserve the raw
                # value while normalizing the no-retry state for analysis.
                "inspect_retries": [0 if event.retries is None else event.retries for event in events],
                "inspect_retries_raw": [event.retries for event in events],
                "inspect_retry_count_basis": "Inspect 0.3.278 retry-event counter: None means no recorded retry; SDK and Inspect retries disabled",
                "usage_origin": "synthetic_counts" if synthetic else "provider_reported_or_missing",
                "elapsed_basis": "Inspect client-observed model call time; not server inference time",
                "source_log_credential_redaction": key is not None,
                "error_message_policy": "generic record category; known credentials redacted in source log",
                "inspect_scores": {name: score.value for name, score in (sample.scores or {}).items()},
            }
            records.append(AuditRecord(
                run_id=log.eval.run_id, endpoint_id=endpoint_id, prompt_id=prompt_id,
                replicate=sample.epoch - 1, prompt_hash=_prompt_hash(prompt_inputs[prompt_id]),
                config_hash=_config_hash(config), model_declared=model_declared,
                reference_kind=reference_kind, synthetic=synthetic,
                status="error" if failed else "success",
                response=None if failed or output is None else output.completion,
                error="inspect_sample_error; raw upstream message omitted" if failed else None,
                elapsed_seconds=max(0.0, elapsed) if elapsed is not None else None,
                usage={name: getattr(usage, name, None) for name in _USAGE_FIELDS} if usage is not None else {},
                source_log=str(Path(original.location).resolve()), metadata=item_metadata,
            ))
    return sorted(records, key=lambda record: (record.prompt_id, record.replicate))


def collect_fixture(endpoint_id: str, variant: Literal["honest", "substituted"], *,
                    repeats: int = 12, seed: int = 11, log_dir: Path) -> list[AuditRecord]:
    """Run synthetic categorical controls through a real Inspect evaluation.

    The word ``honest`` names a known synthetic control, not a verdict about an
    API vendor. Different seeds change local draws and leave config_hash equal.
    """
    if not isinstance(endpoint_id, str) or not endpoint_id.strip():
        raise ValueError("endpoint_id must be nonempty")
    if variant not in {"honest", "substituted"}:
        raise ValueError("variant must be honest or substituted")
    if isinstance(repeats, bool) or not isinstance(repeats, int) or not 1 <= repeats <= 10000:
        raise ValueError("repeats must be an integer from 1 to 10000")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    prompts = [_Prompt(id=identifier, input=text) for identifier, text, _ in FIXTURE_PROMPTS]
    task = Task(
        name="relay_audit_synthetic_fixture",
        dataset=[Sample(id=identifier, input=text, target=target) for identifier, text, target in FIXTURE_PROMPTS],
        solver=generate(cache=False), scorer=exact(),
        metadata={"synthetic_backend": "Inspect mockllm; no real LLM", "fixture_variant": variant},
    )
    config = _generation_config()
    logs = _run(task, f"mockllm/{FIXTURE_MODEL}", config, Path(log_dir), repeats,
                {"custom_outputs": _fixture_callable(variant, seed)})
    return _records(
        logs, endpoint_id=endpoint_id, model_declared=FIXTURE_MODEL,
        reference_kind="synthetic_control", synthetic=True, prompts=prompts,
        config=config, repeats=repeats,
        metadata={"backend": "inspect_mockllm", "fixture_variant": variant, "fixture_seed": seed,
                  "limitations": ["Synthetic categorical draws, not real LLM responses or identity evidence.",
                                  "Mock model-call snapshots are synthetic and are not HTTP payloads."]},
    )


def collect_endpoint(config_path: Path, log_dir: Path) -> list[AuditRecord]:
    """Invoke an explicitly configured OpenAI-compatible endpoint via Inspect.

    This function can incur provider charges. It is not used by the synthetic
    demo. The API key value must already exist under the configured env name.
    The archived source log is sanitized after the run, before record export;
    it can still contain user prompt and response content, so use safe inputs.
    """
    try:
        parsed = json.loads(Path(config_path).read_text(encoding="utf-8"))
        settings = _EndpointConfig.model_validate(parsed)
    except (OSError, ValueError) as exc:
        raise ValueError(f"Invalid endpoint configuration ({type(exc).__name__}); raw values omitted") from None
    api_key = os.environ.get(settings.api_key_env)
    if not api_key or len(api_key) < 8:
        raise ValueError("Configured credential environment variable is missing or shorter than 8 characters")
    # Do not accept endpoint-selected options that emulate tools, silently
    # choose Responses API, stream, enable caches, or turn retries back on.
    config = _generation_config(max_tokens=settings.max_tokens, temperature=settings.temperature,
                                timeout=settings.timeout, attempt_timeout=settings.attempt_timeout)
    task = Task(
        name="relay_audit_endpoint_collection",
        dataset=[Sample(id=prompt.id, input=prompt.input) for prompt in settings.prompts],
        solver=generate(cache=False),
        metadata={"endpoint_id": settings.endpoint_id, "protocol": "openai_chat_completions"},
    )
    logs = _run(
        task, f"openai-api/relay/{settings.model_declared}", config, Path(log_dir), settings.repeats,
        {"base_url": settings.base_url, "api_key": api_key, "responses_api": False,
         "stream": False, "max_retries": 0},
    )
    return _records(
        logs, endpoint_id=settings.endpoint_id, model_declared=settings.model_declared,
        reference_kind=settings.reference_kind, synthetic=False,
        prompts=settings.prompts, config=config, repeats=settings.repeats, key=api_key,
        metadata={"backend": "inspect_openai_compatible", "protocol": "openai_chat_completions",
                  "base_url": settings.base_url,
                  "limitations": ["Client request and provider response logs do not verify upstream HTTP forwarding or model weights.",
                                  "Response model, IDs and usage are provider supplied; no upstream authenticity guarantee.",
                                  "No tool emulation, streaming, Responses API or context-window claim is tested."]},
    )
