"""Bounded, data-only updates from the allowlisted KBF reference repository.

Downloaded records are not independently authenticated model observations.
The local KBF implementation remains pinned; incompatible data is skipped.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import time
from typing import Callable
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from .vendor.kbf import kbf_common as kbf

BUNDLED_DIR = Path(__file__).parent / "data" / "reference_packs"
UPSTREAM_REPO = "Ooo0ption/KBF"
PINNED_COMMIT = "68aefbbab879469152aaac9fd988a86c30ee4739"
API_ROOT = "https://api.github.com/repos/Ooo0ption/KBF"
RAW_ROOT = "https://raw.githubusercontent.com/Ooo0ption/KBF"
REFERENCE_PREFIX = "probes/reference_202609/"
MAX_PACK_BYTES = 1024 * 1024
MAX_CATALOG_BYTES = 2 * 1024 * 1024
MAX_TREE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_PACKS = 100
MAX_UPDATE_SECONDS = 90
HTTP_TIMEOUT = 15
LICENSE_SHA256 = "0485fff26320cac0547cdd92fc503066869f02110ef5914f121c7a50b8cb1467"
PINNED_SOURCE_BLOBS = {
    "scripts/domains.py": "e281ae4f167f8ee6a164d4477d1b93bc293a300f",
    "scripts/kbf_common.py": "bc22366811d41d9ebf1846b7c9bfbb742b8d6b54",
    "scripts/kbf_test.py": "e62b9ad11e61fb23ab5c4c923b5202378e12fab4",
    "scripts/protocols.py": "aa3e154146c1579a1ca32eaa75b9cd507115307a",
}
_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,240}\.json\Z")
_REFERENCE_PATH = re.compile(r"probes/reference_20\d{2}(?:0[1-9]|1[0-2])/([A-Za-z0-9][A-Za-z0-9_.-]{0,180}\.json)\Z")
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class ReferenceValidationError(ValueError):
    """A data pack cannot be reproduced by the pinned local implementation."""


class ReferenceFetchError(ValueError):
    """Safe, credential-free explanation of a failed upstream request."""


class UpdateStopped(ReferenceFetchError):
    pass


def _json(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise ReferenceValidationError("invalid_json") from None


def _text(value, field: str, limit: int = 300) -> str:
    if (not isinstance(value, str) or not value or len(value) > limit
            or any(ord(c) < 32 for c in value)):
        raise ReferenceValidationError(f"invalid_{field}")
    return value


def _date(value, field: str) -> str:
    text = _text(value, field, 64)
    try:
        datetime.fromisoformat(text)
    except ValueError:
        raise ReferenceValidationError(f"invalid_{field}") from None
    return text


def _finite(value, nullable: bool = False) -> bool:
    try:
        return (value is None and nullable) or (type(value) in {float, int} and math.isfinite(value) and abs(value) <= 1e100)
    except OverflowError:
        return False


def _bounded_values(data) -> None:
    """Reject hidden non-finite values and excessive nesting in otherwise valid JSON."""
    stack, seen = [(data, 0)], 0
    while stack:
        item, depth = stack.pop()
        seen += 1
        if seen > 100000 or depth > 30:
            raise ReferenceValidationError("excessive_structure")
        if type(item) is float and not math.isfinite(item):
            raise ReferenceValidationError("non_finite_number")
        if isinstance(item, dict):
            if any(not isinstance(k, str) or len(k) > 500 for k in item):
                raise ReferenceValidationError("invalid_object_key")
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            stack.extend((v, depth + 1) for v in item)
        elif isinstance(item, str) and len(item) > 100000:
            raise ReferenceValidationError("excessive_string")


def validate_reference_bytes(raw: bytes, *, filename: str, upstream_commit: str,
                             source_path: str | None = None, git_blob_sha: str | None = None,
                             accessed_on: str | None = None) -> tuple[dict, dict]:
    """Validate the raw KBF format and reproduce its recorded self-test."""
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_PACK_BYTES:
        raise ReferenceValidationError("pack_size_limit")
    if not isinstance(filename, str) or not _FILENAME.fullmatch(filename) or ".." in filename:
        raise ReferenceValidationError("unsafe_filename")
    if not isinstance(upstream_commit, str) or not _HEX40.fullmatch(upstream_commit):
        raise ReferenceValidationError("invalid_commit")
    path = source_path or REFERENCE_PREFIX + filename
    if not _allowed_path(path):
        raise ReferenceValidationError("unsupported_source_path")
    if git_blob_sha is not None:
        if not _HEX40.fullmatch(git_blob_sha) or git_blob_hash(raw) != git_blob_sha:
            raise ReferenceValidationError("git_blob_hash_mismatch")
    data = _json(raw)
    if not isinstance(data, dict):
        raise ReferenceValidationError("invalid_pack_object")
    _bounded_values(data)
    model = _text(data.get("reference_model"), "reference_model", 200)
    generated = _date(data.get("timestamp"), "generated_at")
    if data.get("numbering_format") != "(N)":
        raise ReferenceValidationError("unsupported_prompt_numbering")
    endpoint = data.get("reference_endpoint")
    if endpoint is not None:
        if not isinstance(endpoint, dict) or endpoint.get("protocol") != "openai-chat":
            raise ReferenceValidationError("unsupported_reference_protocol")
        base = _text(endpoint.get("api_base"), "reference_endpoint", 500)
        parts = urlsplit(base)
        if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username
                or parts.password or parts.query or parts.fragment):
            raise ReferenceValidationError("invalid_reference_endpoint")
    probes = data.get("probes")
    if not isinstance(probes, list) or not 1 <= len(probes) <= 2000:
        raise ReferenceValidationError("invalid_probe_count")
    if type(data.get("total_probes")) is not int or data["total_probes"] != len(probes):
        raise ReferenceValidationError("probe_counter_mismatch")
    for probe in probes:
        if not isinstance(probe, dict):
            raise ReferenceValidationError("invalid_probe")
        _text(probe.get("name"), "probe_name", 500)
        if not isinstance(probe.get("domain"), str) or probe["domain"] not in kbf.DOMAIN_CLOZE:
            raise ReferenceValidationError("unsupported_domain")
        if not _finite(kbf.probe_consensus(probe)):
            raise ReferenceValidationError("invalid_reference_value")
        for key in ("value", "cloze_consensus", "contrast_value"):
            if key in probe and not _finite(probe[key], nullable=True):
                raise ReferenceValidationError("invalid_probe_number")
    target_results = data.get("target_results")
    self_test = target_results.get(model.split("/")[-1]) if isinstance(target_results, dict) else None
    if not isinstance(self_test, dict) or self_test.get("model") != model or self_test.get("is_self") is not True:
        raise ReferenceValidationError("missing_reference_self_test")
    settings = self_test.get("test_config")
    if (not isinstance(settings, dict) or set(settings) != {"sys", "temp"}
            or settings["sys"] != kbf.TEST_SYS_PROMPT or not _finite(settings["temp"])
            or settings["temp"] != kbf.TEST_TEMP):
        raise ReferenceValidationError("unsupported_self_test_settings")
    values = self_test.get("values")
    if not isinstance(values, list) or len(values) != len(probes) or any(not _finite(v, nullable=True) for v in values):
        raise ReferenceValidationError("invalid_self_test_values")
    actual = [None if value is None else int(kbf.check_match(kbf.probe_consensus(probe), value, probe["domain"]))
              for probe, value in zip(probes, values)]
    total, hamming = sum(v is not None for v in actual), sum(v == 0 for v in actual)
    for field, expected in (("total", total), ("hamming", hamming), ("correct", total - hamming)):
        if type(self_test.get(field)) is not int or self_test[field] != expected:
            raise ReferenceValidationError(f"self_test_{field}_mismatch:recorded={self_test.get(field)},recomputed={expected}")
    if total < max(1, math.ceil(len(probes) * 0.5)):
        raise ReferenceValidationError("insufficient_reference_self_coverage")
    vector = self_test.get("match_vector")
    if not isinstance(vector, list) or len(vector) != len(probes) or vector != actual:
        raise ReferenceValidationError("self_test_match_vector_mismatch")
    calibrated = _date(self_test.get("timestamp"), "calibrated_at")
    providers = data.get("provider_map", {})
    if not isinstance(providers, dict):
        raise ReferenceValidationError("invalid_provider_map")
    provider = providers.get(model, "未记录")
    _text(provider, "provider", 200)
    metadata = {
        "id": filename[:-5], "label": model.split("/")[-1], "model": model, "provider": provider,
        "generated_at": generated, "calibrated_at": calibrated, "probe_count": len(probes),
        "self_hamming": hamming, "self_total": total,
        "source_url": f"https://github.com/{UPSTREAM_REPO}/blob/{upstream_commit}/{path}",
        "sha256": hashlib.sha256(raw).hexdigest(), "filename": filename, "license": "Apache-2.0",
        "source_verified": False, "source_endpoint": endpoint["api_base"] if endpoint else "unknown",
        "protocol": "chat_completions", "upstream_commit": upstream_commit, "source_path": path,
        "git_blob_sha": git_blob_sha or git_blob_hash(raw),
        "source_directory": PurePosixPath(path).parent.as_posix(),
        "license_url": f"https://github.com/{UPSTREAM_REPO}/blob/{upstream_commit}/LICENSE",
        "license_sha256": LICENSE_SHA256,
        "accessed_on": accessed_on or datetime.now(timezone.utc).date().isoformat(),
        "self_error_rate": hamming / total,
        "domain_count": len({p["domain"] for p in probes}),
        "provenance_note": "作者发布的参考数据；记录的模型和渠道未经本组独立认证。",
    }
    return metadata, data


def git_blob_hash(raw: bytes) -> str:
    return hashlib.sha1(f"blob {len(raw)}\0".encode("ascii") + raw).hexdigest()


def _allowed_path(path) -> bool:
    return bool(isinstance(path, str) and _REFERENCE_PATH.fullmatch(path) and ".." not in path)


def _known_rejections() -> dict[tuple[str, str], str]:
    """Reuse this release's checked exclusions only for identical Git blobs."""
    try:
        audit = _json((BUNDLED_DIR / "update-audit.json").read_bytes())
        if not isinstance(audit, dict) or audit.get("upstream_commit") != PINNED_COMMIT:
            return {}
        rows = audit.get("skipped", [])
        if not isinstance(rows, list) or len(rows) > MAX_PACKS:
            return {}
        return {(row["source_path"], row["git_blob_sha"]): row["reason"] for row in rows
                if isinstance(row, dict) and _allowed_path(row.get("source_path"))
                and isinstance(row.get("git_blob_sha"), str) and _HEX40.fullmatch(row["git_blob_sha"])
                and isinstance(row.get("reason"), str) and len(row["reason"]) <= 500}
    except (OSError, ReferenceValidationError):
        return {}


def _catalog(directory: Path) -> list[dict]:
    path = directory / "catalog.json"
    if not path.exists():
        return []
    if path.is_symlink() or path.stat().st_size > MAX_CATALOG_BYTES:
        raise ReferenceValidationError("catalog_size_or_path_limit")
    rows = _json(path.read_bytes())
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ReferenceValidationError("invalid_catalog")
    ids = set()
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                or row["id"] in ids or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,240}", row["id"])
                or not isinstance(row.get("filename"), str) or not _FILENAME.fullmatch(row["filename"])
                or ".." in row["filename"] or not isinstance(row.get("sha256"), str)
                or not _HEX64.fullmatch(row["sha256"]) or not isinstance(row.get("upstream_commit"), str)
                or not _HEX40.fullmatch(row["upstream_commit"])):
            raise ReferenceValidationError("invalid_catalog_entry")
        ids.add(row["id"])
    return rows


def catalog_entries(library_dir: Path | None = None, *, warnings: list | None = None) -> list[tuple[dict, Path]]:
    """Merge installed and opt-in cached packs without replacing prior identities."""
    entries = [(row, BUNDLED_DIR) for row in _catalog(BUNDLED_DIR)]
    known = {row["id"]: row["sha256"] for row, _ in entries}
    if library_dir is not None:
        directory = Path(library_dir)
        try:
            cache = _catalog(directory)
        except ReferenceValidationError as exc:
            if warnings is not None:
                warnings.append({"filename": "catalog.json", "reason": str(exc)})
            cache = []
        for row in cache:
            if row["id"] in known:
                if known[row["id"]] != row["sha256"]:
                    if warnings is not None:
                        warnings.append({"filename": row["filename"], "reason": "cached_catalog_shadows_installed_reference"})
                continue
            entries.append((row, directory))
            known[row["id"]] = row["sha256"]
    return entries


def reference_library_warnings(library_dir: Path | None = None) -> list[dict]:
    """Warn about opt-in cache damage while retaining all valid bundled packs."""
    warnings: list[dict] = []
    for row, directory in catalog_entries(library_dir, warnings=warnings):
        if directory == BUNDLED_DIR:
            continue
        try:
            load_reference(row["id"], library_dir)
        except (ReferenceValidationError, OSError) as exc:
            warnings.append({"filename": row["filename"], "reason": str(exc) if isinstance(exc, ValueError) else "cached_file_unreadable"})
    return warnings


def load_reference(pack_id: str, library_dir: Path | None = None) -> tuple[dict, dict]:
    found = next(((row, directory) for row, directory in catalog_entries(library_dir) if row["id"] == pack_id), None)
    if found is None:
        raise ReferenceValidationError("Unknown reference pack; select an installed reference pack")
    row, directory = found
    path = directory / row["filename"]
    if path.is_symlink() or path.parent != directory or not path.is_file() or path.stat().st_size > MAX_PACK_BYTES:
        raise ReferenceValidationError("Reference pack path or size check failed")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != row["sha256"]:
        raise ReferenceValidationError("Reference pack integrity check failed")
    actual, data = validate_reference_bytes(raw, filename=row["filename"], upstream_commit=row["upstream_commit"],
                                          source_path=row.get("source_path"), git_blob_sha=row.get("git_blob_sha"),
                                          accessed_on=row.get("accessed_on"))
    for key in ("model", "probe_count", "self_hamming", "self_total"):
        if row.get(key) != actual[key]:
            raise ReferenceValidationError("Reference pack catalog does not match its contents")
    return {**actual, **row, "self_error_rate": actual["self_error_rate"],
            "domain_count": actual["domain_count"], "provenance_note": actual["provenance_note"],
            "source_verified": False, "storage": "bundled" if directory == BUNDLED_DIR else "cached"}, data


def _allowed_url(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.username or parts.password or parts.port or parts.fragment:
        return False
    if parts.hostname == "api.github.com":
        return (url == API_ROOT + "/commits/main" or bool(re.fullmatch(re.escape(API_ROOT) + r"/git/trees/[0-9a-f]{40}\?recursive=1", url)))
    if parts.hostname != "raw.githubusercontent.com" or parts.query:
        return False
    prefix = "/Ooo0ption/KBF/"
    if not parts.path.startswith(prefix):
        return False
    tail = parts.path[len(prefix):].split("/", 1)
    return len(tail) == 2 and bool(_HEX40.fullmatch(tail[0])) and (tail[1] == "LICENSE" or _allowed_path(tail[1]))


def _http_fetch(url: str, *, max_bytes: int, timeout: float, cancelled: Callable | None, deadline: float) -> tuple[int, dict, bytes]:
    if not _allowed_url(url):
        raise ReferenceFetchError("untrusted_upstream_url")
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=True,
                          headers={"User-Agent": "relay-audit-reference-updater", "Accept": "application/vnd.github+json"}) as client:
            with client.stream("GET", url) as response:
                length = response.headers.get("content-length")
                if length and (not length.isdecimal() or int(length) > max_bytes):
                    raise ReferenceFetchError("response_size_limit")
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    if cancelled and cancelled():
                        raise UpdateStopped("cancelled")
                    if time.monotonic() >= deadline:
                        raise UpdateStopped("update_time_limit")
                    size += len(chunk)
                    if size > max_bytes:
                        raise ReferenceFetchError("response_size_limit")
                    chunks.append(chunk)
                return response.status_code, dict(response.headers), b"".join(chunks)
    except httpx.TimeoutException:
        raise ReferenceFetchError("upstream_timeout") from None
    except httpx.HTTPError:
        raise ReferenceFetchError("upstream_network_error") from None


def _atomic_json(path: Path, value) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _acquire_update_lock(path: Path):
    """Kernel-released lock; an interrupted process cannot leave permanent busy state."""
    if path.is_symlink():
        raise ReferenceValidationError("unsafe_update_lock")
    stream = path.open("a+b")
    try:
        if path.stat().st_size == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return stream
    except OSError:
        stream.close()
        raise BlockingIOError("reference_update_already_running") from None


def _release_update_lock(stream) -> None:
    try:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
        stream.close()


def update_reference_library(library_dir: Path, progress: Callable | None = None,
                             cancelled: Callable | None = None, *, fetch: Callable | None = None) -> dict:
    """Resolve main once, then fetch bounded data and licence from that commit.

    ``fetch`` is a test seam: (url, *, max_bytes, timeout, cancelled, deadline)
    -> (HTTP status, headers, raw bytes). There are no credentials or URL options.
    """
    directory = Path(library_dir)
    checked = datetime.now(timezone.utc).isoformat()
    result = {"status": "unchanged", "upstream_commit": None, "added": [], "existing": [],
              "skipped": [], "failed": [], "catalog": [], "checked_at": checked,
              "source": f"https://github.com/{UPSTREAM_REPO}", "reference_directory": REFERENCE_PREFIX.rstrip("/"),
              "limits": {"max_packs": MAX_PACKS, "max_pack_bytes": MAX_PACK_BYTES,
                         "max_total_bytes": MAX_TOTAL_BYTES, "max_seconds": MAX_UPDATE_SECONDS},
        "source_verified": False, "code_updated": False, "upstream_code_changed": [], "downloaded_bytes": 0,
        "reference_directories": []}
    deadline = time.monotonic() + MAX_UPDATE_SECONDS
    fetcher = fetch or _http_fetch
    total, completed = 0, 0
    def announce(phase: str, filename: str | None = None):
        if progress:
            progress({"phase": phase, "completed": completed, "total": total,
                      "filename": filename, "added": len(result["added"]), "skipped": len(result["skipped"]),
                      "message": "正在读取公开参考目录" if phase == "discovering" else f"已检查 {completed}/{total} 份参考文件"})
    def request(url: str, limit: int):
        if cancelled and cancelled():
            raise UpdateStopped("cancelled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise UpdateStopped("update_time_limit")
        if not _allowed_url(url):
            raise ReferenceFetchError("untrusted_upstream_url")
        status, headers, raw = fetcher(url, max_bytes=min(limit, MAX_TOTAL_BYTES - result["downloaded_bytes"]),
                                      timeout=min(HTTP_TIMEOUT, remaining), cancelled=cancelled, deadline=deadline)
        if not isinstance(raw, bytes) or len(raw) > limit or result["downloaded_bytes"] + len(raw) > MAX_TOTAL_BYTES:
            raise ReferenceFetchError("response_size_limit")
        result["downloaded_bytes"] += len(raw)
        normalized = {str(k).lower(): str(v) for k, v in headers.items()}
        if status in {403, 429} and (status == 429 or normalized.get("x-ratelimit-remaining") == "0"):
            raise ReferenceFetchError("github_rate_limit" + (f":reset={normalized['x-ratelimit-reset']}" if normalized.get("x-ratelimit-reset", "").isdecimal() else ""))
        if 300 <= status < 400:
            raise ReferenceFetchError("upstream_redirect_rejected")
        if status != 200:
            raise ReferenceFetchError(f"upstream_http_{status}")
        return raw
    persisted, lock_stream = False, None
    lock = directory / ".update.lock"
    try:
        if directory.is_symlink():
            raise ReferenceValidationError("unsafe_library_directory")
        directory.mkdir(parents=True, exist_ok=True)
        try:
            lock_stream = _acquire_update_lock(lock)
        except BlockingIOError:
            result["status"] = "busy"
            result["failed"].append({"filename": None, "reason": "reference_update_already_running"})
            return result
        cache = _catalog(directory)
        all_entries = catalog_entries(directory)
        # Existing cache bytes must be intact before changing its catalogue.
        for row in cache:
            load_reference(row["id"], directory)
        result["catalog"] = [row for row, _ in all_entries]
        announce("discovering")
        commit_info = _json(request(API_ROOT + "/commits/main", 512 * 1024))
        commit = commit_info.get("sha") if isinstance(commit_info, dict) else None
        if not isinstance(commit, str) or not _HEX40.fullmatch(commit):
            raise ReferenceValidationError("invalid_resolved_commit")
        result["upstream_commit"] = commit
        tree = _json(request(f"{API_ROOT}/git/trees/{commit}?recursive=1", MAX_TREE_BYTES))
        if (not isinstance(tree, dict) or tree.get("truncated") is not False or not isinstance(tree.get("tree"), list)
                or not isinstance(tree.get("sha"), str) or not _HEX40.fullmatch(tree["sha"])):
            raise ReferenceValidationError("invalid_or_truncated_upstream_tree")
        paths = {r.get("path"): r for r in tree["tree"] if isinstance(r, dict) and isinstance(r.get("path"), str)}
        result["upstream_code_changed"] = [path for path, sha in PINNED_SOURCE_BLOBS.items()
                                            if paths.get(path, {}).get("sha") != sha]
        license_blob = paths.get("LICENSE", {})
        if license_blob.get("type") != "blob" or license_blob.get("mode") != "100644":
            raise ReferenceValidationError("upstream_license_missing")
        license_raw = request(f"{RAW_ROOT}/{commit}/LICENSE", 64 * 1024)
        if hashlib.sha256(license_raw).hexdigest() != LICENSE_SHA256 or git_blob_hash(license_raw) != license_blob.get("sha"):
            raise ReferenceValidationError("upstream_license_changed")
        license_path = directory / "LICENSE"
        if license_path.exists():
            if license_path.is_symlink() or hashlib.sha256(license_path.read_bytes()).hexdigest() != LICENSE_SHA256:
                raise ReferenceValidationError("cached_license_changed")
        else:
            with license_path.open("xb") as stream:
                stream.write(license_raw)
        notice_path = directory / "NOTICE"
        if not notice_path.exists():
            notice_path.write_text("KBF reference data, Ooo0ption/KBF, Apache-2.0.\n"
                                   "Model identity and source are author records, not independently authenticated.\n"
                                   "Pinned raw source, commit, blob hash and licence are recorded in catalog.json.\n"
                                   "Relay Audit downloads data only; the local KBF implementation is not updated.\n", encoding="utf-8")
        candidates = []
        for path, row in sorted(paths.items(), key=lambda item: (-int(item[0].split("/")[1].split("_")[-1]) if _allowed_path(item[0]) else 0, item[0])):
            if not _allowed_path(path):
                continue
            name = PurePosixPath(path).name
            if (row.get("type") != "blob" or row.get("mode") != "100644" or type(row.get("size")) is not int
                    or not 0 < row["size"] <= MAX_PACK_BYTES or not isinstance(row.get("sha"), str) or not _HEX40.fullmatch(row["sha"])):
                result["skipped"].append({"filename": name, "reason": "unsupported_tree_entry_or_size"})
                continue
            if len(candidates) >= MAX_PACKS:
                result["skipped"].append({"filename": name, "reason": "pack_count_limit"})
                continue
            candidates.append(row)
        result["reference_directories"] = sorted({PurePosixPath(row["path"]).parent.as_posix() for row in candidates}, reverse=True)
        total = len(candidates)
        if not total:
            result["skipped"].append({"filename": None, "reason": "no_compatible_reference_directory"})
        known_rejections = _known_rejections()
        for row in candidates:
            name = PurePosixPath(row["path"]).name
            announce("checking", name)
            try:
                if cancelled and cancelled():
                    raise UpdateStopped("cancelled")
                rejection = known_rejections.get((row["path"], row["sha"]))
                if rejection is not None:
                    result["skipped"].append({"filename": name, "reason": rejection, "source_path": row["path"],
                                              "git_blob_sha": row["sha"], "reused_audit": True})
                    continue
                existing = next((old for old, _ in all_entries if old.get("git_blob_sha") == row["sha"]
                                 and old.get("source_path", REFERENCE_PREFIX + old["filename"]) == row["path"]), None)
                if existing is not None:
                    load_reference(existing["id"], directory)
                    result["existing"].append(existing)
                    continue
                raw = request(f"{RAW_ROOT}/{commit}/{row['path']}", MAX_PACK_BYTES)
                if len(raw) != row["size"]:
                    raise ReferenceValidationError("tree_size_mismatch")
                meta, _ = validate_reference_bytes(raw, filename=name, upstream_commit=commit, source_path=row["path"],
                                                   git_blob_sha=row["sha"], accessed_on=checked[:10])
                existing = next((old for old, _ in all_entries if old["sha256"] == meta["sha256"]), None)
                if existing is not None:
                    result["existing"].append(existing)
                    continue
                known_ids = {old["id"] for old, _ in all_entries}
                if meta["id"] in known_ids or (directory / name).exists():
                    stem = name[:-5]
                    for length in (12, 64):
                        identifier = f"{stem}--{meta['sha256'][:length]}"
                        filename = identifier + ".json"
                        if identifier not in known_ids and not (directory / filename).exists():
                            meta.update(id=identifier, filename=filename)
                            break
                    else:
                        raise ReferenceValidationError("immutable_reference_path_conflict")
                # Publish only complete immutable bytes; catalogue is replaced last.
                destination = directory / meta["filename"]
                with destination.open("xb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    _atomic_json(directory / "catalog.json", cache + [meta])
                except OSError:
                    destination.unlink()
                    raise
                cache.append(meta)
                all_entries.append((meta, directory))
                result["added"].append(meta)
                result["catalog"] = [entry for entry, _ in all_entries]
                persisted = True
            except ReferenceValidationError as exc:
                result["skipped"].append({"filename": name, "reason": str(exc)})
            except UpdateStopped:
                raise
            except (ReferenceFetchError, OSError) as exc:
                reason = str(exc) if isinstance(exc, ReferenceFetchError) else "library_write_error"
                result["failed"].append({"filename": name, "reason": reason})
                if reason.startswith("github_rate_limit"):
                    break
            finally:
                completed += 1
                announce("checked", name)
        result["status"] = "partial" if result["failed"] else "updated" if result["added"] else "unchanged"
    except UpdateStopped as exc:
        result["status"] = "cancelled" if str(exc) == "cancelled" else "partial"
        result["stop_reason"] = str(exc)
        if str(exc) != "cancelled":
            result["failed"].append({"filename": None, "reason": str(exc)})
    except (ReferenceFetchError, ReferenceValidationError, OSError) as exc:
        result["status"] = "partial" if persisted else "error"
        result["failed"].append({"filename": None, "reason": str(exc) if isinstance(exc, ValueError) else "library_read_or_write_error"})
    finally:
        # Do not remove another updater's lock after returning busy.
        if lock_stream is not None:
            try:
                _release_update_lock(lock_stream)
            except OSError:
                pass
    if directory.is_dir() and result["status"] != "busy":
        try:
            _atomic_json(directory / "last_update.json", result)
        except OSError:
            result["failed"].append({"filename": None, "reason": "update_summary_write_error"})
            result["status"] = "partial" if persisted else "error"
    announce(result["status"])
    return result
