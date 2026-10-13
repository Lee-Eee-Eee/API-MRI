import json
import re
import threading
import time

import pytest
from fastapi.testclient import TestClient

from relay_audit.records import AuditRecord, content_hash
from relay_audit.web import create_app, parse_jsonl, validate_job


def evidence(role, answer):
    return "\n".join(AuditRecord(
        run_id=role, endpoint_id=role, prompt_id="format", replicate=i,
        prompt_hash=content_hash("prompt"), config_hash=content_hash("config"),
        model_declared="synthetic", reference_kind="synthetic_control", synthetic=True,
        status="success", response=answer,
        metadata={"expected_prompt_ids": ["format"], "expected_repeats": 12,
                  "probe_check": {"kind": "exact", "expected": "blue"}},
    ).model_dump_json() for i in range(12))


def wait_for(client, identifier):
    for _ in range(200):
        data = client.get(f"/api/runs/{identifier}").json()
        if data["status"] not in {"running", "queued"}:
            return data
        time.sleep(.05)
    pytest.fail("local job did not complete")


def test_local_boundary_and_session_token(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        bootstrap = client.get("/api/bootstrap")
        assert bootstrap.status_code == 200
        token = bootstrap.json()["session_token"]
        assert bootstrap.headers["cache-control"] == "no-store"
        html = client.get("/").text
        assets = re.findall(r'(?:src|href)="(/[^"#]+\.(?:js|css))"', html)
        assert len(assets) == 2
        assert all(client.get(path).status_code == 200 for path in assets)
        assert client.post("/api/runs", json={"mode": "demo"}).status_code == 403
        headers = {"X-Relay-Token": token}
        assert client.post("/api/runs", json={"mode": "demo"}, headers={**headers, "Origin": "https://evil.example"}).status_code == 403
        assert client.get("/api/bootstrap", headers={"Host": "rebind.example"}).status_code == 403
        malformed = client.post("/api/runs", content='{"api_key":"private-test-secret",', headers={**headers, "Content-Type": "application/json"})
        assert malformed.status_code == 422
        assert "private-test-secret" not in malformed.text
        assert client.get("/api/runs/not-a-valid-path/download/comparison/json").status_code == 404
        assert client.post("/api/runs", json={"mode": "offline", "reference_jsonl": "private-test-secret", "candidate_jsonl": "x"}, headers=headers).status_code == 422


def test_offline_ui_flow_history_exports_and_no_model_calls(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("offline job must not collect responses")
    import relay_audit.collect
    monkeypatch.setattr(relay_audit.collect, "collect_endpoint", no_network)
    app = create_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        token = client.get("/api/bootstrap").json()["session_token"]
        response = client.post("/api/runs", headers={"X-Relay-Token": token}, json={
            "mode": "offline", "reference_jsonl": evidence("ref", "blue"),
            "candidate_jsonl": evidence("candidate", "magenta"), "permutations": 99,
            "study": {"case_id": "C01", "model_claim": "固定版本", "data_policy_claim": "不训练"},
        })
        assert response.status_code == 202, response.text
        identifier = response.json()["id"]
        result = wait_for(client, identifier)
        assert result["status"] == "completed", result
        report = result["reports"][0]["report"]
        assert report["status"] == "behavior_difference"
        assert report["study"]["case_id"] == "C01"
        assert len(report["claim_matrix"]) == 2
        assert report["diagnostics"]["roles"]["reference"]["checks"]["passed"] == 12
        for extension in ("json", "md", "csv"):
            download = client.get(f"/api/runs/{identifier}/download/comparison/{extension}")
            assert download.status_code == 200
        records = client.get(f"/api/runs/{identifier}/records?report=comparison").json()
        assert len(records["reference"]) == 12
        assert records["candidate"][0]["response"] == "magenta"
        assert len(client.get("/api/runs").json()["runs"]) == 1
    reloaded = create_app(tmp_path)
    assert reloaded.state.manager.get(identifier)["status"] == "completed"


def test_cancel_keeps_single_active_job_and_does_not_persist_payload(tmp_path, monkeypatch):
    import relay_audit.workflows
    started, release = threading.Event(), threading.Event()
    def fake_pair(payload, out, *, progress, cancelled):
        started.set()
        release.wait(timeout=3)
        return {"status": "insufficient_evidence", "collection": {"cancelled": cancelled()}}
    monkeypatch.setattr(relay_audit.workflows, "run_pair", fake_pair)
    app = create_app(tmp_path)
    manager = app.state.manager
    settings = {"mode": "live", "permutations": 99, "seed": 11, "alpha": .05, "min_samples": 8,
                "api_key": "private-test-secret"}
    first = manager.start(dict(settings))
    assert started.wait(2)
    with pytest.raises(RuntimeError):
        manager.start(dict(settings))
    manager.cancel(first["id"])
    release.set()
    manager.threads[0].join(timeout=5)
    assert manager.get(first["id"])["status"] == "cancelled"
    assert "private-test-secret" not in (tmp_path / first["id"] / "job.json").read_text(encoding="utf-8")


def test_import_limits_and_bom():
    assert len(parse_jsonl("\ufeff"+evidence("ref", "blue"))) == 12
    with pytest.raises(ValueError):
        parse_jsonl("x" * (2*1024*1024+1))
    with pytest.raises(ValueError):
        parse_jsonl("")
    partial = validate_job({"mode": "offline", "reference_jsonl": evidence("reference", "blue"), "candidate_jsonl": ""})
    assert partial["candidate_records"] == []
    with pytest.raises(ValueError):
        validate_job({"mode": "offline", "reference_jsonl": "", "candidate_jsonl": ""})


def test_lab_ui_flow_with_scenario_reveal(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        bootstrap = client.get("/api/bootstrap").json()
        assert len(bootstrap["scenarios"]) >= 6
        response = client.post("/api/runs", headers={"X-Relay-Token": bootstrap["session_token"]},
                               json={"mode": "lab", "scenario": "prompt_rewrite", "permutations": 99})
        assert response.status_code == 202
        result = wait_for(client, response.json()["id"])
        assert result["status"] == "completed", result
        report = result["reports"][0]["report"]
        assert report["lab"]["scenario"]["id"] == "prompt_rewrite"
        assert report["lab"]["real_model_calls"] == 0
        assert report["synthetic"] is True
