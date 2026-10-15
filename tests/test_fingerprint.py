"""KBF parity, bounded localhost transport, and evidence replay checks."""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import threading
import time

import pytest

from relay_audit import fingerprint as fp
from relay_audit.records import content_hash, file_hash
from relay_audit.vendor.kbf.kbf_test import compute_verdict

PACK = "gpt-4.1-mini_20260915"
KEY = "test-secret-do-not-persist-123456"


def payload(base="https://api.example.invalid/v1", **extra):
    return {"reference_pack": PACK, "profile": "full", "candidate": {"api_base": base, "model": "custom-model-alias", "api_key": KEY}, **extra}


@pytest.fixture
def server():
    _, data = fp._load_pack(PACK)
    answers = {b["prompt"]: "\n".join(f"({i+1}) {fp.kbf.probe_consensus(p)}" for i, (_, p) in enumerate(b["items"]))
               for b in fp._batches(data)}
    state = {"mode": "match", "calls": [], "responses": []}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["calls"].append(request)
            text = request["messages"][1]["content"]
            response = "\n".join(f"({i}) {i}" for i in range(1, 11)) if text == fp.DISCOVERY_PROMPT else answers[text]
            code = 200
            if state["mode"] == "error":
                code, body = 401, {"error": KEY, KEY: {"api_key": KEY}}
            elif text == fp.DISCOVERY_PROMPT and (state["mode"] == "discovery_fail" or state["mode"] == "discovery_retry" and len(state["calls"]) <= 2):
                code, body = 400, {"error": "unsupported_parameter"}
            elif state["mode"] == "redirect":
                code, body = 302, {"error": "redirect not followed"}
            else:
                if state["mode"] == "retry" and text != fp.DISCOVERY_PROMPT:
                    response = "no numeric answers"
                if state["mode"] == "large":
                    response = "x" * 100000
                body = {"choices": [{"message": {"content": response + "\n# key echo " + KEY},
                                      "finish_reason": "length" if state["mode"] == "truncated" and text != fp.DISCOVERY_PROMPT else "stop"}],
                        "usage": {"prompt_tokens": 12, "completion_tokens": 25},
                        "metadata": {KEY: KEY, "api_key": KEY}}
                if state["mode"] == "malformed" and text != fp.DISCOVERY_PROMPT:
                    body = {"choices": [{"message": None}]}
            state["responses"].append(body)
            encoded = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            if code == 302:
                self.send_header("Location", "https://external.example.invalid/credential-trap")
            self.end_headers()
            try:
                if state["mode"] == "slow":
                    for start in range(0, len(encoded), 64):
                        self.wfile.write(encoded[start:start + 64])
                        self.wfile.flush()
                        time.sleep(0.2)
                else:
                    self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                pass
    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    state["base"] = f"http://127.0.0.1:{http.server_port}/v1"
    yield state
    http.shutdown()
    http.server_close()
    thread.join(timeout=2)


def rehash(rows):
    previous = None
    for i, row in enumerate(rows):
        row["sequence"], row["previous_hash"] = i, previous
        row["record_hash"] = content_hash({k: v for k, v in row.items() if k != "record_hash"})
        previous = row["record_hash"]
    return rows


def test_pinned_references_and_upstream_code_have_original_hashes():
    packs = fp.list_reference_packs()
    assert len(packs) == 26
    assert packs[0]["source_endpoint"] == "https://openrouter.ai/api/v1/chat/completions"
    assert all(p["source_verified"] is False for p in packs)
    upstream = Path(fp.__file__).parent / "vendor/kbf"
    for row in json.loads((upstream / "UPSTREAM.json").read_text())["files"]:
        assert file_hash(upstream / Path(row["path"]).name) == row["local_sha256"]


def test_plan_is_offline_bounded_and_does_not_read_credentials(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", lambda *a: pytest.fail("Unexpected network"))
    request = payload()
    request["candidate"].pop("api_key")
    request["candidate"]["api_key_env"] = "UNSET_FINGERPRINT_TEST_KEY"
    plan = fp.plan_fingerprint(request)
    assert plan["batch_count"] == 34
    assert plan["max_requests"] == 6 + 3 * 34
    assert plan["max_output_tokens_total"] == 4800 + 240 * 265
    assert "api_key_env" not in json.dumps(plan)


@pytest.mark.parametrize("variant,status", [("match", "no_difference_detected"), ("different", "difference_detected"), ("incomplete", "insufficient_evidence")])
def test_demo_replays_raw_responses_and_matches_upstream_statistics(tmp_path, monkeypatch, variant, status):
    monkeypatch.setattr(socket.socket, "connect", lambda *a: pytest.fail("Unexpected network"))
    report = fp.demo_fingerprint(PACK, tmp_path / "demo", variant)
    assert report["status"] == status and report["synthetic"] is True
    replay = fp.replay_fingerprint(PACK, tmp_path / "demo/fingerprint_records.jsonl", tmp_path / "replay")
    assert replay["fingerprint"]["summary"] == report["fingerprint"]["summary"]
    s = report["fingerprint"]["summary"]
    upstream = compute_verdict({"total": s["answered"], "correct": s["matches"], "hamming": s["mismatches"]}, fp._load_pack(PACK)[1])
    assert report["fingerprint"]["upstream_statistics"] == upstream
    assert "模型身份证明" in (tmp_path / "demo/report.md").read_text(encoding="utf-8")


def test_live_localhost_preserves_alias_redacts_echo_and_replays(tmp_path, server):
    report = fp.run_fingerprint(payload(server["base"]), tmp_path / "live")
    assert report["status"] == "no_difference_detected"
    assert report["synthetic"] is False
    assert report["candidate"]["model"] == "custom-model-alias"
    assert len(server["calls"]) == 35
    for file in (tmp_path / "live").iterdir():
        assert KEY.encode() not in file.read_bytes()
    rows = fp.parse_fingerprint_records((tmp_path / "live/fingerprint_records.jsonl").read_text(encoding="utf-8"), PACK)
    assert rows[1]["raw_response"]["metadata"]["api_key"] == "[REDACTED]"
    assert all(row["max_tokens"] <= 800 for row in server["calls"])
    assert report["fingerprint"]["probes"][0]["selected_request_sequence"] is not None
    replay = fp.replay_fingerprint(PACK, rows, tmp_path / "replay")
    assert replay["fingerprint"]["summary"] == report["fingerprint"]["summary"]
    assert (tmp_path / "live/report.json").stat().st_size < 2 * 1024 * 1024


@pytest.mark.parametrize("mode,calls", [("error", 1), ("redirect", 1), ("truncated", 2), ("large", 1), ("malformed", 2)])
def test_failed_or_truncated_requests_stop_with_partial_evidence(tmp_path, server, mode, calls):
    server["mode"] = mode
    report = fp.run_fingerprint(payload(server["base"]), tmp_path / mode)
    assert report["status"] == "insufficient_evidence"
    assert report["collection"]["state"] == "partial"
    assert report["collection"]["failure"]
    assert len(server["calls"]) == calls
    assert KEY not in (tmp_path / mode / "fingerprint_records.jsonl").read_text(encoding="utf-8")
    replay = fp.replay_fingerprint(PACK, tmp_path / mode / "fingerprint_records.jsonl", tmp_path / "replay")
    assert replay["status"] == "insufficient_evidence"


def test_format_retries_are_limited_and_coverage_stays_unknown(tmp_path, server):
    server["mode"] = "retry"
    report = fp.run_fingerprint(payload(server["base"]), tmp_path / "retries")
    # Upstream validation permits batches with fewer than three values to pass
    # the format gate even when every value is missing; coverage still fails.
    batches = fp._batches(fp._load_pack(PACK)[1])
    assert len(server["calls"]) == 1 + sum(3 if len(b["items"]) >= 3 else 1 for b in batches)
    assert report["status"] == "insufficient_evidence"
    assert report["fingerprint"]["summary"]["answered"] == 0
    fp.replay_fingerprint(PACK, tmp_path / "retries/fingerprint_records.jsonl", tmp_path / "replayed")


def test_cancel_checkpoints_completed_batch(tmp_path, server):
    cancelled = False
    def progress(event):
        nonlocal cancelled
        if event["phase"] == "collecting":
            assert (tmp_path / "cancel/fingerprint_records.jsonl").exists()
            cancelled = True
    report = fp.run_fingerprint(payload(server["base"]), tmp_path / "cancel", progress, lambda: cancelled)
    assert report["collection"]["cancelled"] is True
    assert report["collection"]["requests"] == 2
    assert report["status"] == "insufficient_evidence"


def test_cancel_before_first_request_and_missing_key_never_connect(tmp_path, monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", lambda *a: pytest.fail("Unexpected network"))
    report = fp.run_fingerprint(payload(), tmp_path / "cancel", cancelled=lambda: True)
    assert report["collection"]["requests"] == 0
    request = payload()
    request["candidate"].pop("api_key")
    with pytest.raises(ValueError, match="key missing"):
        fp.run_fingerprint(request, tmp_path / "missing")
    assert not (tmp_path / "missing").exists()


@pytest.mark.parametrize("change", ["hash", "prompt", "run", "duplicate", "settings", "pack"])
def test_replay_rejects_changed_probes_mixed_runs_and_settings(tmp_path, change):
    fp.demo_fingerprint(PACK, tmp_path / "demo")
    rows = fp.parse_fingerprint_records((tmp_path / "demo/fingerprint_records.jsonl").read_text(encoding="utf-8"), PACK)
    if change == "hash":
        rows[1]["raw_response"]["choices"][0]["message"]["content"] = "(1) 999"
    elif change == "prompt":
        rows[1]["request"]["messages"][1]["content"] = "Different probe"
    elif change == "run":
        rows[1]["run_id"] = "f" * 32
    elif change == "duplicate":
        rows.insert(2, copy.deepcopy(rows[1]))
    elif change == "settings":
        rows[0]["settings"]["temperature"] = 0.7
    else:
        rows[0]["reference_sha256"] = "0" * 64
    if change != "hash":
        rehash(rows)
    with pytest.raises(ValueError):
        fp.replay_fingerprint(PACK, rows, tmp_path / "replay")
    assert not (tmp_path / "replay").exists()


def test_replay_never_trusts_imported_scores_or_false_completion(tmp_path):
    fp.demo_fingerprint(PACK, tmp_path / "demo", "incomplete")
    rows = fp.parse_fingerprint_records((tmp_path / "demo/fingerprint_records.jsonl").read_text(encoding="utf-8"), PACK)
    rows[1]["candidate_answer"], rows[1]["match"] = 12345678, True
    rows[-1]["state"] = "complete"
    rehash(rows)
    report = fp.replay_fingerprint(PACK, rows, tmp_path / "replay")
    assert report["status"] == "insufficient_evidence"
    assert report["collection"]["state"] == "partial"
    assert all(p["candidate_value"] != 12345678 for p in report["fingerprint"]["probes"])


@pytest.mark.parametrize("bad", ["http://example.com/v1", "https://user:secret@example.com/v1", "https://example.com/v1?key=secret"])
def test_bad_urls_are_rejected_without_echo(bad):
    with pytest.raises(ValueError) as exc:
        fp.plan_fingerprint(payload(bad))
    assert "secret" not in str(exc.value)


def test_complete_enough_cannot_override_finish_length(tmp_path):
    fp.demo_fingerprint(PACK, tmp_path / "demo")
    rows = fp.parse_fingerprint_records((tmp_path / "demo/fingerprint_records.jsonl").read_text(encoding="utf-8"), PACK)
    rows[-2]["raw_response"]["choices"][0]["finish_reason"] = "length"
    rehash(rows)
    report = fp.replay_fingerprint(PACK, rows, tmp_path / "replay")
    assert report["status"] == "insufficient_evidence"


def test_maximum_evidence_stops_without_losing_prior_rows(tmp_path, server, monkeypatch):
    monkeypatch.setattr(fp, "MAX_EVIDENCE", 5000)
    report = fp.run_fingerprint(payload(server["base"]), tmp_path / "bounded")
    assert report["status"] == "insufficient_evidence"
    assert "evidence_size_limit" in (tmp_path / "bounded/fingerprint_records.jsonl").read_text(encoding="utf-8")


def test_slow_drip_obeys_total_deadline_between_chunks(tmp_path, server):
    server["mode"] = "slow"
    started = time.monotonic()
    report = fp.run_fingerprint(payload(server["base"], timeout=1), tmp_path / "slow")
    assert time.monotonic() - started < 2.5
    assert report["status"] == "insufficient_evidence"
    text = (tmp_path / "slow/fingerprint_records.jsonl").read_text(encoding="utf-8")
    assert "request_timeout" in text
    fp.parse_fingerprint_records(text, PACK)


def test_cancel_during_stream_leaves_replayable_partial(tmp_path, server):
    server["mode"] = "slow"
    stop = threading.Event()
    timer = threading.Timer(0.3, stop.set)
    timer.start()
    report = fp.run_fingerprint(payload(server["base"]), tmp_path / "slow-cancel", cancelled=stop.is_set)
    timer.join()
    assert report["collection"]["cancelled"] is True
    assert report["status"] == "insufficient_evidence"
    replay = fp.replay_fingerprint(PACK, tmp_path / "slow-cancel/fingerprint_records.jsonl", tmp_path / "replay")
    assert replay["collection"]["cancelled"] is True


@pytest.mark.parametrize("raw", [None, [], {"choices": [None]}, {"choices": ["wrong"]},
                                  {"choices": [{"message": None}]}, {"choices": [{"message": "wrong"}]}])
def test_malformed_provider_bodies_have_no_parseable_text(raw):
    row = {"http_status": 200, "error": None, "raw_response": raw}
    assert fp._raw_text(row) is None
    assert not fp._discovery_ok({**row, "strategy": "bare"})


def test_default_screening_selects_fixed_unchanged_batches_offline(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", lambda *a: pytest.fail("Unexpected network"))
    request = payload()
    request.pop("profile")
    plan = fp.plan_fingerprint(request)
    assert plan["profile"] == "screening"
    assert plan["budget"] == {"max_requests": 4, "max_output_tokens": 4096}
    assert plan["max_requests"] == 4
    assert plan["max_output_tokens_total"] <= 3200
    assert plan["batch_count"] == 3 and 0 < plan["probe_count"] <= 30
    assert len(plan["selection"]["domains"]) == 3
    assert plan["reference_probe_count"] == 265
    assert plan["budget_can_complete_minimum"]
    assert plan["selection"] == fp.plan_fingerprint(request)["selection"]
    all_batches = {b["id"]: b for b in fp._batches(fp._load_pack(PACK)[1])}
    selected, _ = fp._selection(fp._load_pack(PACK)[1], "screening")
    assert all(b == all_batches[b["id"]] for b in selected)


def test_screening_collects_once_per_batch_and_never_claims_statistical_identity(tmp_path, server):
    report = fp.run_fingerprint(payload(server["base"], profile="screening"), tmp_path / "screen")
    assert len(server["calls"]) == 4
    assert report["status"] == "insufficient_evidence"
    assert report["profile"] == "screening"
    assert report["fingerprint"]["screening"]["outcome"] == "no_differences_observed"
    assert report["fingerprint"]["summary"]["p_value"] is None
    assert report["fingerprint"]["upstream_statistics"] is None
    assert report["fingerprint"]["summary"]["total"] <= 30
    assert report["cost"]["requested_output_tokens"] == sum(c["max_tokens"] for c in server["calls"])
    assert report["cost"]["usage"]["prompt_tokens"] == 4 * 12
    assert report["cost"]["usage"]["completion_tokens"] == 4 * 25
    assert report["cost"]["usage"]["total_tokens"] is None
    assert report["cost"]["usage"]["complete"] is False
    replay = fp.replay_fingerprint(PACK, tmp_path / "screen/fingerprint_records.jsonl", tmp_path / "replay-screen")
    assert replay["cost"] == report["cost"]
    assert replay["fingerprint"] == report["fingerprint"]


def test_screening_format_failure_does_not_trigger_retries(tmp_path, server):
    server["mode"] = "retry"
    report = fp.run_fingerprint(payload(server["base"], profile="screening"), tmp_path / "screen")
    assert len(server["calls"]) == 4
    assert report["fingerprint"]["screening"]["outcome"] == "incomplete"
    fp.replay_fingerprint(PACK, tmp_path / "screen/fingerprint_records.jsonl", tmp_path / "replay-screen")


@pytest.mark.parametrize("budget,calls,stop_code", [
    ({"max_requests": 2}, 2, "budget_max_requests"),
    ({"max_output_tokens": 1599}, 1, "budget_max_output_tokens"),
    ({"max_output_tokens": 799}, 0, "budget_max_output_tokens"),
])
def test_budget_reserves_whole_request_before_sending(tmp_path, server, budget, calls, stop_code):
    report = fp.run_fingerprint(payload(server["base"], profile="screening", budget=budget), tmp_path / "capped")
    assert len(server["calls"]) == calls
    assert report["collection"]["state"] == "partial"
    assert report["collection"]["stop_code"] == stop_code
    assert report["cost"]["requested_output_tokens"] <= report["budget"]["max_output_tokens"]
    assert all(c["max_tokens"] == 800 for c in server["calls"])
    replay = fp.replay_fingerprint(PACK, tmp_path / "capped/fingerprint_records.jsonl", tmp_path / "replay-capped")
    assert replay["cost"] == report["cost"]
    assert replay["collection"]["stop_code"] == stop_code


def test_partial_answers_cannot_report_full_compatibility(tmp_path):
    fp.demo_fingerprint(PACK, tmp_path / "demo")
    rows = fp.parse_fingerprint_records((tmp_path / "demo/fingerprint_records.jsonl").read_text(encoding="utf-8"), PACK)
    batches = fp._batches(fp._load_pack(PACK)[1])
    for row in rows[1:-1]:
        items = batches[row["batch_id"]]["items"]
        retained = (len(items) + 1) // 2
        row["raw_response"]["choices"][0]["message"]["content"] = "\n".join(
            f"({i + 1}) {fp.kbf.probe_consensus(p) if i < retained else 'unknown'}" for i, (_, p) in enumerate(items))
    rehash(rows)
    report = fp.replay_fingerprint(PACK, rows, tmp_path / "partial-answers")
    assert report["fingerprint"]["summary"]["coverage"] >= .5
    assert report["fingerprint"]["upstream_verdict"] == "SAME"
    assert report["status"] == "insufficient_evidence"
    assert report["fingerprint"]["summary"]["p_value"] is None
    assert "missing_answers" in report["fingerprint"]["assessment_reasons"]


@pytest.mark.parametrize("change", ["selection", "budget", "cost", "profile"])
def test_replay_rejects_inconsistent_screening_ledger(tmp_path, change):
    fp.demo_fingerprint(PACK, tmp_path / "demo", profile="screening")
    rows = fp.parse_fingerprint_records((tmp_path / "demo/fingerprint_records.jsonl").read_text(encoding="utf-8"), PACK)
    if change == "selection":
        rows[0]["selection"]["batch_ids"][0] += 1
    elif change == "budget":
        rows[0]["budget"]["max_requests"] = 1
    elif change == "cost":
        rows[-1]["cost"]["requested_output_tokens"] = 0
    else:
        rows[0]["profile"] = "full"
    rehash(rows)
    with pytest.raises(ValueError):
        fp.replay_fingerprint(PACK, rows, tmp_path / "invalid")


def test_legacy_manifest_without_budget_remains_full_and_replayable(tmp_path):
    report = fp.demo_fingerprint(PACK, tmp_path / "demo")
    rows = fp.parse_fingerprint_records((tmp_path / "demo/fingerprint_records.jsonl").read_text(encoding="utf-8"), PACK)
    for name in ("profile", "budget", "selection"):
        rows[0].pop(name)
    for name in ("cost", "stop_code"):
        rows[-1].pop(name)
    rehash(rows)
    replay = fp.replay_fingerprint(PACK, rows, tmp_path / "legacy")
    assert replay["profile"] == "full"
    assert replay["fingerprint"]["summary"] == report["fingerprint"]["summary"]
    assert replay["status"] == "no_difference_detected"


@pytest.mark.parametrize("budget", [{"max_requests": 0}, {"max_requests": True}, {"max_output_tokens": -1},
                                     {"max_requests": 2.5}, {"max_output_tokens": None}, {"money": 10}])
def test_invalid_budgets_fail_before_network(budget):
    with pytest.raises(ValueError):
        fp.plan_fingerprint(payload(budget=budget))


@pytest.mark.parametrize("mode,calls,completed", [("discovery_retry", 4, 1), ("discovery_fail", 3, 0)])
def test_discovery_attempts_share_budget_and_keep_probe_reserve(tmp_path, server, mode, calls, completed):
    server["mode"] = mode
    report = fp.run_fingerprint(payload(server["base"], profile="screening"), tmp_path / "discovery")
    assert len(server["calls"]) == calls
    assert report["collection"]["completed"] == completed
    assert report["collection"]["stop_code"] == "budget_max_requests"
    assert report["collection"]["failure"] is None
    assert report["cost"]["requested_output_tokens"] == calls * 800
    fp.replay_fingerprint(PACK, tmp_path / "discovery/fingerprint_records.jsonl", tmp_path / "replay")


def test_usage_missing_partial_and_zero_are_distinct():
    rows = [{"request": {"max_tokens": 800}, "raw_response": {"usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}},
            {"request": {"max_tokens": 800}, "raw_response": {}}]
    cost = fp._cost(rows)
    assert cost["usage"]["total_tokens"] == 0
    assert cost["usage"]["missing_requests"] == 1
    assert cost["usage"]["complete"] is False
    assert fp._cost(rows[1:])["usage"]["total_tokens"] is None
    assert fp._cost(rows, synthetic=True)["usage"]["total_tokens"] is None


def test_legacy_report_can_be_rendered_without_inventing_costs(tmp_path):
    report = fp.demo_fingerprint(PACK, tmp_path / "demo")
    for name in ("profile", "budget", "cost"):
        report.pop(name)
    report["fingerprint"]["summary"].pop("reference_total")
    text = fp.markdown_fingerprint_report(report)
    assert "未记录" in text and "未知 / 未知 / 未知" in text
