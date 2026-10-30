"""Shared-protocol retrieval, hard budget, partial cells and rejection contracts."""
import json
from collections import Counter

import pytest

from relay_audit import identification as ident
from relay_audit.cli import main


def payload(**overrides):
    return {"candidate": {"api_base": "https://example.test/v1", "model": "any-alias", "api_key": "secret-only-in-memory"}, **overrides}


@pytest.fixture
def small_bank(monkeypatch):
    cells = [{"id": f"cell-{i}", "lang": "en", "task_id": "num10-random", "normalize_as": "integer",
              "answer_space": "integer 1-10", "system": "One number only.", "user": f"Test number {i}?"} for i in range(4)]
    models = [{"model": f"maker/model-{i}", "label": f"Model {i}", "family": f"family-{i}",
               "counts": {c["id"]: {str(i + 1): 10} for c in cells}} for i in range(3)]
    bank = {"schema": "api-mri-identification-bank/1", "id": "test-bank", "source": {"data": "https://example.test/data"},
        "collected_at": "test", "built_on": "test", "reference_split": "test", "normalization": "test", "cells": cells, "models": models,
        "calibration": {"open_set_gates": {"3": {"max_distance": .1, "min_gap": .1}, "4": {"max_distance": .1, "min_gap": .1}}}}
    monkeypatch.setattr(ident, "load_identification_bank", lambda: bank)
    return bank


def reply(text="1", **extra):
    raw = {"choices": [{"message": {"content": text}, "finish_reason": "stop"}],
           "usage": {"prompt_tokens": 25, "completion_tokens": 1, "total_tokens": 26}, **extra}
    return {"raw_response": raw, "http_status": 200, "error": None, "elapsed_seconds": .1}


def test_normalizer_and_distance():
    cell = {"normalize_as": "integer", "lang": "zh", "answer_space": "integer 1-100"}
    assert ident.normalize_answer("四十二", cell) == ("42", "valid")
    assert ident.normalize_answer("١٧", cell) == ("17", "valid")
    assert ident.normalize_answer("101", cell) == ("101", "invalid")
    assert ident.jsd({"a": 10}, {"a": 2}) == 0
    assert ident.jsd({"a": 1}, {"b": 1}) == 1


def test_plan_does_not_need_kbf_or_network(small_bank, monkeypatch):
    monkeypatch.setattr(ident.fp, "_load_pack", lambda *a, **k: pytest.fail("identification must not depend on KBF packs"))
    plan = ident.plan_identification(payload())
    assert plan["candidate_count"] == 3
    assert plan["max_completed_cells"] == 4
    assert "secret-only-in-memory" not in json.dumps(plan)
    assert plan["protocol"]["max_tokens"] == 16


@pytest.mark.parametrize("override", [{"budget": {"max_requests": True}}, {"candidate_models": [{}]},
    {"candidate": {"api_base": "http://outside.test", "model": "x"}}, {"api2d_no_cache": "true"},
    {"budget": {"max_requests": 33}}, {"budget": {"max_output_tokens": 513}}])
def test_config_rejects_invalid_inputs(small_bank, override):
    with pytest.raises(ValueError):
        ident.plan_identification(payload(**override))


def test_partial_cell_never_enters_ranking_or_calibration(small_bank):
    observed = {"cell-0": Counter({"1": 4}), "cell-1": Counter({"2": 3})}
    rows = ident.rank_candidates(small_bank, small_bank["models"], observed)
    assert rows[0]["model"] == "maker/model-0"
    assert all(r["compared_cells"] == 1 for r in rows)


def test_budget_counts_actual_requests_and_preserves_partial(small_bank, monkeypatch, tmp_path):
    monkeypatch.setattr(ident.fp, "_call", lambda *a, **k: reply())
    result = ident.run_identification(payload(budget={"max_requests": 5, "max_output_tokens": 80}), tmp_path / "run")
    assert result["cost"]["request_count"] == 4
    assert result["cost"]["requested_output_tokens"] == 64
    assert result["cost"]["usage"]["prompt_tokens"] == 100
    assert result["coverage"]["completed_cells"] == 1
    # Do not spend the last request on a cell that cannot reach four samples.
    assert len(result["coverage"]["partial_cells"]) == 0
    assert result["status"] == "insufficient_evidence"
    assert result["probability"] is None


def test_mid_cell_failure_is_saved_but_not_ranked(small_bank, monkeypatch, tmp_path):
    called = []
    def call(*args):
        called.append(1)
        return reply() if len(called) < 6 else {"raw_response": None, "http_status": 500, "error": "http_500", "elapsed_seconds": .1}
    monkeypatch.setattr(ident.fp, "_call", call)
    result = ident.run_identification(payload(budget={"max_requests": 12}), tmp_path / "partial")
    assert result["cost"]["real_requests"] == 6
    assert result["coverage"]["completed_cells"] == 1
    assert len(result["coverage"]["partial_cells"]) == 1
    assert all(r["compared_cells"] == 1 for r in result["ranking"])
    assert result["calibration"]["applicable_gate"] is None


@pytest.mark.parametrize("cached", [True, "hit"])
def test_response_cache_is_not_independent_sample(small_bank, monkeypatch, tmp_path, cached):
    monkeypatch.setattr(ident.fp, "_call", lambda *a, **k: reply(cache=cached))
    result = ident.run_identification(payload(), tmp_path / str(cached))
    assert result["stop_reason"] == "response_cache_hit"
    assert result["coverage"]["valid_answers"] == 0
    assert result["cost"]["real_requests"] == 1
    assert result["cost"]["usage"]["completion_tokens"] == 1


def test_reasoning_stops_and_is_charged(small_bank, monkeypatch, tmp_path):
    raw = reply()
    raw["raw_response"]["choices"][0]["message"]["reasoning_content"] = "unexpected hidden tokens"
    monkeypatch.setattr(ident.fp, "_call", lambda *a, **k: raw)
    result = ident.run_identification(payload(), tmp_path / "reasoning")
    assert result["stop_reason"] == "reasoning_returned"
    assert result["status"] == "insufficient_evidence"
    assert result["cost"]["real_requests"] == 1


def test_no_cache_header_is_explicit_and_replayable(small_bank, monkeypatch, tmp_path):
    headers = []
    def call(client, config, *args):
        headers.append(config["_headers"])
        return reply()
    monkeypatch.setattr(ident.fp, "_call", call)
    result = ident.run_identification(payload(api2d_no_cache=True, budget={"max_requests": 12}), tmp_path / "run")
    assert headers == [{"x-api2d-no-cache": "1"}] * 12
    replay = ident.replay_identification(tmp_path / "run" / "identification_records.jsonl", tmp_path / "replay")
    assert replay["ranking"] == result["ranking"]
    assert replay["status"] == result["status"] == "candidate_shortlist"


def test_demo_replay_and_cli_are_offline(small_bank, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(ident.fp, "_call", lambda *a, **k: pytest.fail("offline workflows must not call API"))
    result = ident.demo_identification(tmp_path / "demo", model_id="maker/model-1")
    assert result["synthetic"] is True
    assert result["cost"]["real_requests"] == 0
    assert result["ranking"][0]["model"] == "maker/model-1"
    assert result["calibration"]["rank_stability"]["tested_cells"] >= 1
    assert main(["identify-replay", "--records", str(tmp_path / "demo" / "identification_records.jsonl"), "--out", str(tmp_path / "cli")]) == 0
    assert "概率" in capsys.readouterr().out
    rows = [json.loads(line) for line in (tmp_path / "demo" / "identification_records.jsonl").read_text(encoding="utf-8").splitlines()]
    rows[1]["cell_id"] = "cell-0"
    with pytest.raises(ValueError):
        ident.parse_identification_records("\n".join(map(json.dumps, rows)))


def test_subset_has_no_borrowed_calibration(small_bank, monkeypatch, tmp_path):
    monkeypatch.setattr(ident.fp, "_call", lambda *a, **k: reply())
    result = ident.run_identification(payload(candidate_models=["maker/model-0", "maker/model-1"], budget={"max_requests": 12}), tmp_path / "subset")
    assert result["status"] == "insufficient_evidence"
    assert result["calibration"]["applicable_gate"] is None


@pytest.mark.parametrize("usage", [[1], {"completion_tokens_details": [1]}])
def test_malformed_usage_stops_without_losing_evidence(small_bank, monkeypatch, tmp_path, usage):
    monkeypatch.setattr(ident.fp, "_call", lambda *a, **k: reply(usage=usage))
    result = ident.run_identification(payload(), tmp_path / "malformed")
    assert result["stop_reason"] == "transport_or_format_error"
    assert result["cost"]["real_requests"] == 1
    assert (tmp_path / "malformed" / "identification_records.jsonl").exists()


def test_reasoning_usage_is_saved_as_reported_subtotal(small_bank, monkeypatch, tmp_path):
    raw = reply(usage={"completion_tokens": 9, "completion_tokens_details": {"reasoning_tokens": 8}})
    monkeypatch.setattr(ident.fp, "_call", lambda *a, **k: raw)
    result = ident.run_identification(payload(), tmp_path / "reasoning-cost")
    assert result["stop_reason"] == "reasoning_returned"
    assert result["cost"]["usage"]["reasoning_tokens"] == 8
    assert result["cost"]["usage"]["reasoning_missing_requests"] == 0
    assert result["cost"]["usage"]["prompt_tokens"] is None


def test_failure_after_complete_cells_retains_ranking_but_abstains(small_bank, monkeypatch, tmp_path):
    count = 0
    def call(*a, **k):
        nonlocal count
        count += 1
        return reply() if count <= 12 else {"http_status": 503, "raw_response": None, "error": "http_503"}
    monkeypatch.setattr(ident.fp, "_call", call)
    result = ident.run_identification(payload(budget={"max_requests": 16}), tmp_path / "late-failure")
    assert result["coverage"]["completed_cells"] == 3
    assert result["ranking"][0]["model"] == "maker/model-0"
    assert result["status"] == "insufficient_evidence"
    assert result["cost"]["real_requests"] == 13


def test_local_http_protocol_cache_stop_and_secret_redaction(small_bank, tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((dict(self.headers), body))
            raw = reply()["raw_response"]
            data = json.dumps(raw).encode()
            self.send_response(200)
            if len(seen) == 2:
                self.send_header("x-api2d-cache", "HIT")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        key = "local-test-key-not-for-logs"
        result = ident.run_identification(payload(candidate={"api_base": f"http://127.0.0.1:{server.server_port}/v1", "model": "alias", "api_key": key}, api2d_no_cache=True), tmp_path / "http")
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
    assert len(seen) == result["cost"]["real_requests"] == 2
    assert seen[0][0]["Authorization"] == "Bearer " + key
    assert seen[0][0]["x-api2d-no-cache"] == "1"
    assert seen[0][1]["max_tokens"] == 16
    assert seen[0][1]["reasoning"] == {"enabled": False}
    assert result["stop_reason"] == "response_cache_hit"
    assert result["coverage"]["completed_cells"] == 0
    assert result["cost"]["usage"]["prompt_tokens"] == 50
    for file in (tmp_path / "http").iterdir():
        assert key not in file.read_text(encoding="utf-8")
