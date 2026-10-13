"""Public CLI and browser contracts for single-endpoint fingerprint workflows."""

import json
import socket
import time

import pytest
from fastapi.testclient import TestClient

from relay_audit.cli import main
from relay_audit.web import create_app


def wait_for(client, identifier):
    for _ in range(400):
        job = client.get(f"/api/runs/{identifier}").json()
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(.025)
    pytest.fail("local fingerprint job did not finish")


def test_browser_fingerprint_demo_replay_and_exports_without_network(tmp_path, monkeypatch):
    import relay_audit.fingerprint
    import httpx

    def forbidden(*args, **kwargs):
        raise AssertionError("reference listing and offline workflows must not make network calls")

    # TestClient uses an in-memory transport; Windows event loops still need
    # their local socket pair, so block model HTTP transport rather than that.
    monkeypatch.setattr(relay_audit.fingerprint, "_call", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    app = create_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        data = client.get("/api/bootstrap").json()
        packs = data["reference_packs"]
        assert len(packs) == 4
        assert all(pack["sha256"] and pack["probe_count"] > 100 for pack in packs)
        headers = {"X-Relay-Token": data["session_token"]}
        pack_id = packs[0]["id"]
        planned = client.post("/api/plan", headers=headers, json={
            "mode": "fingerprint", "reference_pack": pack_id,
            "candidate": {"api_base": "https://api.example.com/v1", "model": packs[0]["model"], "api_key": "synthetic-key-for-plan"},
        })
        assert planned.status_code == 200, planned.text
        assert "synthetic-key-for-plan" not in planned.text
        assert client.get("/api/runs").json()["runs"] == []
        response = client.post("/api/runs", headers=headers, json={
            "mode": "fingerprint_demo", "reference_pack": pack_id, "variant": "different",
        })
        assert response.status_code == 202, response.text
        identifier = response.json()["id"]
        job = wait_for(client, identifier)
        assert job["status"] == "completed", job
        report = job["reports"][0]["report"]
        assert job["reports"][0]["name"] == "fingerprint"
        assert report["kind"] == "fingerprint"
        assert report["synthetic"] is True
        assert report["status"] == "difference_detected"
        assert len(report["fingerprint"]["probes"]) == packs[0]["probe_count"]
        for extension in ("json", "md", "csv", "jsonl"):
            download = client.get(f"/api/runs/{identifier}/download/fingerprint/{extension}")
            assert download.status_code == 200, download.text
        records = client.get(f"/api/runs/{identifier}/download/fingerprint/jsonl").text
        raw = client.get(f"/api/runs/{identifier}/records?report=fingerprint").json()
        assert len(raw["fingerprint"]) > 0
        replay = client.post("/api/runs", headers=headers, json={
            "mode": "fingerprint_offline", "reference_pack": pack_id, "evidence_jsonl": records,
            "study": {"case_id": "C03", "model_claim": "固定型号", "route_claim": "官方来源", "notes": "![tracking](https://example.com/image)"},
        })
        assert replay.status_code == 202, replay.text
        replay_job = wait_for(client, replay.json()["id"])
        assert replay_job["status"] == "completed", replay_job
        replay_report = replay_job["reports"][0]["report"]
        assert replay_report["fingerprint"]["summary"] == report["fingerprint"]["summary"]
        assert replay_report["study"]["case_id"] == "C03"
        assert "参考指纹" in replay_report["claim_matrix"][0]["observation"]
        md = client.get(f"/api/runs/{replay_job['id']}/download/fingerprint/md").text
        assert "C03" in md and "MET 字符分布" not in md
        assert "![tracking](https://example.com/image)" not in md
        history = client.get("/api/runs").json()["runs"]
        assert len(history) == 2
        assert "probes" not in history[0]["reports"][0]["report"]["fingerprint"]
        assert all(row["reports"][0]["report"]["fingerprint"]["demo_variant"] == "different" for row in history)
        assert client.get(f"/api/runs/{identifier}/download/fingerprint/env").status_code == 404
    assert create_app(tmp_path).state.manager.get(identifier)["status"] == "completed"


def test_browser_rejects_malformed_fingerprint_inputs_without_echo(tmp_path):
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765") as client:
        data = client.get("/api/bootstrap").json()
        headers = {"X-Relay-Token": data["session_token"]}
        for payload in (
            {"mode": "fingerprint_demo", "reference_pack": "secret-as-invalid-pack"},
            {"mode": "fingerprint_offline", "reference_pack": data["reference_packs"][0]["id"], "evidence_jsonl": "secret-as-invalid-json"},
        ):
            result = client.post("/api/runs", headers=headers, json=payload)
            assert result.status_code == 422
            assert "secret-as-invalid" not in result.text


def test_cli_fingerprint_demo_replay_and_config_key_policy(tmp_path, monkeypatch, capsys):
    from relay_audit.fingerprint import list_reference_packs

    def forbidden(*args, **kwargs):
        raise AssertionError("offline CLI must not connect")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    pack = list_reference_packs()[0]
    assert main(["references"]) == 0
    assert pack["id"] in capsys.readouterr().out
    out = tmp_path / "demo"
    assert main(["fingerprint-demo", "--reference-pack", pack["id"], "--variant", "match", "--out", str(out)]) == 0
    before = (out / "report.json").read_bytes()
    report = json.loads(before)
    assert report["status"] == "no_difference_detected"
    replay = tmp_path / "replay"
    assert main(["fingerprint-replay", "--reference-pack", pack["id"], "--records", str(out / "fingerprint_records.jsonl"), "--out", str(replay)]) == 0
    assert json.loads((replay / "report.json").read_text(encoding="utf-8"))["fingerprint"]["summary"] == report["fingerprint"]["summary"]
    with pytest.raises(SystemExit):
        main(["fingerprint-demo", "--reference-pack", pack["id"], "--out", str(out)])
    assert (out / "report.json").read_bytes() == before
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"reference_pack": pack["id"], "candidate": {"api_base": "https://api.example.com/v1", "model": pack["model"], "api_key": "never-print-this-key"}}), encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["fingerprint-plan", "--config", str(config)])
    assert "never-print-this-key" not in capsys.readouterr().err
