"""Weekly data-only updates from API-MRI's published common-reference index."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import re

import httpx

from . import identification_library as library

PUBLIC_ROOT = "https://raw.githubusercontent.com/Lee-Eee-Eee/API-MRI/main/src/relay_audit/data/identification/"
INDEX_URL = PUBLIC_ROOT + "public-index.json"
INTERVAL = timedelta(days=7)
MAX_INDEX = 128 * 1024
MAX_TOTAL = 3 * 1024 * 1024
MAX_SNAPSHOTS = 8
MAX_SNAPSHOT = 512 * 1024


def _state_path(directory):
    return Path(directory) / "identification" / "public-sync.json"


def sync_status(directory):
    value = {}
    path = _state_path(directory)
    try:
        if path.is_symlink() or path.stat().st_size > 8192:
            raise ValueError
        value = json.loads(path.read_bytes(), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if (not isinstance(value, dict) or value.get("status") not in {"failed", "updated", "up_to_date"}
                or any(type(value.get(field, 0)) is not int or not 0 <= value.get(field, 0) <= MAX_SNAPSHOTS
                       for field in ("added", "unchanged"))):
            value = {}
    except (OSError, ValueError, TypeError):
        pass
    last = value.get("last_checked")
    try:
        stamp = datetime.fromisoformat(last)
        if stamp.tzinfo is None:
            raise ValueError
        age = datetime.now(timezone.utc) - stamp
        due = age < timedelta(0) or age >= INTERVAL or value.get("status") == "failed"
    except (TypeError, ValueError):
        due = True
    return {"status": value.get("status", "not_checked"), "last_checked": last,
        "due": due, "interval_days": 7, "model_api_calls": 0, "source": INDEX_URL,
        "added": value.get("added", 0), "unchanged": value.get("unchanged", 0)}


def _download(client, url, maximum):
    # Callers provide only our fixed index URL or a validated basename.
    name = url.removeprefix(PUBLIC_ROOT)
    if (not url.startswith(PUBLIC_ROOT) or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,119}\.json", name)
            or ".." in name or maximum <= 0):
        raise ValueError("Unsupported public reference URL.")
    with client.stream("GET", url, follow_redirects=False) as response:
        if response.status_code != 200:
            raise ValueError("Public reference download failed.")
        chunks, total = [], 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > maximum:
                raise ValueError("Public reference size limit exceeded.")
            chunks.append(chunk)
        return b"".join(chunks)


def sync_public_references(directory, *, force=False, client=None):
    if type(force) is not bool:
        raise ValueError("force must be boolean")
    status = sync_status(directory)
    if not force and not status["due"]:
        return {**status, "status": "cached"}
    owned = client is None
    client = client or httpx.Client(timeout=15, follow_redirects=False, trust_env=False,
                                  headers={"User-Agent": "API-MRI-reference-sync"})
    result = {"status": "failed", "last_checked": datetime.now(timezone.utc).isoformat(),
        "due": False, "interval_days": 7, "model_api_calls": 0, "added": 0, "unchanged": 0, "source": INDEX_URL}
    try:
        raw = _download(client, INDEX_URL, MAX_INDEX)
        index = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if (not isinstance(index, dict) or set(index) != {"schema", "updated_at", "banks"}
                or index.get("schema") != "api-mri-public-reference-index/1"):
            raise ValueError("Unsupported public reference index.")
        if not isinstance(index["updated_at"], str):
            raise ValueError("Invalid public reference index date.")
        updated = datetime.fromisoformat(index["updated_at"].replace("Z", "+00:00"))
        if updated.tzinfo is None:
            raise ValueError("Invalid public reference index date.")
        entries = index.get("banks")
        if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_SNAPSHOTS:
            raise ValueError("Invalid public reference catalog.")
        existing = {row["id"]: row for row in library.list_banks(directory)}
        downloaded, total, identifiers = [], len(raw), set()
        for row in entries:
            if (not isinstance(row, dict) or set(row) != {"id", "sha256", "filename"}
                    or not isinstance(row.get("id"), str) or row["id"] in identifiers
                    or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", row["id"])
                    or not isinstance(row.get("filename"), str)
                    or row["filename"] != row["id"] + ".json"
                    or ".." in row["filename"] or not isinstance(row.get("sha256"), str)
                    or not re.fullmatch(r"[a-f0-9]{64}", row["sha256"])):
                raise ValueError("Invalid immutable reference entry.")
            identifiers.add(row["id"])
            installed = existing.get(row["id"])
            if installed:
                if installed["sha256"] != row["sha256"]:
                    raise ValueError("Published reference version changed its digest.")
                result["unchanged"] += 1
                continue
            data = _download(client, PUBLIC_ROOT + row["filename"], min(MAX_SNAPSHOT, MAX_TOTAL - total))
            total += len(data)
            if hashlib.sha256(data).hexdigest() != row["sha256"]:
                raise ValueError("Public reference digest mismatch.")
            bank = library.validate_bank_snapshot(json.loads(data), expected_sha=row["sha256"])
            if bank["id"] != row["id"]:
                raise ValueError("Public reference identity mismatch.")
            downloaded.append(bank)
        # Validate every new object before publication; the active local pointer is
        # never replaced by a network update. A reader can explicitly select it.
        for bank in downloaded:
            library.publish_bank(bank, directory)
            result["added"] += 1
        result["status"] = "updated" if downloaded else "up_to_date"
        return result
    except httpx.HTTPError:
        raise ValueError("Public reference download failed.") from None
    finally:
        if owned:
            client.close()
        library._atomic_json(_state_path(directory), result)
