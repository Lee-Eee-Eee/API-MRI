import json

import pytest

from relay_audit.records import AuditRecord, content_hash
from relay_audit.stats import compare_records


def records(endpoint, answer, n=12, prompt_ids=("p1",)):
    return [AuditRecord(
        run_id=endpoint, endpoint_id=endpoint, prompt_id=prompt_id,
        replicate=i, prompt_hash=content_hash(prompt_id), config_hash=content_hash({"max_tokens": 64}),
        model_declared="fixture-a", reference_kind="synthetic_control", synthetic=True,
        status="success", response=answer,
        metadata={"expected_prompt_ids": list(prompt_ids), "expected_repeats": n},
    ) for prompt_id in prompt_ids for i in range(n)]


def compare(left, right, **kwargs):
    return compare_records(left, right, permutations=199, seed=11, **kwargs)


def test_actual_met_known_difference_and_seed_reproducibility():
    left, right = records("reference", "AAAAAAAA"), records("candidate", "BBBBBBBB")
    a, b = compare(left, right), compare(left, right)
    assert a == b
    assert a["status"] == "behavior_difference"
    item = a["prompt_results"][0]
    assert item["tested"]
    assert item["corrected_p"] >= 1 / 200
    assert item["raw_p"] == 0
    assert item["adjusted_p"] <= 0.05
    json.dumps(a, allow_nan=False)


def test_identical_outputs_do_not_certify_model_identity():
    result = compare(records("reference", "A"), records("candidate", "A"))
    assert result["status"] == "insufficient_evidence"
    assert result["prompt_results"][0]["statistical_non_rejection"]
    assert "power_not_calibrated" in result["reasons"]
    assert result["method"]["identity_certification"] is False


@pytest.mark.parametrize("field,value", [
    ("prompt_hash", content_hash("changed prompt")),
    ("config_hash", content_hash("changed config")),
    ("model_declared", "other-claim"),
])
def test_mismatched_conditions_are_not_reported_as_substitution(field, value):
    right = [r.model_copy(update={field: value}) for r in records("candidate", "B")]
    result = compare(records("reference", "A"), right)
    assert result["status"] == "insufficient_evidence"
    assert result["coverage"]["tested_prompts"] == 0


def test_too_few_samples_are_refused():
    result = compare(records("reference", "A", n=2), records("candidate", "B", n=2))
    assert result["status"] == "insufficient_evidence"
    assert not result["prompt_results"][0]["tested"]


def test_duplicate_retry_and_error_records_cannot_inflate_evidence():
    left, right = records("reference", "A"), records("candidate", "B")
    duplicate = compare(left, right + [right[0]])
    assert duplicate["status"] == "insufficient_evidence"
    right[0] = right[0].model_copy(update={"status": "error", "response": None, "error": "fixture failure"})
    failed = compare(left, right)
    assert failed["coverage"]["failed_records"]["candidate"] == 1
    assert failed["status"] == "insufficient_evidence"


def test_prompt_missing_on_both_sides_still_blocks_planned_family():
    left = [r for r in records("reference", "A", prompt_ids=("p1", "p2")) if r.prompt_id == "p1"]
    right = [r for r in records("candidate", "B", prompt_ids=("p1", "p2")) if r.prompt_id == "p1"]
    result = compare(left, right)
    assert result["coverage"]["family_size"] == 2
    assert result["coverage"]["untested_prompts"] == 1
    assert result["status"] == "insufficient_evidence"


def test_shared_missing_replicates_cannot_look_complete():
    result = compare(records("reference", "A")[1:], records("candidate", "B")[1:])
    assert result["status"] == "insufficient_evidence"
    assert "reference:planned_replicate_coverage_mismatch" in result["prompt_results"][0]["reasons"]


def test_unknown_reference_empty_outputs_and_overlong_outputs_refused():
    left = [r.model_copy(update={"synthetic": False, "reference_kind": "unknown"}) for r in records("reference", "A")]
    assert compare(left, records("candidate", "B"))["status"] == "insufficient_evidence"
    assert compare(records("reference", ""), records("candidate", ""))["status"] == "insufficient_evidence"
    assert compare_records(records("reference", "long"), records("candidate", "text"), max_completion_chars=3)["status"] == "insufficient_evidence"


def test_same_observations_cannot_be_copied_as_independent_reference():
    left = records("reference", "A")
    right = [row.model_copy(update={"endpoint_id": "candidate", "response": "B"}) for row in left]
    result = compare(left, right)
    assert result["status"] == "insufficient_evidence"
    assert "between_roles:overlapping_observation_ids" in result["reasons"]


def test_invalid_retry_metadata_refused_without_crashing():
    right = [row.model_copy(update={"metadata": {**row.metadata, "inspect_retries": "invalid"}}) for row in records("candidate", "B")]
    assert compare(records("reference", "A"), right)["status"] == "insufficient_evidence"

