"""Offline policy comparison and strictly matched stratum eligibility."""
from collections import Counter

from relay_audit.identification_validation import _matched_pair, evaluate_stopping


def trajectory(first="a", passes=True, length=8):
    return [{"cells": n, "first": first, "top3": [first, "b", "c"],
             "distance": .1 if passes else .9, "gap": .5} for n in range(1, length + 1)]


def test_sequential_comparison_uses_same_eligible_labels_and_actual_stop_cost():
    gates = {str(n): {"max_distance": .2, "min_gap": .1} for n in range(3, 9)}
    known = {"a": trajectory(), "b": trajectory("b", passes=False), "short": trajectory(length=4)}
    result = evaluate_stopping(known, {"unknown": trajectory(passes=False)}, gates)["policies"]
    assert result["fixed"]["known_eligible"] == result["stable_shortlist"]["known_eligible"] == 2
    assert result["stable_shortlist"]["known_incomplete"] == 1
    assert result["fixed"]["mean_known_requests"] == 32
    assert result["stable_shortlist"]["mean_known_requests"] == 28
    assert result["stable_shortlist"]["mean_known_requests_saved_vs_32"] == 4
    assert result["stable_shortlist"]["known_wrong_accepted"] == 0
    assert result["stable_shortlist"]["unknown_accepted"] == 0


def test_sequential_analysis_reports_a_wrong_stable_candidate():
    gates = {str(n): {"max_distance": .2, "min_gap": .1} for n in range(3, 9)}
    rows = trajectory("wrong")
    rows[-1] = {**rows[-1], "first": "truth", "top3": ["truth", "wrong"]}
    result = evaluate_stopping({"truth": rows}, {}, gates)["policies"]
    assert result["fixed"]["known_wrong_accepted"] == 0
    assert result["stable_shortlist"]["known_wrong_accepted"] == 1
    assert result["stable_shortlist"]["mean_known_requests"] == 24


def test_source_pairs_need_enough_cells_and_disjoint_matched_samples():
    left = {f"c{i}": ["a"] * 10 for i in range(8)}
    right = {f"c{i}": ["b"] * 10 for i in range(8)}
    result = _matched_pair(left, right)
    assert result == {"shared_cells": 8, "between_jsd": 1, "within_jsd": 0, "excess_jsd": 1}
    right["c0"] = ["b"] * 9
    assert _matched_pair(left, right) is None
    assert _matched_pair(left, {}) is None


def test_matched_within_baseline_can_exceed_between_distance():
    a = {f"c{i}": ["x"] * 5 + ["y"] * 5 for i in range(8)}
    result = _matched_pair(a, a)
    assert result["between_jsd"] == 0
    assert result["within_jsd"] == 1
    assert result["excess_jsd"] == -1
