from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

import pytest

from relay_audit.collect import collect_endpoint, collect_fixture
from relay_audit.records import file_hash


def test_inspect_fixture_preserves_provenance_and_conditions(tmp_path):
    left = collect_fixture("reference", "honest", repeats=12, seed=11, log_dir=tmp_path / "left")
    right = collect_fixture("candidate", "substituted", repeats=12, seed=12, log_dir=tmp_path / "right")
    assert len(left) == len(right) == 36
    assert {row.prompt_hash for row in left} == {row.prompt_hash for row in right}
    assert {row.config_hash for row in left} == {row.config_hash for row in right}
    assert all(row.synthetic and row.status == "success" for row in left + right)
    assert all(row.model_declared == "research-fixture/declared-a" for row in left + right)
    assert all(row.metadata["expected_repeats"] == 12 for row in left)
    for row in left + right:
        assert row.metadata["source_log_sha256"] == file_hash(Path(row.source_log))
        assert row.metadata["call_started_at_utc"]
        assert row.metadata["inspect_retries"] == [0]
        assert "inspect_retries_raw" in row.metadata
    from relay_audit.stats import compare_records

    result = compare_records(left, right, permutations=199)
    assert result["coverage"]["tested_prompts"] == 3
    assert result["status"] == "behavior_difference"


@contextmanager
def local_endpoint(*, fail=False):
    requests = []
    key = "fixture-secret-key-ONLY-FOR-LOCAL-TEST"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, body, self.headers.get("Authorization")))
            payload = (
                {"error": {"message": f"test failure echoes {key}", "type": "fixture_failure", "code": "bad_request"}}
                if fail else {
                    "id": "fixture-response", "object": "chat.completion", "created": 1,
                    "model": "unverified-response-label",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "blue"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
                }
            )
            data = json.dumps(payload).encode()
            self.send_response(400 if fail else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", key, requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


@pytest.mark.parametrize("fail", [False, True])
def test_real_protocol_on_loopback_without_paid_calls_or_credential_export(tmp_path, monkeypatch, fail):
    from inspect_ai.log import read_eval_log

    with local_endpoint(fail=fail) as (url, key, requests):
        monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
        monkeypatch.setenv("RELAY_AUDIT_TEST_KEY", key)
        config = {
            "endpoint_id": "local-test", "model_declared": "declared-model", "base_url": url,
            "api_key_env": "RELAY_AUDIT_TEST_KEY", "reference_kind": "unknown", "repeats": 2,
            "prompts": [{"id": "p1", "input": "Return blue."}],
        }
        path = tmp_path / "config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        rows = collect_endpoint(path, log_dir=tmp_path / "inspect")
        assert len(rows) == len(requests) == 2
        assert all(row.model_declared == "declared-model" and not row.synthetic for row in rows)
        assert {row.status for row in rows} == ({"error"} if fail else {"success"})
        assert all(key not in row.model_dump_json() for row in rows)
        for log in {row.source_log for row in rows}:
            assert key not in read_eval_log(log, resolve_attachments=True).model_dump_json()
        assert all(item[0] == "/v1/chat/completions" for item in requests)
        assert all(item[1]["model"] == "declared-model" for item in requests)


@pytest.mark.parametrize("update", [
    {"base_url": "https://user:secret@example.com/v1"},
    {"base_url": "https://example.com/v1?key=secret"},
    {"reference_kind": "synthetic_control"},
    {"api_key": "plaintext-secret"},
])
def test_unsafe_or_mislabelled_config_rejected_before_network(tmp_path, update):
    config = {
        "endpoint_id": "test", "model_declared": "model", "base_url": "https://example.com/v1",
        "api_key_env": "RELAY_AUDIT_TEST_KEY", "prompts": [{"id": "p1", "input": "hello"}],
        **update,
    }
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="raw values omitted"):
        collect_endpoint(path, log_dir=tmp_path / "unused")
    assert not (tmp_path / "unused").exists()


def test_failure_path_sanitizes_or_discards_incomplete_log(tmp_path, monkeypatch):
    from relay_audit import collect

    log_dir = tmp_path / "failed-run"
    key = "fixture-secret-key-not-for-network"

    async def broken_execute(*args):
        (log_dir / "partial.eval").write_bytes(key.encode())
        raise RuntimeError("interrupted fixture")

    monkeypatch.setattr(collect, "_execute", broken_execute)
    with pytest.raises(RuntimeError, match="sanitation failed"):
        collect._run(None, "unused", collect._generation_config(), log_dir, 1, {"api_key": key})
    assert key.encode() not in (log_dir / "partial.eval").read_bytes()
