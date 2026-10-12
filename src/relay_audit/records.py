"""Small, versioned evidence format independent of Inspect log internals."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def content_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class AuditRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0.1"] = "0.1"
    run_id: str = Field(min_length=1)
    endpoint_id: str = Field(min_length=1)
    prompt_id: str = Field(min_length=1)
    replicate: int = Field(ge=0, strict=True)
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_declared: str = Field(min_length=1)
    reference_kind: Literal["official", "third_party", "synthetic_control", "unknown"]
    synthetic: bool
    status: Literal["success", "error"]
    response: str | None = None
    error: str | None = None
    elapsed_seconds: float | None = Field(default=None, ge=0)
    usage: dict[str, int | None] = Field(default_factory=dict)
    source_log: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_outcome(self) -> "AuditRecord":
        if self.status == "success" and self.response is None:
            raise ValueError("A successful record must contain a response (including empty text).")
        if self.status == "error" and not self.error:
            raise ValueError("An error record must explain its failure.")
        return self


def save_records(path: Path, records: list[AuditRecord]) -> None:
    """Never overwrite an existing evidence file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(record.model_dump_json() + "\n")


def load_records(path: Path) -> list[AuditRecord]:
    records: list[AuditRecord] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                records.append(AuditRecord.model_validate_json(line))
            except ValueError as exc:
                raise ValueError(f"{path.name}:{line_number}: invalid evidence record") from exc
    if not records:
        raise ValueError("Evidence file has no records.")
    return records


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
