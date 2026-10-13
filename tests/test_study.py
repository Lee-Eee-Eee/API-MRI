import json

import pytest

from relay_audit.study import attach_study, claim_matrix, redact_study, validate_study
from relay_audit.diagnostics import build_diagnostics


@pytest.mark.parametrize("value", [
    {"observed_on": "2026-02-30"}, {"source_url": "javascript:alert(1)"},
    {"source_url": "https://user:secret@example.com"}, {"api_key": "private-secret"},
])
def test_study_validation_does_not_echo_bad_input(value):
    with pytest.raises(ValueError) as exc:
        validate_study(value)
    assert "private-secret" not in str(exc.value)
    assert "user:secret" not in str(exc.value)


def test_claims_are_preserved_as_unverified_and_link_to_evidence(tmp_path):
    study = {"case_id": "C01", "observed_on": "2026-10-13", "source_url": "https://example.invalid/policy",
             "price_claim": "每百万输入token 1元，缓存另计", "model_claim": "固定版本",
             "data_policy_claim": "不保存输入输出", "remedy_claim": "承诺故障补偿"}
    report = {"status": "behavior_difference", "reasons": [],
              "diagnostics": build_diagnostics([], [])}
    report["diagnostics"]["roles"]["candidate"].update(records=12, errors=2)
    original = json.dumps(report)
    (tmp_path / "report.json").write_text(original, encoding="utf-8")
    (tmp_path / "report.md").write_text("original", encoding="utf-8")
    result = attach_study(report, study, tmp_path)
    assert json.dumps(report) == original
    assert result["status"] == "behavior_difference"
    assert result["study"] == study
    rows = {row["field"]: row for row in result["claim_matrix"]}
    assert "未核对" in rows["price_claim"]["observation"]
    assert "没有" in rows["data_policy_claim"]["observation"]
    assert "2次错误" in rows["remedy_claim"]["observation"]
    assert json.loads((tmp_path / "study.json").read_text(encoding="utf-8"))["source_status"] == "researcher_entered_unverified"
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8")) == result
    with pytest.raises(FileExistsError):
        attach_study(result, study, tmp_path)


def test_blank_card_has_no_guessed_claims():
    assert validate_study({"case_id": " "}) == {}
    assert claim_matrix({}, {}) == []


def test_keys_accidentally_pasted_in_notes_are_removed(monkeypatch):
    monkeypatch.setenv("STUDY_TEST_KEY", "other-test-secret")
    value = {"notes": "call with private-test-secret or other-test-secret"}
    cleaned = redact_study(value, {"reference": {"api_key": "private-test-secret"},
                                   "candidate": {"api_key_env": "STUDY_TEST_KEY"}})
    assert "private-test-secret" not in cleaned["notes"]
    assert "other-test-secret" not in cleaned["notes"]
    assert value["notes"].endswith("other-test-secret")
