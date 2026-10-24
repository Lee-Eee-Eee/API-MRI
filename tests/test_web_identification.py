import json
import time

from fastapi.testclient import TestClient

from relay_audit.web import create_app


def completed(client, identifier):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        result = client.get(f"/api/runs/{identifier}").json()
        if result["status"] in {"completed", "failed", "cancelled"}:
            return result
        time.sleep(.02)
    raise AssertionError("Identification job did not finish")


def test_identification_plan_is_offline_and_sanitized(tmp_path, monkeypatch):
    import relay_audit.fingerprint as fp

    monkeypatch.setattr(fp, "_call", lambda *a, **k: (_ for _ in ()).throw(AssertionError("plan made an API call")))
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765") as client:
        bootstrap = client.get("/api/bootstrap").json()
        assert bootstrap["identification_bank"]["model_count"] > 100
        assert "identify" in bootstrap["capabilities"]["modes"]
        headers = {"X-Relay-Token": bootstrap["session_token"]}
        payload = {"mode": "identify", "candidate": {"api_base": "https://example.test/v1", "model": "untrusted-alias", "api_key": "secret-for-local-test"},
                   "budget": {"max_requests": 24, "max_output_tokens": 384}}
        response = client.post("/api/plan", json=payload, headers=headers)
        assert response.status_code == 200, response.text
        plan = response.json()
        assert plan["max_completed_cells"] == 6
        assert plan["protocol"]["max_tokens"] == 16
        assert "secret-for-local-test" not in response.text
        del payload["candidate"]["api_key"]
        assert client.post("/api/plan", json=payload, headers=headers).status_code == 200
        assert not list(tmp_path.glob("*/job.json"))


def test_identification_demo_history_and_exact_replay(tmp_path, monkeypatch):
    import relay_audit.fingerprint as fp

    monkeypatch.setattr(fp, "_call", lambda *a, **k: (_ for _ in ()).throw(AssertionError("offline mode made an API call")))
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765") as client:
        headers = {"X-Relay-Token": client.get("/api/bootstrap").json()["session_token"]}
        response = client.post("/api/runs", json={"mode": "identify_demo", "budget": {"max_requests": 12, "max_output_tokens": 192}}, headers=headers)
        assert response.status_code == 202, response.text
        result = completed(client, response.json()["id"])
        assert result["status"] == "completed", result
        report = result["reports"][0]["report"]
        assert report["kind"] == "identification" and report["synthetic"]
        assert report["identity_decision"] == "abstained" and report["probability"] is None
        assert report["cost"]["real_requests"] == 0
        assert report["coverage"]["completed_cells"] == 3
        identifier = result["id"]
        for ext in ("json", "md", "jsonl"):
            assert client.get(f"/api/runs/{identifier}/download/identification/{ext}").status_code == 200
        text = client.get(f"/api/runs/{identifier}/download/identification/jsonl").text
        bad_text = text.replace('"sequence": 1', '"sequence": 99', 1)
        if bad_text != text:
            assert client.post("/api/runs", json={"mode": "identify_offline", "evidence_jsonl": bad_text}, headers=headers).status_code == 422
        response = client.post("/api/runs", json={"mode": "identify_offline", "evidence_jsonl": text}, headers=headers)
        assert response.status_code == 202, response.text
        replay = completed(client, response.json()["id"])
        assert replay["status"] == "completed", replay
        assert replay["reports"][0]["report"]["ranking"] == report["ranking"]
        assert replay["reports"][0]["report"]["cost"] == report["cost"]
        rows = client.get(f"/api/runs/{identifier}/records?report=identification").json()["identification"]
        assert len(rows) == 14
        assert len(client.get("/api/runs").json()["runs"]) == 2
