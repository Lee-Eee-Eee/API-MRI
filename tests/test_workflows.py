"""Bounded pair workflows: loopback protocol tests incur no provider charges."""

from copy import deepcopy
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

import pytest

from relay_audit.probes import ProbeCheck, list_probes
from relay_audit.records import load_records, file_hash
from relay_audit.workflows import plan_pair, run_pair


KEYS = {"reference": "local-reference-secret-key", "candidate": "local-candidate-secret-key"}


def payload(url="https://example.com/v1"):
    return {
        "reference": {"endpoint_id": "reference", "model_declared": "declared-v1", "base_url": url,
                      "api_key": KEYS["reference"], "reference_kind": "official"},
        "candidate": {"endpoint_id": "candidate", "model_declared": "declared-v1", "base_url": url,
                      "api_key": KEYS["candidate"], "reference_kind": "third_party"},
        "prompts": [{"id": "p1", "input": "Return only 45.", "check": {"kind": "exact", "expected": "45"}}],
        "repeats": 2, "permutations": 19, "min_samples": 2,
    }


@contextmanager
def local_pair(*, echo_keys=False):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            auth = self.headers["Authorization"]
            requests.append((auth, data))
            content = " ".join(KEYS.values()) if echo_keys else "45"
            response = json.dumps({
                "id": "loopback-response", "object": "chat.completion", "created": 1, "model": "declared-v1",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_probe_catalog_is_bounded_validated_and_independent():
    probes = list_probes()
    assert 3 <= len(probes) <= 5
    assert len({probe["id"] for probe in probes}) == len(probes)
    for probe in probes:
        assert probe["description"] and probe["input"]
        ProbeCheck.model_validate(probe["check"])
    probes[0]["check"]["expected"] = "changed"
    assert list_probes()[0]["check"]["expected"] == "45"


def test_plan_does_not_need_credentials_or_write_files(monkeypatch):
    config = payload()
    for role in KEYS:
        del config[role]["api_key"]
        config[role]["api_key_env"] = "DOES_NOT_EXIST"
    monkeypatch.delenv("DOES_NOT_EXIST", raising=False)
    plan = plan_pair(config)
    assert plan["request_count"] == 4
    assert plan["max_output_tokens_total"] == 512
    assert all("api_key" not in plan[role] for role in KEYS)


@pytest.mark.parametrize("change", [
    lambda p: p["candidate"].update(model_declared="different-model"),
    lambda p: p.update(repeats=True),
    lambda p: p.update(repeats=101),
    lambda p: p.update(max_tokens=4097),
    lambda p: p.update(prompts=p["prompts"] * 2),
    lambda p: p.update(repeats=100, max_tokens=4096),
    lambda p: p["reference"].update(base_url="http://example.com/v1"),
    lambda p: p["reference"].update(base_url="https://user:pass@example.com/v1"),
    lambda p: p["reference"].update(base_url="https://example.com/v1?key=secret"),
    lambda p: p["prompts"][0].update(check={"kind": "json_object", "expected": "bad"}),
    lambda p: p.update(alpha=float("nan")),
    lambda p: p.update(attempt_timeout=100, timeout=10),
])
def test_invalid_plan_fails_before_directory_or_network(tmp_path, monkeypatch, change):
    from relay_audit import workflows
    config = payload()
    change(config)
    monkeypatch.setattr(workflows, "collect_endpoint_config", lambda *args, **kwargs: pytest.fail("network started"))
    with pytest.raises(ValueError, match="raw values omitted") as error:
        run_pair(config, tmp_path / "unused")
    assert all(key not in str(error.value) for key in KEYS.values())
    assert not (tmp_path / "unused").exists()


def test_both_keys_resolved_before_any_request(tmp_path, monkeypatch):
    from relay_audit import workflows
    config = payload()
    del config["candidate"]["api_key"]
    config["candidate"]["api_key_env"] = "ABSENT_PAIR_KEY"
    monkeypatch.delenv("ABSENT_PAIR_KEY", raising=False)
    monkeypatch.setattr(workflows, "collect_endpoint_config", lambda *args, **kwargs: pytest.fail("network started"))
    with pytest.raises(ValueError, match="candidate"):
        run_pair(config, tmp_path / "unused")
    assert not (tmp_path / "unused").exists()


def test_pair_loopback_interleaves_preserves_plan_and_redacts_both_keys(tmp_path, monkeypatch):
    from inspect_ai.log import read_eval_log
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    events = []
    with local_pair(echo_keys=True) as (url, requests):
        result = run_pair(payload(url), tmp_path / "pair", progress=events.append)
    assert [auth for auth, _ in requests] == [
        f"Bearer {KEYS[role]}" for role in ("reference", "candidate", "candidate", "reference")]
    assert result["collection"]["completed"] == 4
    assert result["collection"]["complete"] is True
    assert events[-1]["phase"] == "complete"
    for role in KEYS:
        rows = load_records(tmp_path / "pair" / f"{role}.jsonl")
        assert {row.replicate for row in rows} == {0, 1}
        assert len({row.run_id for row in rows}) == 1
        assert all(row.metadata["expected_prompt_ids"] == ["p1"] for row in rows)
        assert all(row.metadata["expected_repeats"] == 2 for row in rows)
        assert all(row.metadata["probe_check"] == {"kind": "exact", "expected": "45"} for row in rows)
        for row in rows:
            assert row.metadata["source_log_sha256"] == file_hash(Path(row.source_log))
            assert all(key not in read_eval_log(row.source_log, resolve_attachments=True).model_dump_json() for key in KEYS.values())
    for path in (tmp_path / "pair").rglob("*"):
        if path.is_file() and path.suffix != ".eval":
            assert all(key.encode() not in path.read_bytes() for key in KEYS.values())


def test_cancellation_keeps_checkpoints_and_blocks_full_conclusion(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    state = {"stop": False}

    def progress(event):
        if event["completed"] >= 2:
            state["stop"] = True

    with local_pair() as (url, requests):
        result = run_pair(payload(url), tmp_path / "partial", progress=progress, cancelled=lambda: state["stop"])
    assert len(requests) == 2
    assert result["status"] == "insufficient_evidence"
    assert result["collection"]["cancelled"] is True
    assert result["collection"]["complete"] is False
    assert "planned_collection_not_completed" in result["reasons"]
    for role in KEYS:
        rows = load_records(tmp_path / "partial" / f"{role}.jsonl")
        assert len(rows) == 1 and rows[0].metadata["expected_repeats"] == 2


def test_immediate_cancellation_and_existing_output(tmp_path):
    result = run_pair(payload(), tmp_path / "cancelled", cancelled=lambda: True)
    assert result["collection"]["completed"] == 0
    assert result["status"] == "insufficient_evidence"
    assert (tmp_path / "cancelled" / "report.json").exists()
    with pytest.raises(FileExistsError):
        run_pair(payload(), tmp_path / "cancelled")
