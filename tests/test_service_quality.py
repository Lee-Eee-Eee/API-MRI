"""Service measurements exercise transport fixtures, never a paid endpoint."""
import json

import httpx
import pytest

from relay_audit.records import content_hash
from relay_audit.service_quality import (
    demo_service_quality, parse_service_records, plan_service_quality,
    replay_service_quality, run_service_quality,
)

KEY = "fixture-secret-0123456789"


def config(**changes):
    return {"candidate": {"api_base": "https://fixture.example/v1", "model": "fixture", "api_key": KEY},
            "checks": ["stream"], "samples": 1, **changes}


class Clock:
    now = 0.0
    def __call__(self):
        return self.now


class TimedStream(httpx.SyncByteStream):
    def __init__(self, clock, chunks):
        self.clock, self.chunks = clock, chunks
    def __iter__(self):
        start = self.clock.now
        for at, chunk in self.chunks:
            self.clock.now = start + at
            yield chunk


def event(delta=None, *, usage=None, finish=None):
    value = {"choices": [] if usage is not None else [{"delta": delta or {}, **({"finish_reason": finish} if finish else {})}]}
    if usage is not None:
        value["usage"] = usage
    return ("data: " + json.dumps(value, ensure_ascii=False) + "\n\n").encode()


def stream_run(tmp_path, chunks, **changes):
    clock = Clock()
    def handler(request):
        assert request.headers["authorization"] == "Bearer " + KEY
        clock.now += .1
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=TimedStream(clock, chunks))
    return run_service_quality(config(**changes), tmp_path / "run", transport=httpx.MockTransport(handler), clock=clock)


def rows(tmp_path):
    return parse_service_records((tmp_path / "run" / "service_records.jsonl").read_text(encoding="utf-8"))


def rehash(records):
    previous = None
    for number, row in enumerate(records):
        row.update(sequence=number, previous_hash=previous)
        row.pop("record_hash", None)
        row["record_hash"] = content_hash(row)
        previous = row["record_hash"]
    return "\n".join(json.dumps(row, ensure_ascii=False) for row in records)


def test_plan_is_network_free_redacted_and_caps_the_whole_plan():
    value = plan_service_quality(config(checks=["stream", "tools"], samples=2))
    assert value["planned_requests"] == 6
    assert value["maximum_requests"] == 6
    assert value["maximum_requested_output_tokens"] == 768
    assert KEY not in json.dumps(value)
    limited = plan_service_quality(config(budget={"max_requests": 6, "max_output_tokens": 100}))
    assert limited["maximum_requests"] == 0


@pytest.mark.parametrize("changes", [
    {"samples": True}, {"max_tokens": 0}, {"timeout": 301}, {"checks": [["stream"]]},
    {"checks": []}, {"budget": {"max_requests": 33}}, {"unknown": 1},
    {"token_parameter": ["max_tokens"]}, {"stream_usage": 1},
    {"candidate": {"api_base": "http://public.example/v1", "model": "x"}},
    {"candidate": {"api_base": "https://user:pass@fixture.example/v1", "model": "x"}},
    {"candidate": {"api_base": "https://fixture.example/v1?key=secret", "model": "x"}},
])
def test_invalid_configs_rejected_before_network(changes):
    with pytest.raises(ValueError):
        plan_service_quality(config(**changes))


def test_stream_separates_headers_reasoning_and_first_visible_content(tmp_path):
    usage = {"prompt_tokens": 20, "completion_tokens": 40, "total_tokens": 60, "completion_tokens_details": {"reasoning_tokens": 10}}
    report = stream_run(tmp_path, [(.1, event({"role": "assistant"})), (.2, event({"reasoning_content": "thinking"})),
        (.4, event({"content": "hello"})), (.7, event({"content": " world"})), (.8, event(usage=usage)), (.9, b"data: [DONE]\n\n")])
    stream = report["service_quality"]["stream"]
    request = report["service_quality"]["requests"][0]
    assert request["timing"]["headers_ms"] == 100
    assert stream["first_reasoning_ms"]["median"] == 300
    assert stream["first_content_ms"]["median"] == 500
    assert stream["content_event_gaps_ms"]["median"] == 300
    assert stream["output_tokens_per_second"]["median"] == 40
    assert stream["visible_decode_tokens_per_second"]["median"] is None
    assert rows(tmp_path)[1]["content"] == "hello world"


def test_sse_event_split_across_utf8_and_transport_chunks(tmp_path):
    payload = event({"content": "中文"})
    position = payload.index("中".encode()) + 1
    report = stream_run(tmp_path, [(.1, payload[:position]), (.2, payload[position:]), (.3, event(finish="stop")), (.4, b"data: [DONE]\n\n")])
    assert report["service_quality"]["requests"][0]["content_excerpt"] == "中文"
    assert report["service_quality"]["stream"]["first_content_ms"]["median"] == 300
    assert report["service_quality"]["stream"]["output_tokens_per_second"]["count"] == 0
    assert report["cost"]["usage"]["completion_tokens"] is None
    assert rows(tmp_path)[1]["usage"]["completion_tokens"] is None


def test_multi_token_event_does_not_create_exact_decode_or_token_gap_rate(tmp_path):
    usage = {"completion_tokens": 80, "completion_tokens_details": {"reasoning_tokens": 0}}
    report = stream_run(tmp_path, [(.1, event({"content": "many words in the first event " * 4})),
        (.3, event({"content": "the rest"})), (.5, event(usage=usage)), (.6, b"data: [DONE]\n\n")])
    stream = report["service_quality"]["stream"]
    assert stream["output_tokens_per_second"]["median"] == pytest.approx(80 / .7, abs=1e-6)
    assert stream["visible_decode_tokens_per_second"]["median"] is None
    assert stream["content_event_gaps_ms"]["median"] == 200
    assert "inter_token_latency" not in stream


def test_explicit_output_parameter_and_usage_compatibility_without_probes(tmp_path):
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
            content=event({"content": "hello"}) + b"data: [DONE]\n\n")
    report = run_service_quality(config(token_parameter="max_completion_tokens", stream_usage=False),
        tmp_path / "run", transport=httpx.MockTransport(handler))
    assert len(calls) == 1
    assert calls[0]["max_completion_tokens"] == 128
    assert "max_tokens" not in calls[0]
    assert "stream_options" not in calls[0]
    assert report["cost"]["requested_output_tokens"] == 128
    assert report["service_quality"]["stream"]["output_tokens_per_second"]["median"] is None
    rows(tmp_path)


@pytest.mark.parametrize("usage", [None, {"completion_tokens": -1}, {"completion_tokens": True}, {"completion_tokens": "20"}])
def test_missing_or_malformed_usage_never_estimates_tokens_from_chunks(tmp_path, usage):
    chunks = [(.1, event({"content": "one"})), (.2, event({"content": " two"}))]
    if usage is not None:
        chunks.append((.3, event(usage=usage)))
    chunks.append((.4, b"data: [DONE]\n\n"))
    report = stream_run(tmp_path, chunks)
    assert report["service_quality"]["stream"]["output_tokens_per_second"]["median"] is None
    assert report["cost"]["usage"]["completion_tokens"] is None
    rows(tmp_path)


def test_partial_stream_keeps_text_but_does_not_score_throughput(tmp_path):
    report = stream_run(tmp_path, [(.2, event({"content": "partial"}))])
    row = report["service_quality"]["requests"][0]
    assert row["error"] == "incomplete_stream"
    assert row["content_excerpt"] == "partial"
    assert row["metrics"]["output_tokens_per_second"] is None
    assert report["service_quality"]["connection"]["successful_requests"] == 0
    rows(tmp_path)


def test_capped_output_is_visible_as_truncated_not_a_token_rate_claim(tmp_path):
    report = stream_run(tmp_path, [(.1, event({"content": "capped"})), (.2, event(finish="length")), (.3, b"data: [DONE]\n\n")])
    assert report["service_quality"]["stream"]["truncated_samples"] == 1
    rows(tmp_path)


def test_reasoning_only_is_not_a_successful_visible_response(tmp_path):
    report = stream_run(tmp_path, [(.1, event({"reasoning_content": "thinking"})), (.2, event(finish="length")), (.3, b"data: [DONE]\n\n")])
    row = report["service_quality"]["requests"][0]
    assert row["error"] == "empty_content"
    assert row["timing"]["first_reasoning_ms"] == 200
    assert row["timing"]["first_content_ms"] is None
    rows(tmp_path)


@pytest.mark.parametrize("status,error", [(401, "authentication_error"), (403, "permission_error"), (429, "rate_limited")])
def test_credential_and_rate_failures_stop_without_retry(tmp_path, status, error):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"retry-after": "60"}, json={"error": {"message": KEY}})
    report = run_service_quality(config(samples=4), tmp_path / "run", transport=httpx.MockTransport(handler))
    assert len(calls) == 1
    assert report["collection"]["failure"] == error
    assert report["service_quality"]["requests"][0]["response_headers"]["retry-after"] == "60"
    assert KEY not in (tmp_path / "run" / "service_records.jsonl").read_text(encoding="utf-8")
    rows(tmp_path)


def test_timeout_and_cancel_preserve_partial_records(tmp_path):
    report = stream_run(tmp_path, [(31, event({"content": "too late"}))])
    assert report["collection"]["failure"] == "request_timeout"
    rows(tmp_path)
    report = run_service_quality(config(), tmp_path / "cancel", cancelled=lambda: True,
        transport=httpx.MockTransport(lambda _: pytest.fail("cancelled run made a request")))
    assert report["collection"]["cancelled"]
    assert report["cost"]["request_count"] == 0


def test_unterminated_sse_line_is_bounded_before_newline(tmp_path):
    report = stream_run(tmp_path, [(.1, b"data: " + b"x" * 70000)])
    assert report["collection"]["failure"] == "response_size_limit"
    rows(tmp_path)


def test_budget_prevents_even_first_request_when_output_cap_too_small(tmp_path):
    report = run_service_quality(config(budget={"max_requests": 2, "max_output_tokens": 64}), tmp_path / "run",
        transport=httpx.MockTransport(lambda _: pytest.fail("budget exhausted run made a request")))
    assert report["cost"]["request_count"] == 0
    assert report["collection"]["stop_code"] == "budget_max_output_tokens"
    rows(tmp_path)


def test_secret_echo_split_across_sse_events_is_redacted_and_replayable(tmp_path):
    report = stream_run(tmp_path, [(.1, event({"content": KEY[:10]})), (.2, event({"content": KEY[10:]})), (.3, b"data: [DONE]\n\n")])
    evidence = (tmp_path / "run" / "service_records.jsonl").read_text(encoding="utf-8")
    assert KEY not in evidence
    assert KEY[:10] not in evidence
    assert report["service_quality"]["requests"][0]["content_excerpt"] == "[REDACTED][REDACTED]"
    rows(tmp_path)


def test_tool_forced_auto_no_call_and_roundtrip_are_separate(tmp_path):
    report = demo_service_quality(tmp_path / "run")
    assert {v["status"] for v in report["service_quality"]["tools"].values()} == {"passed"}
    assert report["synthetic"]
    assert report["cost"]["request_count"] == 0
    assert report["cost"]["simulated_request_count"] == 6
    assert report["cost"]["usage"]["completion_tokens"] is None
    assert report["service_quality"]["stream"]["response_ms"]["count"] == 2
    assert report["service_quality"]["connection"]["response_ms"]["count"] == 6
    assert report["service_quality"]["stream"]["response_ms"]["median"] > report["service_quality"]["connection"]["response_ms"]["median"]
    replay = replay_service_quality(tmp_path / "run" / "service_records.jsonl", tmp_path / "replay")
    assert replay == report


def test_wrong_tool_arguments_never_execute_fixture_or_get_roundtrip_pass(tmp_path):
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if body["messages"][0]["content"].endswith("Do not use any tools."):
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "HELLO"}, "finish_reason": "stop"}]})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{"id": "bad", "type": "function", "function": {"name": "lookup_sample", "arguments": '{"sample_id":"secret","field":"temperature"}'}}]}, "finish_reason": "tool_calls"}]})
    report = run_service_quality(config(checks=["tools"]), tmp_path / "run", transport=httpx.MockTransport(handler))
    assert len(calls) == 3
    assert all(body["tool_choice"] != "none" for body in calls)
    tools = report["service_quality"]["tools"]
    assert tools["tool_forced"]["status"] == "failed"
    assert tools["tool_roundtrip"]["status"] == "skipped"
    assert tools["tool_auto"]["status"] == "failed"
    assert tools["tool_no_call"]["status"] == "passed"
    rows(tmp_path)


def test_endpoint_rejects_tools_without_hiding_rejection_or_retrying(tmp_path):
    report = run_service_quality(config(checks=["tools"]), tmp_path / "run", transport=httpx.MockTransport(
        lambda _: httpx.Response(400, json={"error": {"message": "tools unsupported"}})))
    assert report["service_quality"]["tools"]["tool_forced"]["status"] == "error"
    assert report["service_quality"]["tools"]["tool_auto"]["status"] == "error"
    assert report["cost"]["request_count"] == 3
    rows(tmp_path)


def test_forced_pass_does_not_pass_auto_selection_of_distractor(tmp_path):
    def handler(request):
        body = json.loads(request.content)
        finish = "stop"
        if body["tool_choice"] == "none":
            fixture = json.loads(body["messages"][-1]["content"])
            message = {"content": f"{fixture['value']} {fixture['unit']} {fixture['receipt']}"}
        elif body["messages"][0]["content"].endswith("Do not use any tools."):
            assert len(body["tools"]) == 2
            message = {"content": "HELLO"}
        else:
            auto = body["tool_choice"] == "auto"
            if auto:
                assert {tool["function"]["name"] for tool in body["tools"]} == {"lookup_sample", "lookup_unit_legend"}
                assert "lookup_sample" not in body["messages"][0]["content"]
            message = {"content": None, "tool_calls": [{"id": "fixture-call", "type": "function", "function": {
                "name": "lookup_unit_legend" if auto else "lookup_sample",
                "arguments": '{"unit":"C"}' if auto else '{"sample_id":"MRI-17","field":"temperature"}'}}]}
            finish = "tool_calls"
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", **message}, "finish_reason": finish}]})
    report = run_service_quality(config(checks=["tools"]), tmp_path / "run", transport=httpx.MockTransport(handler))
    tools = report["service_quality"]["tools"]
    assert tools["tool_forced"]["status"] == "passed"
    assert tools["tool_roundtrip"]["status"] == "passed"
    assert tools["tool_auto"] == {"status": "failed", "reason": "wrong_tool", "request_index": 2}
    assert tools["tool_no_call"]["status"] == "passed"
    assert report["cost"]["request_count"] == 4
    rows(tmp_path)


def test_replay_rejects_forged_tool_assessment_even_with_rehashed_rows(tmp_path):
    demo_service_quality(tmp_path / "run")
    evidence = rows(tmp_path)
    forced = next(r for r in evidence if r.get("stage") == "tool_forced")
    forced["assessment"]["status"] = "failed"
    with pytest.raises(ValueError):
        parse_service_records(rehash(evidence))


def test_replay_rejects_derived_timing_drift_and_early_complete(tmp_path):
    demo_service_quality(tmp_path / "run")
    evidence = rows(tmp_path)
    evidence[1]["timing"]["first_content_ms"] += 1
    with pytest.raises(ValueError):
        parse_service_records(rehash(evidence))
    evidence = rows(tmp_path)
    evidence = [evidence[0], evidence[1], evidence[-1]]
    with pytest.raises(ValueError):
        parse_service_records(rehash(evidence))


def test_replay_rejects_changed_body_and_recomputed_usage(tmp_path):
    demo_service_quality(tmp_path / "run")
    evidence = rows(tmp_path)
    evidence[1]["request"]["messages"][0]["content"] = "different workload"
    with pytest.raises(ValueError):
        parse_service_records(rehash(evidence))
    evidence = rows(tmp_path)
    evidence[1]["usage"]["completion_tokens"] = 8000
    with pytest.raises(ValueError):
        parse_service_records(rehash(evidence))


def test_replay_rederives_401_classification_even_after_rehash(tmp_path):
    run_service_quality(config(), tmp_path / "run", transport=httpx.MockTransport(
        lambda _: httpx.Response(401, json={"error": {"message": "unauthorized"}})))
    evidence = rows(tmp_path)
    evidence[1]["error"] = None
    evidence[-1].update(stop_code="complete", failure=None)
    with pytest.raises(ValueError):
        parse_service_records(rehash(evidence))


@pytest.mark.parametrize("finish,error", [("error", "generation_error"), ("content_filter", "content_filtered")])
def test_sse_generation_error_and_filter_are_not_successes(tmp_path, finish, error):
    report = stream_run(tmp_path, [(.1, event({"content": "partial"}, finish=finish)), (.2, b"data: [DONE]\n\n")])
    assert report["service_quality"]["connection"]["successful_requests"] == 0
    assert report["service_quality"]["stream"]["successful_samples"] == 0
    assert report["service_quality"]["requests"][0]["error"] == error
    assert report["service_quality"]["requests"][0]["metrics"]["characters_per_second"] is None
    evidence = rows(tmp_path)
    evidence[1]["error"] = None
    with pytest.raises(ValueError):
        parse_service_records(rehash(evidence))


@pytest.mark.parametrize("finish,error", [("error", "generation_error"), ("content_filter", "content_filtered")])
def test_json_generation_error_and_filter_are_not_successes(tmp_path, finish, error):
    report = run_service_quality(config(checks=["tools"]), tmp_path / "run", transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "partial"}, "finish_reason": finish}]})))
    assert report["service_quality"]["connection"]["successful_requests"] == 0
    assert report["service_quality"]["requests"][0]["error"] == error
    assert report["service_quality"]["tools"]["tool_forced"]["status"] == "error"
    rows(tmp_path)


@pytest.mark.parametrize("finish,error", [("error", "generation_error"), ("content_filter", "content_filtered")])
def test_later_stop_event_does_not_erase_reported_generation_failure(tmp_path, finish, error):
    report = stream_run(tmp_path, [(.1, event({"content": "partial"}, finish=finish)),
        (.2, event(finish="stop")), (.3, b"data: [DONE]\n\n")])
    assert report["service_quality"]["requests"][0]["error"] == error
    assert report["service_quality"]["connection"]["successful_requests"] == 0
    rows(tmp_path)


def test_replay_rejects_cleared_incomplete_error_and_forged_finish_state(tmp_path):
    report = stream_run(tmp_path, [(.1, event({"content": "partial"}, finish="stop"))])
    assert report["service_quality"]["requests"][0]["error"] == "incomplete_stream"
    evidence = rows(tmp_path)
    evidence[1]["error"] = None
    with pytest.raises(ValueError):
        parse_service_records(rehash(evidence))
    evidence = rows(tmp_path)
    evidence[-1].update(stop_code="request_failed", failure="network_error")
    with pytest.raises(ValueError):
        parse_service_records(rehash(evidence))


def test_replay_rejects_erased_failure_after_timeout(tmp_path):
    stream_run(tmp_path, [(31, event({"content": "late"}))])
    evidence = rows(tmp_path)
    evidence[-1].update(stop_code="complete", failure=None)
    with pytest.raises(ValueError):
        parse_service_records(rehash(evidence))
