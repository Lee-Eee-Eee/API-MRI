"""The browser preserves explicit sampling depth, budgets and partial evidence."""

import time

import pytest
from fastapi.testclient import TestClient

from relay_audit import fingerprint
from relay_audit.web import create_app


PACK = "gpt-4.1-mini_20260915"


def wait_for(client, identifier):
    for _ in range(200):
        run = client.get(f"/api/runs/{identifier}").json()
        if run["status"] not in {"queued", "running"}:
            return run
        time.sleep(.02)
    pytest.fail("local job did not complete")


@pytest.mark.parametrize("profile", ["screening", "full"])
def test_browser_plan_and_worker_preserve_requested_budget(tmp_path, monkeypatch, profile):
    received = []

    def fake_collect(payload, out, **kwargs):
        received.append(payload)
        return {"kind": "fingerprint", "profile": payload["profile"], "status": "insufficient_evidence",
                "budget": payload["budget"], "cost": {"request_count": 1, "usage": {"complete": False}},
                "collection": {"state": "partial", "stop_code": "budget_max_requests", "failure": "budget reached"},
                "fingerprint": {"summary": {}, "reference": {"id": PACK}}}

    monkeypatch.setattr(fingerprint, "run_fingerprint", fake_collect)
    monkeypatch.setattr(fingerprint, "_call", lambda *args, **kwargs: pytest.fail("Unexpected model call"))
    app = create_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        headers = {"X-Relay-Token": client.get("/api/bootstrap").json()["session_token"]}
        payload = {"mode": "fingerprint", "reference_pack": PACK, "profile": profile,
                   "budget": {"max_requests": 1, "max_output_tokens": 800},
                   "candidate": {"api_base": "https://example.invalid/v1", "model": "alias", "api_key": "secret-test-key"}}
        plan = client.post("/api/plan", json=payload, headers=headers)
        assert plan.status_code == 200, plan.text
        assert plan.json()["profile"] == profile
        assert plan.json()["budget"] == payload["budget"]
        assert plan.json()["budget_can_complete_minimum"] is False
        run = client.post("/api/runs", json=payload, headers=headers)
        assert run.status_code == 202, run.text
        result = wait_for(client, run.json()["id"])
        assert received[0]["profile"] == profile
        assert received[0]["budget"] == payload["budget"]
        assert result["status"] == "completed"  # Reaching a chosen budget is an expected stop.
        assert "预算" in result["progress"]["message"]
        assert result["reports"][0]["report"]["collection"]["state"] == "partial"
        history = client.get("/api/runs").json()["runs"][0]["reports"][0]["report"]
        assert history["profile"] == profile and history["cost"]["request_count"] == 1
        assert "secret-test-key" not in (tmp_path / result["id"] / "job.json").read_text(encoding="utf-8")


def test_screening_demo_and_replay_keep_descriptive_result(tmp_path, monkeypatch):
    monkeypatch.setattr(fingerprint, "_call", lambda *args, **kwargs: pytest.fail("Unexpected model call"))
    app = create_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        headers = {"X-Relay-Token": client.get("/api/bootstrap").json()["session_token"]}
        created = client.post("/api/runs", json={"mode": "fingerprint_demo", "reference_pack": PACK, "variant": "match"}, headers=headers)
        assert created.status_code == 202, created.text
        run = wait_for(client, created.json()["id"])
        report = run["reports"][0]["report"]
        assert report["profile"] == "screening" and report["status"] == "insufficient_evidence"
        assert report["fingerprint"]["screening"]["outcome"] == "no_differences_observed"
        assert report["fingerprint"]["summary"]["p_value"] is None
        evidence = client.get(f"/api/runs/{run['id']}/download/fingerprint/jsonl").text
        replay = client.post("/api/runs", json={"mode": "fingerprint_offline", "reference_pack": PACK, "evidence_jsonl": evidence}, headers=headers)
        assert replay.status_code == 202, replay.text
        replayed = wait_for(client, replay.json()["id"])["reports"][0]["report"]
        assert replayed["profile"] == "screening"
        assert replayed["fingerprint"]["summary"] == report["fingerprint"]["summary"]


def test_full_demo_is_explicit_and_invalid_budget_is_rejected(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        headers = {"X-Relay-Token": client.get("/api/bootstrap").json()["session_token"]}
        created = client.post("/api/runs", json={"mode": "fingerprint_demo", "reference_pack": PACK, "profile": "full", "variant": "match"}, headers=headers)
        assert created.status_code == 202, created.text
        run = wait_for(client, created.json()["id"])
        assert run["reports"][0]["report"]["profile"] == "full"
        response = client.post("/api/plan", json={"mode": "fingerprint", "reference_pack": PACK, "budget": {"max_requests": 0},
                              "candidate": {"api_base": "https://example.invalid/v1", "model": "alias", "api_key": "secret-test-key"}}, headers=headers)
        assert response.status_code == 422
        assert "预算" in response.json()["detail"] and "secret-test-key" not in response.text
