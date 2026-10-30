import json
import socket
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from relay_audit.cli import main
from relay_audit.web import create_app


def completed(client, identifier):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        result = client.get(f"/api/runs/{identifier}").json()
        if result["status"] not in {"queued", "running"}:
            return result
        time.sleep(.01)
    pytest.fail("service job did not finish")


def test_service_plan_no_key_no_calls_and_invalid_key_not_echoed(tmp_path, monkeypatch):
    import relay_audit.service_quality as service

    monkeypatch.setattr(service, "run_service_quality", lambda *a, **k: pytest.fail("plan made a call"))
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765") as client:
        bootstrap = client.get("/api/bootstrap").json()
        assert {"service", "service_demo", "service_offline"} <= set(bootstrap["capabilities"]["modes"])
        headers = {"X-Relay-Token": bootstrap["session_token"]}
        payload = {"mode": "service", "candidate": {"api_base": "https://example.test/v1", "model": "test"}}
        plan = client.post("/api/plan", json=payload, headers=headers)
        assert plan.status_code == 200, plan.text
        assert plan.json()["maximum_requests"] == 6
        assert plan.json()["maximum_requested_output_tokens"] == 768
        assert not list(tmp_path.glob("*/job.json"))
        payload["candidate"]["api_key"] = "private test secret with spaces"
        bad = client.post("/api/plan", json=payload, headers=headers)
        assert bad.status_code == 422
        assert "private test secret" not in bad.text
        assert client.post("/api/runs", json=payload, headers=headers).status_code == 422


def test_service_demo_exports_history_and_exact_offline_replay(tmp_path, monkeypatch):
    # Windows' event loop uses a loopback socketpair; block outbound HTTP instead.
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda *a, **k: pytest.fail("demo opened HTTP connection"))
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765") as client:
        headers = {"X-Relay-Token": client.get("/api/bootstrap").json()["session_token"]}
        started = client.post("/api/runs", json={"mode": "service_demo"}, headers=headers)
        assert started.status_code == 202, started.text
        result = completed(client, started.json()["id"])
        assert result["status"] == "completed", result
        report = result["reports"][0]["report"]
        assert report["kind"] == "service_quality" and report["synthetic"]
        assert report["cost"]["request_count"] == 0
        assert report["service_quality"]["stream"]["successful_samples"] == 2
        assert all(item["status"] == "passed" for item in report["service_quality"]["tools"].values())
        identifier = result["id"]
        for ext in ("json", "md", "jsonl"):
            assert client.get(f"/api/runs/{identifier}/download/service/{ext}").status_code == 200
        assert client.get(f"/api/runs/{identifier}/download/service/csv").status_code == 404
        text = client.get(f"/api/runs/{identifier}/download/service/jsonl").text
        assert len(client.get(f"/api/runs/{identifier}/records?report=service").json()["service"]) == 8
        replay = client.post("/api/runs", json={"mode": "service_offline", "evidence_jsonl": text}, headers=headers)
        assert replay.status_code == 202, replay.text
        replayed = completed(client, replay.json()["id"])
        assert replayed["status"] == "completed", replayed
        assert replayed["reports"][0]["report"] == report
        tampered = text.replace('"sequence": 1', '"sequence": 99', 1)
        assert tampered != text
        assert client.post("/api/runs", json={"mode": "service_offline", "evidence_jsonl": tampered}, headers=headers).status_code == 422
        assert len(client.get("/api/runs").json()["runs"]) == 2


def test_service_cli_plan_demo_and_replay(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("offline command opened socket"))
    config = tmp_path / "service.json"
    config.write_text(json.dumps({"candidate": {"api_base": "https://example.test/v1", "model": "test"}, "checks": ["stream"]}), encoding="utf-8")
    assert main(["service-plan", "--config", str(config)]) == 0
    assert json.loads(capsys.readouterr().out)["planned_requests"] == 2
    demo, replay = tmp_path / "demo", tmp_path / "replay"
    assert main(["service-demo", "--out", str(demo)]) == 0
    assert main(["service-replay", "--records", str(demo / "service_records.jsonl"), "--out", str(replay)]) == 0
    assert (demo / "report.json").read_bytes() == (replay / "report.json").read_bytes()
    with pytest.raises(SystemExit):
        main(["service-demo", "--out", str(demo)])


@pytest.mark.parametrize("checks,count", [(["stream"], 2), (["tools"], 4)])
def test_service_demo_respects_selected_check_and_history_intent(tmp_path, checks, count):
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765") as client:
        headers = {"X-Relay-Token": client.get("/api/bootstrap").json()["session_token"]}
        started = client.post("/api/runs", json={"mode": "service_demo", "checks": checks}, headers=headers)
        assert started.status_code == 202
        result = completed(client, started.json()["id"])
        report = result["reports"][0]["report"]
        assert result["status"] == "completed", result
        assert report["checks"] == checks
        assert report["cost"]["simulated_request_count"] == count
        assert client.get("/api/runs").json()["runs"][0]["reports"][0]["report"]["checks"] == checks
