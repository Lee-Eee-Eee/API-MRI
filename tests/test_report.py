import csv
import json

import pytest

from relay_audit.diagnostics import build_diagnostics, check_response, summarize_records
from relay_audit.records import AuditRecord, content_hash, save_records
from relay_audit.report import markdown_report, reason_text, write_report


def record(*, prompt_id="p1", response="45", check=None, **kwargs):
    metadata = {"probe_check": check} if check is not None else {}
    values = dict(run_id="run", endpoint_id="endpoint", prompt_id=prompt_id, replicate=0,
                  prompt_hash=content_hash(prompt_id), config_hash=content_hash("config"),
                  model_declared="model", reference_kind="synthetic_control", synthetic=True,
                  status="success", response=response, metadata=metadata)
    values.update(kwargs)
    return AuditRecord(**values)


def test_missing_measurements_are_unknown_and_partial_usage_is_explicit():
    summary = summarize_records([record(), record(usage={"input_tokens": 4, "output_tokens": 0}, elapsed_seconds=2)])
    assert summary["reported_tokens"] == {"input_tokens": 4, "output_tokens": 0, "total_tokens": None}
    assert summary["reported_token_records"] == {"input_tokens": 1, "output_tokens": 1, "total_tokens": 0}
    assert summary["elapsed_seconds"] == {"count": 1, "median": 2, "p95": 2}
    assert summary["checks"] == {"evaluated": 0, "passed": 0, "pass_rate": None}
    empty = summarize_records([])
    assert empty["success_rate"] is None
    assert empty["elapsed_seconds"] == {"count": 0, "median": None, "p95": None}
    assert all(value is None for value in empty["reported_tokens"].values())


@pytest.mark.parametrize("kind,expected,response,wanted", [
    ("exact", "45", " 45\n", True),
    ("exact", "45", "The answer is 45", False),
    ("contains", "relay", "A relay endpoint", True),
    ("contains", "relay", "RELAY", False),
    ("json_object", {"active": True, "count": 3}, '{"count":3,"active":true}', True),
    ("json_object", {"active": True}, '{"active":1}', False),
    ("json_object", {"count": 3}, '{"count":3,"extra":0}', False),
    ("json_object", {"count": 3}, '```json\n{"count":3}\n```', False),
    ("json_object", {"count": 3}, '[3]', False),
    ("json_object", {"count": 3}, '{"count":NaN}', False),
])
def test_declared_task_checks(kind, expected, response, wanted):
    assert check_response(record(response=response, check={"kind": kind, "expected": expected})) is wanted


def test_failed_requests_count_as_failed_tasks_but_unsupported_checks_do_not():
    rows = [record(check={"kind": "exact", "expected": "45"}),
            record(status="error", response=None, error="request failed", check={"kind": "exact", "expected": "45"}),
            record(check={"kind": "unsupported", "expected": "45"}),
            record(check={"kind": ["invalid"], "expected": "45"}),
            record(check={"kind": "json_object", "expected": "invalid"})]
    summary = summarize_records(rows)
    assert summary["success_rate"] == 0.8 and summary["errors"] == 1
    assert summary["checks"] == {"evaluated": 2, "passed": 1, "pass_rate": 0.5}


def test_diagnostics_keep_missing_planned_prompts_and_nearest_rank_latency():
    rows = [record(elapsed_seconds=value) for value in range(1, 21)]
    result = build_diagnostics(rows, [], prompt_ids=["missing", "p1"])
    assert result["roles"]["reference"]["elapsed_seconds"] == {"count": 20, "median": 10.5, "p95": 19}
    assert [row["prompt_id"] for row in result["prompts"]] == ["missing", "p1"]
    assert result["prompts"][0]["roles"]["reference"]["records"] == 0


def test_report_exports_are_replayable_readable_and_csv_safe(tmp_path):
    dangerous_id = '  =HYPERLINK("https://example.invalid")'
    reference, candidate = tmp_path / "reference.jsonl", tmp_path / "candidate.jsonl"
    save_records(reference, [record(prompt_id=dangerous_id, response="private answer sk-should-not-export")])
    save_records(candidate, [record(prompt_id=dangerous_id)])
    result = {"status": "insufficient_evidence", "synthetic": True,
              "reasons": ["between_roles:model_declared_mismatch"],
              "prompt_results": [{"prompt_id": dangerous_id, "status": "insufficient_evidence",
                                  "reasons": ["reference:below_engineering_sample_threshold"], "tested": False,
                                  "adjusted_p": None}]}
    first = write_report(result, tmp_path / "first", reference=reference, candidate=candidate)
    second = write_report(result, tmp_path / "second", reference=reference, candidate=candidate)
    assert first == second
    assert "diagnostics" not in result and "reason_messages" not in result["prompt_results"][0]
    assert (tmp_path / "first/report.json").read_bytes() == (tmp_path / "second/report.json").read_bytes()
    csv_path = tmp_path / "first/prompts.csv"
    assert csv_path.read_bytes().startswith(b"\xef\xbb\xbf")
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 2 and all(row["prompt_id"] == "'" + dangerous_id for row in rows)
    assert rows[0]["reported_total_tokens"] == "" and rows[0]["latency_median_seconds"] == ""
    for path in (tmp_path / "first").iterdir():
        assert "sk-should-not-export" not in path.read_text(encoding="utf-8-sig")
    text = markdown_report(first)
    assert "## 请求表现" in text and "## 逐题结果" in text and "```json" not in text
    assert "模型名称或版本不一致" in text and "between_roles:" not in text
    with pytest.raises(FileExistsError):
        write_report(result, tmp_path / "first", reference=reference, candidate=candidate)


def test_empty_cancelled_pair_still_produces_report(tmp_path):
    reference, candidate = tmp_path / "reference.jsonl", tmp_path / "candidate.jsonl"
    reference.touch()
    candidate.touch()
    result = write_report({"status": "insufficient_evidence", "reasons": ["collection_cancelled"],
                           "collection": {"completed": 0, "total": 48, "cancelled": True}},
                          tmp_path / "report", reference=reference, candidate=candidate)
    assert result["diagnostics"]["roles"]["reference"]["records"] == 0
    assert "已取消" in (tmp_path / "report/report.md").read_text(encoding="utf-8")
    json.dumps(result, allow_nan=False)


def test_reason_translation_handles_mixed_records_and_engine_error():
    assert reason_text("reference:mixed_model_declared") == "参考端混用了不同的模型名称"
    assert "统计计算" in reason_text("met_execution_failed:ValueError")


def test_lab_and_research_sections_distinguish_settings_claims_and_observations():
    report = {"status": "insufficient_evidence", "synthetic": True,
              "lab": {"scenario": {"id": "prompt_rewrite", "name": "提示改写", "description": "相同可见回答",
                                   "mechanism": "由程序改写提示"},
                      "identifiability_demo": "替换与改写可以产生相同观察", "teaching_questions": ["还需什么证据？"]},
              "study": {"service_name": "![external](https://example.invalid/image)", "research_question": "数据如何流动？"},
              "claim_matrix": [{"label": "数据用途", "claim": "声称不留存", "observation": "没有内部数据证据",
                                "next_evidence": "需要核对产品条款"}]}
    text = markdown_report(report)
    assert "## 实验情境" in text and "程序设定的机制" in text
    assert "## 服务档案与研究问题" in text and "**本次观察**：没有内部数据证据" in text
    assert "![external]" not in text and "\\!\\[external\\]" in text
