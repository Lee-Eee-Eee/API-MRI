from pathlib import Path

import pytest

from relay_audit.lab import list_scenarios, run_scenario
from relay_audit.records import file_hash, load_records


def test_scenario_cards_are_complete_independent_copies():
    cards = list_scenarios()
    assert {card["id"] for card in cards} == {
        "honest", "substitution", "mixed_route", "prompt_rewrite", "output_cap", "unstable"}
    for card in cards:
        assert all(card[field] for field in ("name", "description", "mechanism", "expected_signal", "alternatives"))
    cards[0]["alternatives"].append("modified")
    assert "modified" not in list_scenarios()[0]["alternatives"]


@pytest.mark.parametrize("scenario", [card["id"] for card in list_scenarios()])
def test_scenarios_keep_matched_complete_synthetic_evidence(tmp_path, scenario):
    out = tmp_path / scenario
    result = run_scenario(scenario, out, repeats=12, permutations=199)
    assert result["synthetic"] is True
    assert result["lab"]["real_model_calls"] == 0
    assert result["lab"]["all_values_synthetic"] is True
    assert result["lab"]["power_calibration"] is False
    assert result["lab"]["scenario"]["id"] == scenario
    assert result["lab"]["records_generated"] == 72
    assert (out / "report.json").exists() and (out / "report.md").exists()
    assert (out / "prompts.csv").exists()
    roles = {role: load_records(out / f"{role}.jsonl") for role in ("reference", "candidate")}
    rows = roles["reference"] + roles["candidate"]
    assert len(rows) == 72
    assert all(row.synthetic and row.reference_kind == "synthetic_control" for row in rows)
    assert len({row.config_hash for row in rows}) == len({row.model_declared for row in rows}) == 1
    assert len({row.run_id for row in rows}) == 2
    assert {row.prompt_hash for row in roles["reference"]} == {row.prompt_hash for row in roles["candidate"]}
    for row in rows:
        assert row.source_log is None
        assert row.metadata["expected_repeats"] == 12
        assert row.metadata["expected_prompt_ids"] == ["arithmetic", "json_format", "extract_order"]
        assert row.metadata["sampling_plan_sha256"] == file_hash(out / "sampling_plan.json")
        assert row.metadata["all_values_synthetic"] is True
    diagnostics = result["diagnostics"]["roles"]
    assert diagnostics["reference"]["checks"]["pass_rate"] == 1
    if scenario == "honest":
        assert diagnostics["candidate"]["checks"]["pass_rate"] == 1
    elif scenario == "unstable":
        assert diagnostics["candidate"]["errors"] == 9
        assert diagnostics["candidate"]["elapsed_seconds"]["p95"] > 8
        assert result["status"] == "insufficient_evidence"
    elif scenario == "mixed_route":
        assert 0 < diagnostics["candidate"]["checks"]["pass_rate"] < 1
        assert {row.metadata["simulated_route"] for row in roles["candidate"]} == {"reference_pattern", "alternate_pattern"}
    else:
        assert diagnostics["candidate"]["checks"]["pass_rate"] == 0
        assert result["status"] == "behavior_difference"


def test_two_constructed_causes_have_identical_observable_traces(tmp_path):
    results = {}
    for scenario in ("substitution", "prompt_rewrite"):
        results[scenario] = run_scenario(scenario, tmp_path / scenario, repeats=8, permutations=99)
    for role in ("reference", "candidate"):
        traces = []
        for scenario in results:
            rows = load_records(tmp_path / scenario / f"{role}.jsonl")
            traces.append([(row.prompt_id, row.replicate, row.prompt_hash, row.config_hash, row.response,
                            row.status, row.error, row.elapsed_seconds, row.usage) for row in rows])
        assert traces[0] == traces[1]
    assert results["substitution"]["prompt_results"] == results["prompt_rewrite"]["prompt_results"]
    assert results["substitution"]["lab"]["scenario"]["mechanism"] != results["prompt_rewrite"]["lab"]["scenario"]["mechanism"]


@pytest.mark.parametrize("changes", [
    {"scenario": "unknown"}, {"repeats": 1}, {"repeats": 101}, {"repeats": True},
    {"seed": -1}, {"permutations": 18}, {"min_samples": 1}, {"alpha": float("nan")},
])
def test_lab_validation_precedes_any_output(tmp_path, changes):
    values = {"scenario": "honest", "out": tmp_path / "unused", **changes}
    with pytest.raises(ValueError, match="Invalid lab settings"):
        run_scenario(**values)
    assert not values["out"].exists()


def test_existing_lab_output_is_preserved(tmp_path):
    out = tmp_path / "existing"
    out.mkdir()
    marker = out / "evidence.txt"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        run_scenario("honest", out)
    assert marker.read_text(encoding="utf-8") == "keep"
