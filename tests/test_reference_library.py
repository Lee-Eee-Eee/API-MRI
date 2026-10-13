"""Data-only reference updates use deterministic fixtures, never model APIs."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import threading

import pytest

from relay_audit import fingerprint as fp
from relay_audit import reference_library as lib

COMMIT = "1" * 40
NAME = "research-fixture_20261013.json"
SOURCE_PATH = "probes/reference_202610/" + NAME


def fixture_pack():
    data = json.loads((lib.BUNDLED_DIR / "gpt-4.1-mini_20260915.json").read_text(encoding="utf-8"))
    model = "openai/research-fixture"
    probes = deepcopy(data["probes"][:3])
    values = [lib.kbf.probe_consensus(p) for p in probes]
    return {"reference_model": model, "timestamp": "2026-10-13T01:00:00",
            "numbering_format": "(N)", "total_probes": len(probes), "probes": probes,
            "provider_map": {model: "Recorded Provider"},
            "target_results": {"research-fixture": {"model": model, "is_self": True,
                "values": values, "match_vector": [1] * len(probes), "correct": len(probes),
                "total": len(probes), "hamming": 0, "timestamp": "2026-10-13T01:01:00",
                "test_config": {"sys": lib.kbf.TEST_SYS_PROMPT, "temp": lib.kbf.TEST_TEMP}}}}


def encoded(data):
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


class UpstreamFixture:
    def __init__(self, packs=None, *, commit=COMMIT, code_changed=False, license_raw=None):
        self.commit = commit
        self.packs = {SOURCE_PATH: encoded(fixture_pack())} if packs is None else packs
        self.calls = []
        self.license = license_raw if license_raw is not None else (lib.BUNDLED_DIR.parent.parent / "vendor/kbf/LICENSE").read_bytes()
        self.code_changed = code_changed

    def tree(self):
        rows = [{"path": path, "sha": "b" * 40 if self.code_changed and path.endswith("domains.py") else sha,
                 "mode": "100644", "type": "blob", "size": 100} for path, sha in lib.PINNED_SOURCE_BLOBS.items()]
        rows.append({"path": "LICENSE", "sha": lib.git_blob_hash(self.license), "mode": "100644", "type": "blob", "size": len(self.license)})
        rows.extend({"path": path, "sha": lib.git_blob_hash(raw), "mode": "100644", "type": "blob", "size": len(raw)}
                    for path, raw in self.packs.items())
        return {"sha": self.commit, "truncated": False, "tree": rows}

    def __call__(self, url, *, max_bytes, timeout, cancelled, deadline):
        assert lib._allowed_url(url)
        assert 0 < timeout <= lib.HTTP_TIMEOUT
        self.calls.append(url)
        if url == lib.API_ROOT + "/commits/main":
            raw = encoded({"sha": self.commit})
        elif url == f"{lib.API_ROOT}/git/trees/{self.commit}?recursive=1":
            raw = encoded(self.tree())
        elif url == f"{lib.RAW_ROOT}/{self.commit}/LICENSE":
            raw = self.license
        else:
            raw = self.packs[url.split(self.commit + "/", 1)[1]]
        assert len(raw) <= max_bytes
        return 200, {}, raw


def test_update_is_opt_in_data_only_and_replayable(tmp_path):
    fixture = UpstreamFixture(code_changed=True)
    seen = []
    before = {p["id"] for p in fp.list_reference_packs()}
    result = lib.update_reference_library(tmp_path / "library", progress=seen.append, fetch=fixture)
    assert result["status"] == "updated"
    assert result["upstream_commit"] == COMMIT
    assert result["reference_directories"] == ["probes/reference_202610"]
    assert result["code_updated"] is False
    assert result["upstream_code_changed"] == ["scripts/domains.py"]
    assert result["source_verified"] is False
    assert len(result["added"]) == 1
    meta = result["added"][0]
    assert meta["id"] == NAME[:-5]
    assert meta["license"] == "Apache-2.0"
    assert meta["license_sha256"] == lib.LICENSE_SHA256
    assert meta["git_blob_sha"] == lib.git_blob_hash(fixture.packs[SOURCE_PATH])
    assert (tmp_path / "library" / NAME).read_bytes() == fixture.packs[SOURCE_PATH]
    assert (tmp_path / "library/LICENSE").read_bytes() == fixture.license
    assert (tmp_path / "library/NOTICE").exists()
    assert all("scripts/" not in url for url in fixture.calls)
    assert fixture.calls.count(lib.API_ROOT + "/commits/main") == 1
    assert all(COMMIT in url for url in fixture.calls if not url.endswith("/main"))
    assert any(p["phase"] == "checked" and p["completed"] == 1 for p in seen)
    assert {p["id"] for p in fp.list_reference_packs()} == before
    assert meta["id"] in {p["id"] for p in fp.list_reference_packs(tmp_path / "library")}
    with pytest.raises(ValueError):
        fp.demo_fingerprint(meta["id"], tmp_path / "without-cache")
    report = fp.demo_fingerprint(meta["id"], tmp_path / "demo", library_dir=tmp_path / "library")
    assert report["status"] == "no_difference_detected"
    replay = fp.replay_fingerprint(meta["id"], tmp_path / "demo/fingerprint_records.jsonl",
                                   tmp_path / "replay", library_dir=tmp_path / "library")
    assert replay["fingerprint"]["summary"] == report["fingerprint"]["summary"]
    plan = fp.plan_fingerprint({"reference_pack": meta["id"], "candidate": {
        "api_base": "https://api.example.com/v1", "model": "new-model-alias"}}, library_dir=tmp_path / "library")
    assert plan["candidate"]["model"] == "new-model-alias"
    fixture.calls.clear()
    again = lib.update_reference_library(tmp_path / "library", fetch=fixture)
    assert again["status"] == "unchanged" and not again["added"] and len(again["existing"]) == 1
    assert not any(SOURCE_PATH in url for url in fixture.calls)


@pytest.mark.parametrize("change,reason", [
    (lambda d: d.update(numbering_format="[N]"), "unsupported_prompt_numbering"),
    (lambda d: d.update(reference_endpoint={"protocol": "anthropic", "api_base": "https://api.example.com"}), "unsupported_reference_protocol"),
    (lambda d: d["probes"][0].update(domain="new_domain"), "unsupported_domain"),
    (lambda d: d["probes"][0].update(value=float("inf")), "invalid_json"),
    (lambda d: d["probes"][0].update(value=True), "invalid_reference_value"),
    (lambda d: d.update(total_probes=2), "probe_counter_mismatch"),
    (lambda d: d["target_results"]["research-fixture"]["test_config"].update(sys="changed prompt"), "unsupported_self_test_settings"),
    (lambda d: d["target_results"]["research-fixture"].update(hamming=1), "self_test_hamming_mismatch"),
    (lambda d: d["target_results"]["research-fixture"].update(values=[1]), "invalid_self_test_values"),
    (lambda d: d["target_results"]["research-fixture"].update(match_vector=[0, 1, 1]), "self_test_match_vector_mismatch"),
])
def test_incompatible_data_skips_without_mutating_installed_pack(tmp_path, change, reason):
    initial = UpstreamFixture()
    lib.update_reference_library(tmp_path, fetch=initial)
    raw_before = (tmp_path / NAME).read_bytes()
    catalog_before = (tmp_path / "catalog.json").read_bytes()
    data = fixture_pack()
    change(data)
    modified = UpstreamFixture({SOURCE_PATH: encoded(data)}, commit="2" * 40)
    result = lib.update_reference_library(tmp_path, fetch=modified)
    assert result["status"] == "unchanged"
    assert not result["added"]
    assert result["skipped"][0]["reason"].startswith(reason)
    assert (tmp_path / NAME).read_bytes() == raw_before
    assert (tmp_path / "catalog.json").read_bytes() == catalog_before


def test_same_filename_new_content_is_new_immutable_identity(tmp_path):
    one = lib.update_reference_library(tmp_path, fetch=UpstreamFixture())["added"][0]
    data = fixture_pack()
    data["timestamp"] = "2026-10-13T02:00:00"
    raw = encoded(data)
    two = lib.update_reference_library(tmp_path, fetch=UpstreamFixture({SOURCE_PATH: raw}, commit="2" * 40))["added"][0]
    assert two["id"] == one["id"] + "--" + hashlib.sha256(raw).hexdigest()[:12]
    assert two["source_path"] == one["source_path"]
    assert (tmp_path / two["filename"]).read_bytes() == raw
    assert (tmp_path / one["filename"]).read_bytes() != raw
    assert lib.load_reference(one["id"], tmp_path)[0]["generated_at"] != lib.load_reference(two["id"], tmp_path)[0]["generated_at"]
    assert len(json.loads((tmp_path / "catalog.json").read_text(encoding="utf-8"))) == 2


def test_tampered_cache_is_rejected_but_bundled_remains_available(tmp_path):
    meta = lib.update_reference_library(tmp_path, fetch=UpstreamFixture())["added"][0]
    (tmp_path / meta["filename"]).write_bytes(b"{}")
    before = {p["id"] for p in fp.list_reference_packs()}
    assert {p["id"] for p in fp.list_reference_packs(tmp_path)} == before
    assert lib.reference_library_warnings(tmp_path)[0]["filename"] == meta["filename"]
    with pytest.raises(ValueError, match="integrity"):
        lib.load_reference(meta["id"], tmp_path)
    result = lib.update_reference_library(tmp_path, fetch=UpstreamFixture())
    assert result["status"] == "error" and not result["added"]
    (tmp_path / "catalog.json").write_bytes(b"not json")
    assert {p["id"] for p in fp.list_reference_packs(tmp_path)} == before
    assert lib.reference_library_warnings(tmp_path)[0]["filename"] == "catalog.json"


def test_write_failure_rolls_back_catalogue_and_keeps_existing_bytes(tmp_path, monkeypatch):
    meta = lib.update_reference_library(tmp_path, fetch=UpstreamFixture())["added"][0]
    original_catalog = (tmp_path / "catalog.json").read_bytes()
    original_bytes = (tmp_path / meta["filename"]).read_bytes()
    data = fixture_pack()
    data["timestamp"] = "2026-10-13T02:00:00"
    atomic = lib._atomic_json
    def failed_catalog(path, value):
        if path.name == "catalog.json":
            raise OSError("fixture disk failure")
        return atomic(path, value)
    monkeypatch.setattr(lib, "_atomic_json", failed_catalog)
    result = lib.update_reference_library(tmp_path, fetch=UpstreamFixture({SOURCE_PATH: encoded(data)}))
    assert result["status"] == "partial"
    assert result["failed"][0]["reason"] == "library_write_error"
    assert (tmp_path / "catalog.json").read_bytes() == original_catalog
    assert (tmp_path / meta["filename"]).read_bytes() == original_bytes
    assert len([p for p in tmp_path.glob("*.json") if p.name not in {"catalog.json", "last_update.json"}]) == 1


def test_cancel_keeps_completed_pack_and_progress(tmp_path):
    stopped = False
    fixture = UpstreamFixture({SOURCE_PATH: encoded(fixture_pack()), "probes/reference_202610/z-extra.json": encoded(fixture_pack())})
    def progress(event):
        nonlocal stopped
        if event["phase"] == "checked":
            stopped = True
    result = lib.update_reference_library(tmp_path, progress=progress, cancelled=lambda: stopped, fetch=fixture)
    assert result["status"] == "cancelled" and len(result["added"]) == 1
    assert result["failed"] == [] and result["stop_reason"] == "cancelled"
    assert lib.load_reference(result["added"][0]["id"], tmp_path)[0]["probe_count"] == 3


def test_rate_limit_redirect_size_and_license_errors_are_clear(tmp_path, monkeypatch):
    def limited(url, **kwargs):
        return 403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "12345"}, b"{}"
    result = lib.update_reference_library(tmp_path / "rate", fetch=limited)
    assert result["status"] == "error"
    assert result["failed"][0]["reason"] == "github_rate_limit:reset=12345"
    redirected = lib.update_reference_library(tmp_path / "redirect", fetch=lambda *a, **k: (302, {"location": "https://evil.invalid"}, b""))
    assert redirected["failed"][0]["reason"] == "upstream_redirect_rejected"
    oversized = lib.update_reference_library(tmp_path / "large", fetch=lambda *a, **k: (200, {}, b"x" * (lib.MAX_TOTAL_BYTES + 1)))
    assert oversized["failed"][0]["reason"] == "response_size_limit"
    changed = lib.update_reference_library(tmp_path / "license", fetch=UpstreamFixture(license_raw=b"different licence"))
    assert changed["failed"][0]["reason"] == "upstream_license_changed" and not changed["added"]
    monkeypatch.setattr(lib, "MAX_UPDATE_SECONDS", 0)
    timed = lib.update_reference_library(tmp_path / "timeout", fetch=lambda *a, **k: pytest.fail("must stop before HTTP"))
    assert timed["status"] == "partial" and timed["failed"][0]["reason"] == "update_time_limit"


@pytest.mark.parametrize("url", ["https://evil.invalid/x", "https://api.github.com/repos/other/KBF/commits/main",
    "https://raw.githubusercontent.com/Ooo0ption/KBF/main/probes/reference_202610/x.json",
    f"{lib.RAW_ROOT}/{COMMIT}/scripts/kbf_common.py", f"{lib.RAW_ROOT}/{COMMIT}/probes/reference_202610/../x.json",
    f"{lib.RAW_ROOT}/{COMMIT}/probes/reference_202613/x.json", f"{lib.RAW_ROOT}/{COMMIT}/probes/reference/x.json",
    f"{lib.RAW_ROOT}/{COMMIT}/probes/reference_202610/x.json?token=secret"])
def test_updater_url_allowlist_has_no_custom_source_or_code(url):
    assert not lib._allowed_url(url)


def test_tree_symlinks_and_oversize_packs_are_skipped(tmp_path):
    fixture = UpstreamFixture()
    tree = fixture.tree()
    tree["tree"][-1]["mode"] = "120000"
    fixture.tree = lambda: tree
    result = lib.update_reference_library(tmp_path, fetch=fixture)
    assert not result["added"] and result["skipped"][0]["reason"] == "unsupported_tree_entry_or_size"
    assert not any(SOURCE_PATH in url for url in fixture.calls)


def test_kernel_lock_prevents_parallel_updates_and_releases_after_exit(tmp_path):
    fixture = UpstreamFixture()
    lock = lib._acquire_update_lock(tmp_path / ".update.lock")
    try:
        result = lib.update_reference_library(tmp_path, fetch=fixture)
        assert result["status"] == "busy" and not fixture.calls
    finally:
        lib._release_update_lock(lock)
    assert lib.update_reference_library(tmp_path, fetch=fixture)["status"] == "updated"


def test_builtin_manifest_preserves_original_files_and_two_precise_rejections():
    packs = fp.list_reference_packs()
    assert len(packs) == 26
    assert {p["model"] for p in packs}.issuperset({"openai/gpt-6-astra", "anthropic/claude-opus-5", "moonshotai/kimi-k3", "deepseek/deepseek-v4-pro-0813"})
    audit = json.loads((lib.BUNDLED_DIR / "update-audit.json").read_text(encoding="utf-8"))
    assert audit["candidate_count"] == 28 and audit["accepted_count"] == 26
    assert len(audit["skipped"]) == 2
    assert all("self_test_hamming_mismatch" in row["reason"] for row in audit["skipped"])


def test_checked_rejection_avoids_redownload_but_changed_blob_is_revalidated(tmp_path, monkeypatch):
    raw = encoded(fixture_pack())
    monkeypatch.setattr(lib, "_known_rejections", lambda: {(SOURCE_PATH, lib.git_blob_hash(raw)): "self_test_match_vector_mismatch"})
    same = UpstreamFixture({SOURCE_PATH: raw})
    result = lib.update_reference_library(tmp_path / "same", fetch=same)
    assert not result["added"] and result["skipped"][0]["reused_audit"] is True
    assert not any(url.endswith(SOURCE_PATH) for url in same.calls)
    changed = fixture_pack()
    changed["timestamp"] = "2026-10-13T02:00:00"
    newer = UpstreamFixture({SOURCE_PATH: encoded(changed)})
    result = lib.update_reference_library(tmp_path / "changed", fetch=newer)
    assert len(result["added"]) == 1 and not result["skipped"]
    assert any(url.endswith(SOURCE_PATH) for url in newer.calls)
