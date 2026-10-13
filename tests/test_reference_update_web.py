"""The update action is separate from paid sampling and never accepts secrets."""

import json
import threading
import time

from fastapi.testclient import TestClient

from relay_audit.web import create_app


def wait_update(client):
    for _ in range(200):
        job = client.get("/api/reference-library/update").json()
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(.02)
    raise AssertionError("reference update did not finish")


def test_reference_action_no_model_calls_and_history_persists(tmp_path, monkeypatch):
    import relay_audit.reference_library as library
    import relay_audit.fingerprint as fingerprint

    def no_model(*args, **kwargs):
        raise AssertionError("a reference update must not send model requests")

    monkeypatch.setattr(fingerprint, "_call", no_model)
    calls = []

    def update(directory, progress=None, cancelled=None):
        calls.append(directory)
        progress({"phase": "fetching", "completed": 1, "total": 1, "message": "checked"})
        return {"status": "completed", "added": [], "existing": fingerprint.list_reference_packs(),
                "skipped": [{"filename": "bad.json", "reason": "incompatible self-test"}], "failed": [],
                "catalog": fingerprint.list_reference_packs(), "upstream_commit": "a" * 40, "checked_at": "2026-10-13"}

    monkeypatch.setattr(library, "update_reference_library", update)
    lib = tmp_path / "references"
    app = create_app(tmp_path / "runs", library_dir=lib)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        initial = client.get("/api/reference-library").json()
        assert initial["last_update"] is None
        assert initial["reference_packs"]
        headers = {"X-Relay-Token": client.get("/api/bootstrap").json()["session_token"]}
        assert client.post("/api/reference-library/update", json={}).status_code == 403
        bad = client.post("/api/reference-library/update", headers=headers, json={"api_key": "synthetic-secret"})
        assert bad.status_code == 422 and "synthetic-secret" not in bad.text
        assert client.post("/api/reference-library/update", headers=headers, json={}).status_code == 202
        result = wait_update(client)
        assert result["status"] == "completed"
        assert result["result"]["skipped"][0]["filename"] == "bad.json"
        assert client.get("/api/runs").json()["runs"] == []
        assert client.get("/api/reference-library").json()["last_update"]["id"] == result["id"]
    assert calls == [lib.resolve()]
    assert create_app(tmp_path / "runs", library_dir=lib).state.reference_updater.snapshot()["id"] == result["id"]
    assert "synthetic-secret" not in (lib / "update-job.json").read_text(encoding="utf-8")


def test_update_duplicate_cancel_and_recovery(tmp_path, monkeypatch):
    import relay_audit.reference_library as library

    began, release = threading.Event(), threading.Event()

    def update(directory, progress=None, cancelled=None):
        began.set()
        release.wait(3)
        return {"status": "cancelled" if cancelled() else "completed", "added": [], "existing": [], "skipped": [], "failed": []}

    monkeypatch.setattr(library, "update_reference_library", update)
    app = create_app(tmp_path / "runs", library_dir=tmp_path / "refs")
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        headers = {"X-Relay-Token": client.get("/api/bootstrap").json()["session_token"]}
        assert client.post("/api/reference-library/update", headers=headers, json={}).status_code == 202
        assert began.wait(2)
        assert client.post("/api/reference-library/update", headers=headers, json={}).status_code == 409
        assert client.post("/api/reference-library/update/cancel", headers=headers, json={}).status_code == 200
        release.set()
        assert wait_update(client)["status"] == "cancelled"
    path = tmp_path / "refs" / "update-job.json"
    path.write_text(json.dumps({"id": "interrupted", "status": "running", "result": None}), encoding="utf-8")
    assert create_app(tmp_path / "runs", library_dir=tmp_path / "refs").state.reference_updater.snapshot()["status"] == "cancelled"


def test_update_exception_keeps_previous_library(tmp_path, monkeypatch):
    import relay_audit.reference_library as library

    def update(*args, **kwargs):
        raise OSError("example raw failure must not be returned")

    monkeypatch.setattr(library, "update_reference_library", update)
    with TestClient(create_app(tmp_path / "runs"), base_url="http://127.0.0.1:8765") as client:
        headers = {"X-Relay-Token": client.get("/api/bootstrap").json()["session_token"]}
        client.post("/api/reference-library/update", headers=headers, json={})
        job = wait_update(client)
        assert job["status"] == "failed" and "raw failure" not in job["error"]
        assert client.get("/api/reference-library").json()["reference_packs"]
