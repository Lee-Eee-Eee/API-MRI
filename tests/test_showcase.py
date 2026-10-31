"""One-request visual trials and attributed public examples, without paid calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from relay_audit import showcase
from relay_audit.records import content_hash

KEY = "fixture-secret-0123456789"
SVG = '<svg xmlns="http://www.w3.org/2000/svg"><circle cx="2" cy="3" r="1"/></svg>'


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    folder = tmp_path / "catalog"
    (folder / "assets").mkdir(parents=True)
    asset = folder / "assets" / "pelican.svg"
    asset.write_text(SVG, encoding="utf-8")
    data = {"schema": showcase.CATALOG_SCHEMA,
            "tasks": [{"id": "pelican-svg", "title": "鹈鹕骑自行车", "prompt": "Generate an SVG of a pelican riding a bicycle", "format": "svg"},
                      {"id": "bouncing-ball", "title": "旋转六边形", "prompt": "Return a self contained HTML canvas animation.", "format": "html"}],
            "examples": [{"id": "published-pelican", "task_id": "pelican-svg", "model": "author-model-label", "asset": "assets/pelican.svg",
                "format": "svg", "sha256": hashlib.sha256(SVG.encode()).hexdigest(),
                "license": {"spdx": "MIT", "file": "LICENSES/MIT.txt"},
                "source": {"url": "https://example.invalid/published", "commit": "a" * 40}},
                {"id": "external-pelican", "task_id": "pelican-svg", "model": "another-label", "source": {"url": "https://example.invalid/external"}}]}
    (folder / "catalog.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(showcase, "DATA_DIR", folder)
    return data, folder


def config(**changes):
    return {"task_id": "pelican-svg", "candidate": {"api_base": "https://fixture.example/v1", "model": "fixture", "api_key": KEY}, **changes}


def response(content=SVG, finish="stop", **extras):
    return {"choices": [{"message": {"content": content, **extras}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 80, "total_tokens": 92}}


def run(tmp_path, data=None, status=200, **changes):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json=data if data is not None else response())

    result = showcase.run_showcase(config(**changes), tmp_path / "run", transport=httpx.MockTransport(handler))
    return result, calls


def evidence(tmp_path):
    return (tmp_path / "run" / "showcase_records.jsonl").read_text(encoding="utf-8")


def rehash(records):
    previous = None
    for number, row in enumerate(records):
        row.update(sequence=number, previous_hash=previous)
        row.pop("record_hash", None)
        row["record_hash"] = content_hash(row)
        previous = row["record_hash"]
    return "\n".join(json.dumps(row) for row in records)


def test_empty_catalog_is_readable_and_planning_is_network_free(tmp_path, monkeypatch):
    monkeypatch.setattr(showcase, "DATA_DIR", tmp_path / "missing")
    monkeypatch.setattr(httpx, "Client", lambda *a, **kw: pytest.fail("planning contacted a client"))
    assert showcase.list_showcase() == {"schema": showcase.CATALOG_SCHEMA, "tasks": [], "examples": []}
    with pytest.raises(ValueError, match="已有"):
        showcase.plan_showcase(config())


def test_plan_caps_one_request_and_keeps_the_original_prompt(catalog, monkeypatch):
    monkeypatch.setattr(httpx, "Client", lambda *a, **kw: pytest.fail("planning contacted a client"))
    value = showcase.plan_showcase(config())
    assert value["maximum_requests"] == value["planned_requests"] == 1
    assert value["maximum_requested_output_tokens"] == 2048
    assert value["network_retries"] == 0 and value["stream"] is False
    assert value["task"]["prompt"] == catalog[0]["tasks"][0]["prompt"]
    assert KEY not in json.dumps(value)
    no_key = config(candidate={"api_base": "https://fixture.example/v1", "model": "fixture"})
    assert showcase.plan_showcase(no_key)["planned_requests"] == 1


@pytest.mark.parametrize("changes", [{"max_tokens": True}, {"max_tokens": 255}, {"max_tokens": 4097},
    {"budget": {"max_requests": 10}}, {"timeout": 0}, {"token_parameter": "unsupported"},
    {"candidate": {"api_base": "http://remote.example/v1", "model": "x"}},
    {"candidate": {"api_base": "https://user:password@fixture.example/v1", "model": "x"}}])
def test_invalid_settings_are_rejected_before_a_request(catalog, changes):
    with pytest.raises(ValueError):
        showcase.plan_showcase(config(**changes))


def test_single_request_uses_exact_prompt_and_replays(catalog, tmp_path):
    result, calls = run(tmp_path, response("```svg\n" + SVG + "\n```", reasoning_content="a short plan"),
                        max_tokens=1024, token_parameter="max_completion_tokens")
    assert len(calls) == 1 and calls[0].headers["authorization"] == "Bearer " + KEY
    body = json.loads(calls[0].content)
    assert body == {"model": "fixture", "messages": [{"role": "user", "content": catalog[0]["tasks"][0]["prompt"]}],
                    "stream": False, "max_completion_tokens": 1024}
    assert result["status"] == "completed" and result["artifact"]["complete"]
    assert result["artifact"]["source"] == SVG and result["artifact"]["executed"] is False
    assert result["output"].startswith("```svg")
    assert result["reasoning_returned"] and result["reasoning_characters"] == len("a short plan")
    assert result["cost"]["request_count"] == 1 and result["cost"]["requested_output_tokens"] == 1024
    assert result["usage"]["completion_tokens"] == 80
    assert showcase.replay_showcase(evidence(tmp_path), tmp_path / "replay") == result
    with pytest.raises(FileExistsError):
        showcase.run_showcase(config(), tmp_path / "run", transport=httpx.MockTransport(lambda _: pytest.fail("overwrote")))


@pytest.mark.parametrize("status,error", [(401, "authentication_error"), (403, "permission_error"),
                                         (429, "rate_limited"), (500, "server_error"), (307, "http_error")])
def test_errors_and_redirects_never_retry_or_become_success(catalog, tmp_path, status, error):
    result, calls = run(tmp_path, response(), status=status)
    assert len(calls) == 1 and result["status"] == "failed" and result["error"] == error
    assert not result["artifact"]["complete"]
    assert showcase.replay_showcase(evidence(tmp_path), tmp_path / "replay")["error"] == error
    rows = showcase.parse_showcase_records(evidence(tmp_path))
    rows[-1]["reason"] = "completed"
    with pytest.raises(ValueError):
        showcase.parse_showcase_records(rehash(rows))


@pytest.mark.parametrize("finish,error,status", [("length", "output_truncated", "partial"),
    ("max_tokens", "output_truncated", "partial"), ("content_filter", "content_filtered", "failed"),
    ("error", "generation_error", "failed"), (None, "missing_finish_reason", "partial"),
    ("tool_calls", "unexpected_finish_reason", "failed")])
def test_finish_reason_controls_completeness_even_for_valid_svg(catalog, tmp_path, finish, error, status):
    result, calls = run(tmp_path, response(finish=finish))
    assert len(calls) == 1 and result["error"] == error and result["status"] == status
    assert result["artifact"]["format_valid"] and not result["artifact"]["complete"]
    assert showcase.replay_showcase(evidence(tmp_path), tmp_path / "replay") == result


def test_secret_echo_in_json_unicode_escapes_is_redacted(catalog, tmp_path):
    text = json.dumps(response(SVG.replace("</svg>", f"<text>{KEY}</text></svg>"), reasoning_content=KEY), ensure_ascii=True)
    text = text.replace(KEY, "".join("\\u" + format(ord(char), "04x") for char in KEY))
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=text, headers={"x-request-id": KEY}))
    result = showcase.run_showcase(config(), tmp_path / "run", transport=transport)
    for file in (tmp_path / "run").glob("*"):
        assert KEY not in file.read_text(encoding="utf-8")
    assert KEY not in json.dumps(result) and "[REDACTED]" in result["artifact"]["source"]
    assert showcase.replay_showcase(evidence(tmp_path), tmp_path / "replay") == result


def test_overlarge_response_drops_the_boundary_body_and_has_one_attempt(catalog, tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=(b"x" * showcase.MAX_RESPONSE_BYTES) + KEY.encode())

    result = showcase.run_showcase(config(), tmp_path / "run", transport=httpx.MockTransport(handler))
    assert result["error"] == "response_size_limit" and len(calls) == 1
    rows = showcase.parse_showcase_records(evidence(tmp_path))
    assert rows[1]["body_text"] == "" and rows[1]["body_truncated"] is True
    assert KEY not in evidence(tmp_path)
    assert showcase.replay_showcase(evidence(tmp_path), tmp_path / "replay")["error"] == "response_size_limit"


@pytest.mark.parametrize("transport_error", [httpx.ConnectError("private request details"), httpx.ReadTimeout("private request details")])
def test_transport_exceptions_are_saved_without_raw_exceptions(catalog, tmp_path, transport_error):
    def handler(_):
        raise transport_error

    result = showcase.run_showcase(config(), tmp_path / "run", transport=httpx.MockTransport(handler))
    assert result["error"] in {"network_error", "request_timeout"}
    assert "private request details" not in evidence(tmp_path)
    assert showcase.replay_showcase(evidence(tmp_path), tmp_path / "replay") == result


def test_cancel_before_request_uses_zero_budget(catalog, tmp_path):
    result = showcase.run_showcase(config(), tmp_path / "run", cancelled=lambda: True,
        transport=httpx.MockTransport(lambda _: pytest.fail("cancelled run made request")))
    assert result["status"] == "cancelled" and result["cost"]["request_count"] == 0
    assert len(showcase.parse_showcase_records(evidence(tmp_path))) == 2
    assert showcase.replay_showcase(evidence(tmp_path), tmp_path / "replay") == result


def test_cancellation_between_outer_check_and_wire_does_not_count_an_attempt(catalog, tmp_path):
    observations = 0

    def cancelled():
        nonlocal observations
        observations += 1
        return observations > 1

    result = showcase.run_showcase(config(), tmp_path / "run", cancelled=cancelled,
        transport=httpx.MockTransport(lambda _: pytest.fail("cancelled before HTTP attempt")))
    assert result["cost"]["request_count"] == result["cost"]["requested_output_tokens"] == 0
    assert result["collection"]["cancelled"] and result["collection"]["failure"] is None
    rows = showcase.parse_showcase_records(evidence(tmp_path))
    assert rows[1]["attempt_started"] is False
    assert showcase.replay_showcase(evidence(tmp_path), tmp_path / "replay") == result


def test_body_cancellation_is_preserved_as_a_sent_request(catalog, tmp_path):
    calls = 0

    def cancelled():
        nonlocal calls
        calls += 1
        return calls > 2

    result = showcase.run_showcase(config(), tmp_path / "run", cancelled=cancelled,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response())))
    assert result["status"] == "cancelled" and result["cost"]["request_count"] == 1
    assert result["collection"]["cancelled"] and result["collection"]["failure"] is None
    assert showcase.replay_showcase(evidence(tmp_path), tmp_path / "replay") == result


def test_svg_and_html_code_are_only_text_and_no_artifact_file_runs(catalog, tmp_path):
    target = tmp_path / "executed.txt"
    html = '<!doctype html><html><script>fetch("https://example.invalid/");</script><body>Example</body></html>'
    result, _ = run(tmp_path, response(html), task_id="bouncing-ball")
    assert result["artifact"]["format"] == "html" and result["artifact"]["complete"]
    assert result["artifact"]["source"] == html and not target.exists()
    assert not list((tmp_path / "run").glob("*.html"))
    # An XML entity document is not sent to a permissive resolver.
    dangerous = '<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///private">]><svg>&x;</svg>'
    other = tmp_path / "dangerous"
    result = showcase.run_showcase(config(), other, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response(dangerous))))
    assert not result["artifact"]["format_valid"] and result["artifact"]["problem"] == "xml_declaration_disallowed"


def test_public_demo_reuses_a_licensed_asset_without_calling_a_model(catalog, tmp_path, monkeypatch):
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: pytest.fail("public example used network"))
    result = showcase.demo_showcase(tmp_path / "demo", task_id="pelican-svg")
    assert result["origin"] == "public_source_demo" and result["public_source_demo"] is True
    assert result["candidate"]["model"] == "author-model-label"
    assert result["artifact"]["source"] == SVG and result["published_example"]["id"] == "published-pelican"
    assert result["cost"]["real_requests"] == result["cost"]["maximum_requests"] == 0
    source = (tmp_path / "demo" / "showcase_records.jsonl").read_text(encoding="utf-8")
    assert showcase.replay_showcase(source, tmp_path / "replay") == result
    assert json.loads((tmp_path / "replay" / "sampling_plan.json").read_text())["planned_requests"] == 0


def test_catalog_asset_requires_license_hash_and_safe_path(catalog):
    data, folder = catalog
    assert showcase.showcase_asset("published-pelican") == (folder / "assets" / "pelican.svg").resolve()
    with pytest.raises(ValueError):
        showcase.showcase_asset("external-pelican")
    with pytest.raises(ValueError):
        showcase.showcase_asset("../escape")
    for change in ({"asset": "../secret.svg"}, {"license": {"spdx": "unknown"}}, {"sha256": "0" * 64}):
        altered = deepcopy(data)
        altered["examples"][0].update(change)
        (folder / "catalog.json").write_text(json.dumps(altered))
        with pytest.raises(ValueError):
            showcase.showcase_asset("published-pelican")


def test_replay_rejects_extra_attempts_broken_hash_and_nonfinite_timing(catalog, tmp_path):
    run(tmp_path)
    rows = showcase.parse_showcase_records(evidence(tmp_path))
    extra = deepcopy(rows)
    extra.insert(2, deepcopy(rows[1]))
    with pytest.raises(ValueError):
        showcase.parse_showcase_records(rehash(extra))
    edited = deepcopy(rows)
    edited[1]["http_status"] = 401
    with pytest.raises(ValueError):
        showcase.parse_showcase_records("\n".join(json.dumps(row) for row in edited))
    edited = deepcopy(rows)
    edited[1]["elapsed_seconds"] = float("nan")
    with pytest.raises(ValueError):
        showcase.parse_showcase_records(rehash(edited))


def test_markdown_export_cannot_close_the_code_fence(catalog, tmp_path):
    content = '<svg><!-- ```\n![x](https://example.invalid/private)\n``` --></svg>'
    result, _ = run(tmp_path, response(content))
    text = (tmp_path / "run" / "report.md").read_text(encoding="utf-8")
    assert "````svg\n" in text and text.endswith("````\n")
    assert result["artifact"]["complete"]
