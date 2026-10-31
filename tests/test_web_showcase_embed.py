"""Classic examples, local framing, and isolated generated HTML previews."""
import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from relay_audit.cli import main as cli_main
from relay_audit.portable import PortableServer, existing_instance, installation_id, prepare_data_dir, _request_json
from relay_audit import showcase
from relay_audit.web import create_app, normalize_embed_origins

BASE = "http://127.0.0.1:8765"
DSH = "http://localhost:8080"
KEY = "fixture-showcase-key-0123456789"


def headers(client):
    return {"X-Relay-Token": client.get("/api/bootstrap").json()["session_token"]}


def completed(client, identifier):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        result = client.get(f"/api/runs/{identifier}").json()
        if result["status"] not in {"queued", "running"}:
            return result
        time.sleep(.01)
    pytest.fail("showcase task did not finish")


def directives(response):
    result = {}
    for directive in response.headers["content-security-policy"].split(";"):
        parts = directive.strip().split()
        if parts:
            result[parts[0]] = parts[1:]
    return result


@pytest.fixture(autouse=True)
def forbid_real_http(monkeypatch):
    # ASGI TestClient has its own in-process transport. A real model collector
    # accidentally reached by a test must stop here rather than open sockets.
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda *a, **k: pytest.fail("unexpected outgoing HTTP"))


def test_catalog_and_only_authorized_asset_source_are_available(tmp_path):
    with TestClient(create_app(tmp_path), base_url=BASE) as client:
        bootstrap = client.get("/api/bootstrap").json()
        assert {"showcase", "showcase_demo", "showcase_offline"} <= set(bootstrap["capabilities"]["modes"])
        assert bootstrap["embedding"]["origins"] == []
        response = client.get("/api/showcase")
        assert response.status_code == 200
        catalog = response.json()
        assert len(catalog["tasks"]) == 6 and len(catalog["examples"]) == 37
        assets = [entry for entry in catalog["examples"] if entry.get("asset")]
        assert len(assets) == 24
        example = client.get("/api/showcase/examples/" + assets[0]["id"])
        assert example.status_code == 200 and example.headers["content-type"].startswith("application/json")
        assert example.json()["example"] == assets[0]
        assert example.json()["source"] == showcase.showcase_asset(assets[0]["id"]).read_text(encoding="utf-8")
        assert example.headers["x-content-type-options"] == "nosniff"
        external = next(entry for entry in catalog["examples"] if not entry.get("asset"))
        assert client.get("/api/showcase/examples/" + external["id"]).status_code == 404
        assert client.get("/api/showcase/examples/not-a-real-example").status_code == 404
        assert client.get("/api/showcase/examples/%2e%2e%2fprivate").status_code == 404


def test_plan_needs_no_key_and_never_starts_collection(tmp_path, monkeypatch):
    monkeypatch.setattr(showcase, "run_showcase", lambda *a, **k: pytest.fail("plan started collection"))
    with TestClient(create_app(tmp_path), base_url=BASE) as client:
        auth = headers(client)
        payload = {"mode": "showcase", "task_id": "pelican-svg", "candidate": {"api_base": "https://fixture.example/v1", "model": "fixture"}}
        result = client.post("/api/plan", json=payload, headers=auth)
        assert result.status_code == 200, result.text
        plan = result.json()
        assert plan["maximum_requests"] == 1 and plan["maximum_requested_output_tokens"] == 2048
        assert plan["network_retries"] == 0 and plan["task"]["prompt"].startswith("Generate an SVG")
        assert not list(tmp_path.glob("*/job.json"))
        invalid = client.post("/api/plan", json={**payload, "task_id": "missing"}, headers=auth)
        assert invalid.status_code == 422


def test_public_demo_download_history_records_and_offline_replay(tmp_path):
    with TestClient(create_app(tmp_path), base_url=BASE) as client:
        auth = headers(client)
        request = client.post("/api/runs", json={"mode": "showcase_demo", "task_id": "pelican-svg"}, headers=auth)
        assert request.status_code == 202, request.text
        result = completed(client, request.json()["id"])
        assert result["status"] == "completed", result
        identifier = result["id"]
        report = result["reports"][0]["report"]
        assert report["kind"] == "showcase" and report["origin"] == "public_source_demo"
        assert report["cost"]["request_count"] == 0 and report["published_example"]["source"]
        prefix = f"/api/runs/{identifier}"
        text = client.get(prefix + "/download/showcase/jsonl").text
        assert showcase.parse_showcase_records(text)
        assert client.get(prefix + "/download/showcase/json").json() == report
        assert client.get(prefix + "/download/showcase/md").status_code == 200
        assert set(client.get(prefix + "/records?report=showcase").json()) == {"showcase"}
        replay = client.post("/api/runs", json={"mode": "showcase_offline", "evidence_jsonl": text}, headers=auth)
        assert replay.status_code == 202, replay.text
        replayed = completed(client, replay.json()["id"])
        assert replayed["status"] == "completed" and replayed["reports"][0]["report"] == report
        history = client.get("/api/runs").json()["runs"]
        assert len(history) == 2
        for row in history:
            summary = row["reports"][0]["report"]
            assert summary["candidate"] == report["candidate"]
            assert summary["task"]["title"] == report["task"]["title"]
            assert summary["public_source_demo"] is True
            assert "prompt" not in summary["task"] and "output" not in summary
        tampered = text.replace('"sequence": 1', '"sequence": 7', 1)
        assert client.post("/api/runs", json={"mode": "showcase_offline", "evidence_jsonl": tampered}, headers=auth).status_code == 422


def test_real_mode_uses_one_mock_request_and_does_not_persist_the_key(tmp_path, monkeypatch):
    original = showcase.run_showcase
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer " + KEY
        return httpx.Response(200, json={"choices": [{"message": {"content": '<svg xmlns="http://www.w3.org/2000/svg"/>'}, "finish_reason": "stop"}],
                                        "usage": {"completion_tokens": 8}})

    def run(config, out, *args, **kwargs):
        return original(config, out, *args, **kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(showcase, "run_showcase", run)
    with TestClient(create_app(tmp_path), base_url=BASE) as client:
        auth = headers(client)
        result = client.post("/api/runs", headers=auth, json={"mode": "showcase", "task_id": "pelican-svg", "max_tokens": 512,
            "candidate": {"api_base": "https://fixture.example/v1", "model": "fixture", "api_key": KEY}})
        assert result.status_code == 202
        job = completed(client, result.json()["id"])
        assert job["status"] == "completed", job
        assert len(calls) == 1 and job["reports"][0]["report"]["cost"]["requested_output_tokens"] == 512
        for path in tmp_path.rglob("*"):
            if path.is_file():
                assert KEY not in path.read_text(encoding="utf-8")


def test_cancellation_during_the_request_remains_cancelled_not_failed(tmp_path, monkeypatch):
    original = showcase.run_showcase
    entered, release = threading.Event(), threading.Event()

    def handler(_):
        entered.set()
        assert release.wait(5), "test did not release its mock request"
        return httpx.Response(200, json={"choices": [{"message": {"content": "<svg/>"}, "finish_reason": "stop"}]})

    monkeypatch.setattr(showcase, "run_showcase", lambda config, out, **kwargs:
        original(config, out, **kwargs, transport=httpx.MockTransport(handler)))
    with TestClient(create_app(tmp_path), base_url=BASE) as client:
        auth = headers(client)
        request = client.post("/api/runs", headers=auth, json={"mode": "showcase", "task_id": "pelican-svg",
            "candidate": {"api_base": "https://fixture.example/v1", "model": "fixture", "api_key": KEY}})
        assert request.status_code == 202 and entered.wait(5)
        identifier = request.json()["id"]
        try:
            assert client.post(f"/api/runs/{identifier}/cancel", json={}, headers=auth).status_code == 200
        finally:
            release.set()
        result = completed(client, identifier)
        assert result["status"] == "cancelled", result
        report = result["reports"][0]["report"]
        assert report["status"] == "cancelled" and report["collection"]["failure"] is None
        assert report["cost"]["request_count"] == 1


def test_embedding_changes_only_frame_ancestors_not_api_origins_or_script_policy(tmp_path):
    with TestClient(create_app(tmp_path / "default"), base_url=BASE) as client:
        policy = directives(client.get("/"))
        assert policy["frame-ancestors"] == ["'none'"]
        assert policy["script-src"] == ["'self'"]
    with TestClient(create_app(tmp_path / "embedded", embed_origins=[DSH]), base_url=BASE) as client:
        auth = headers(client)
        root = client.get("/")
        policy = directives(root)
        assert policy["frame-ancestors"] == [DSH]
        assert policy["script-src"] == ["'self'"] and "unsafe-inline" not in root.headers["content-security-policy"]
        assert client.get("/api/bootstrap").json()["embedding"]["origins"] == [DSH]
        payload = {"mode": "showcase_demo", "task_id": "pelican-svg"}
        assert client.post("/api/runs", json=payload, headers={**auth, "Origin": DSH}).status_code == 403
        assert client.get("/api/bootstrap", headers={"Origin": DSH}).status_code == 403
        assert client.post("/api/runs", json=payload).status_code == 403
        assert client.post("/api/runs", json=payload, headers={"X-Relay-Token": "invalid"}).status_code == 403
        assert client.post("/api/plan", json={"mode": "showcase", "task_id": "pelican-svg", "candidate": {
            "api_base": "https://fixture.example/v1", "model": "fixture"}}, headers={**auth, "Origin": BASE}).status_code == 200
        assert not client.get("/api/runs").json()["runs"]


def test_origin_normalization_preserves_only_exact_loopback_origins():
    assert normalize_embed_origins(["http://LOCALHOST:80/", "https://localhost:443", "http://[::1]:8080/",
                                    "http://127.0.0.1:8000", "http://localhost"]) == (
        "http://localhost", "https://localhost", "http://[::1]:8080", "http://127.0.0.1:8000")


@pytest.mark.parametrize("origin", ["*", "null", "https://example.com", "http://localhost.example.com:8080",
    "http://127.0.0.2:8080", "file:///app", "http://user:password@localhost:8080", "http://localhost:8080/ui",
    "http://localhost:8080?key=secret", "http://localhost:8080/#section", "http://localhost:0",
    "http://localhost:65536", "http://localhost:8080;script-src", "http://localhost:8080\n", "http://*:8080"])
def test_invalid_embed_origin_fails_before_server_start(tmp_path, origin):
    with pytest.raises(ValueError):
        create_app(tmp_path, embed_origins=[origin])


def test_embed_origins_must_be_a_small_explicit_list(tmp_path):
    for bad in (DSH, {DSH}, [DSH] * 9):
        with pytest.raises(ValueError):
            create_app(tmp_path, embed_origins=bad)


def save_html_job(app, identifier="a" * 24, *, name="showcase", format="html", source=None):
    source = source if source is not None else '<html><script>parent.location="https://example.invalid";fetch("/api/bootstrap")</script></html>'
    job = {"id": identifier, "mode": "showcase", "status": "completed", "created_at": "2026-10-31T00:00:00+00:00",
           "reports": [{"name": name, "report": {"kind": "showcase", "artifact": {"format": format, "source": source}}}]}
    manager = app.state.manager
    manager.jobs[identifier] = job
    manager._save(job)
    return identifier, source


@pytest.mark.parametrize("origins", [[], [DSH]])
def test_html_preview_has_a_server_enforced_opaque_sandbox(tmp_path, origins):
    app = create_app(tmp_path, embed_origins=origins)
    identifier, source = save_html_job(app)
    with TestClient(app, base_url=BASE) as client:
        response = client.get(f"/api/runs/{identifier}/showcase-preview")
        assert response.status_code == 200 and response.text == source
        assert response.headers["content-type"].startswith("text/html")
        policy = directives(response)
        assert policy["sandbox"] == ["allow-scripts"] and "allow-same-origin" not in response.headers["content-security-policy"]
        assert policy["default-src"] == ["'none'"] and policy["connect-src"] == ["'none'"]
        assert policy["script-src"] == ["'unsafe-inline'"]
        assert policy["frame-src"] == policy["object-src"] == policy["form-action"] == policy["base-uri"] == ["'none'"]
        assert policy["img-src"] == ["data:"]
        assert policy["frame-ancestors"] == ["'self'", *origins]
        for capability in ("camera", "microphone", "geolocation", "clipboard-read", "clipboard-write"):
            assert f"{capability}=()" in response.headers["permissions-policy"]
        assert response.headers["cache-control"] == "no-store" and response.headers["x-content-type-options"] == "nosniff"
        assert directives(client.get("/"))["script-src"] == ["'self'"]


@pytest.mark.parametrize("changes", [{"name": "comparison"}, {"format": "svg"}, {"source": "x" * (256 * 1024 + 1)}])
def test_preview_rejects_non_showcase_or_overlarge_html(tmp_path, changes):
    app = create_app(tmp_path)
    identifier, _ = save_html_job(app, **changes)
    with TestClient(app, base_url=BASE) as client:
        assert client.get(f"/api/runs/{identifier}/showcase-preview").status_code == 404
        assert client.get("/api/runs/ffff/showcase-preview").status_code == 404


def test_portable_instance_advertises_and_enforces_the_requested_frame_scope(tmp_path):
    data = prepare_data_dir(tmp_path / "便携 嵌入" / "data")
    identity = installation_id(data)
    server = PortableServer(data, identity, preferred_port=0, embed_origins=[DSH])
    try:
        server.start(timeout=30)
        assert existing_instance(data, identity, timeout=0, required_origins=[DSH]) == server.url
        assert existing_instance(data, identity, timeout=0, required_origins=["http://localhost:9999"]) is None
        assert existing_instance(data, identity, timeout=0) == server.url
        bootstrap = _request_json(server.url + "/api/bootstrap")
        assert bootstrap["embedding"]["origins"] == [DSH]
        stored = json.loads((data / ".portable-session.json").read_text(encoding="utf-8"))
        assert stored["embed_origins"] == [DSH]
    finally:
        server.stop(timeout=5)
    assert not (data / ".portable-session.json").exists()


def test_cli_exposes_the_explicit_embed_origin_flag(capsys):
    with pytest.raises(SystemExit) as result:
        cli_main(["ui", "--help"])
    assert result.value.code == 0
    assert "--embed-origin" in capsys.readouterr().out


def test_cli_showcase_plan_does_not_require_a_key_or_start_collection(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(showcase, "run_showcase", lambda *a, **k: pytest.fail("CLI plan collected responses"))
    path = tmp_path / "showcase-plan.json"
    path.write_text(json.dumps({"task_id": "pelican-svg", "max_tokens": 1024,
        "candidate": {"api_base": "https://fixture.example/v1", "model": "fixture"}}), encoding="utf-8")
    assert cli_main(["showcase-plan", "--config", str(path)]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["maximum_requests"] == 1 and plan["maximum_requested_output_tokens"] == 1024
    assert not list(tmp_path.glob("*/showcase_records.jsonl"))


def test_cli_public_demo_and_replay_preserve_a_zero_call_report(tmp_path):
    demo, replay = tmp_path / "demo", tmp_path / "replay"
    assert cli_main(["showcase-demo", "--task", "pelican-svg", "--out", str(demo)]) == 0
    assert cli_main(["showcase-replay", "--records", str(demo / "showcase_records.jsonl"), "--out", str(replay)]) == 0
    assert (demo / "report.json").read_bytes() == (replay / "report.json").read_bytes()
    report = json.loads((demo / "report.json").read_text(encoding="utf-8"))
    assert report["cost"]["request_count"] == 0 and report["origin"] == "public_source_demo"


@pytest.mark.parametrize("command", ["showcase-plan", "showcase"])
def test_cli_rejects_plaintext_keys_before_creating_output(tmp_path, capsys, command):
    path = tmp_path / "unsafe-config.json"
    path.write_text(json.dumps({"task_id": "pelican-svg", "candidate": {
        "api_base": "https://fixture.example/v1", "model": "fixture", "api_key": KEY}}), encoding="utf-8")
    argv = [command, "--config", str(path)]
    out = tmp_path / "output"
    if command == "showcase":
        argv += ["--out", str(out)]
    with pytest.raises(SystemExit) as result:
        cli_main(argv)
    assert result.value.code == 2
    assert KEY not in capsys.readouterr().err
    assert not out.exists()
