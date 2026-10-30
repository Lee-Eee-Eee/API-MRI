"""Offline sensitivity, sequential-budget and source-stratum stress checks.

No model requests.  Original corpus files are opened as data, never executed.
The historical holdout has already been inspected: new policies evaluated here
are exploratory reanalyses, not fresh confirmatory test sets.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import itertools
import json
from pathlib import Path
import statistics
import zipfile

from .identification import (BLOCK, TOKEN_CAP, _stage, jsd, load_identification_bank,
                             select_next_cell, should_stop_stable)
from .identification_build import DATA_MD5, corpus, simulate


def _as_stages(trajectory: list[dict], gates: dict) -> list[dict]:
    result = []
    for row in trajectory:
        gate = gates.get(str(row["cells"]))
        result.append({"completed_cells": row["cells"], "first": row["first"],
            "fixed_horizon_gate_passed": bool(gate and row["distance"] <= gate["max_distance"]
                                                and row["gap"] >= gate["min_gap"])})
    return result


def evaluate_stopping(known: dict, unknown: dict, gates: dict) -> dict:
    """Compare the same trajectories under fixed and sequential stopping."""
    policies = {}
    for policy in ("fixed", "stable_shortlist"):
        counts = Counter()
        used, saved, prompt_bytes = [], [], []
        for kind, trajectories in (("known", known), ("unknown", unknown)):
            for model_id, trajectory in sorted(trajectories.items()):
                if len(trajectory) < 8:
                    counts[kind + "_incomplete"] += 1
                    continue
                stages = _as_stages(trajectory, gates)
                horizon = 8
                if policy == "stable_shortlist":
                    horizon = next((n for n in range(6, 9) if should_stop_stable(stages[:n])), 8)
                row = trajectory[horizon - 1]
                gate = gates.get(str(horizon))
                accepted = bool(gate and row["distance"] <= gate["max_distance"] and row["gap"] >= gate["min_gap"])
                counts[kind + "_eligible"] += 1
                counts[kind + "_accepted"] += accepted
                if kind == "known":
                    counts["known_top1_correct"] += row["first"] == model_id
                    counts["known_top3_correct"] += model_id in row["top3"]
                    counts["known_correct_accepted"] += accepted and row["first"] == model_id
                    counts["known_wrong_accepted"] += accepted and row["first"] != model_id
                    counts["known_early_stopped"] += horizon < 8
                    used.append(horizon * BLOCK)
                    saved.append((8 - horizon) * BLOCK)
                    if row.get("input_prompt_utf8_bytes") is not None:
                        prompt_bytes.append(row["input_prompt_utf8_bytes"])
        policies[policy] = {**dict(counts), "mean_known_requests": statistics.fmean(used) if used else None,
            "mean_known_requested_output_cap": statistics.fmean(used) * TOKEN_CAP if used else None,
            "mean_known_requests_saved_vs_32": statistics.fmean(saved) if saved else None,
            "mean_known_input_prompt_utf8_bytes": statistics.fmean(prompt_bytes) if prompt_bytes else None,
            "input_cost_measure": "UTF-8 prompt bytes, not billed tokenizer units",
            "known_top1_accuracy": counts["known_top1_correct"] / counts["known_eligible"] if counts["known_eligible"] else None,
            "known_top3_accuracy": counts["known_top3_correct"] / counts["known_eligible"] if counts["known_eligible"] else None,
            "accuracy_among_accepted": counts["known_correct_accepted"] / counts["known_accepted"] if counts["known_accepted"] else None}
    return {"policies": policies, "common_evaluation_horizon": 32,
        "incomplete_handling": "Only labels completing all eight stages are included in paired savings and accuracy; incomplete labels listed separately.",
        "policy_rule": "stable_shortlist: earliest six complete cells; same leader and passing fixed-horizon gates at two consecutive stages; otherwise use full budget",
        "metric_scope": "Historical same-collection holdout reanalysis; not a fresh blind or live test."}


def _summary(values: list[float]) -> dict:
    return {"count": len(values), "mean": statistics.fmean(values) if values else None,
            "median": statistics.median(values) if values else None,
            "min": min(values) if values else None, "max": max(values) if values else None}


def _matched_pair(left: dict, right: dict, minimum_cells: int = 8) -> dict | None:
    # Five-vs-five comparisons, plus a same-stratum reference with equal counts.
    cells = sorted(c for c in left.keys() & right.keys() if len(left[c]) >= 10 and len(right[c]) >= 10)
    if len(cells) < minimum_cells:
        return None
    between, within = [], []
    for c in cells:
        a, b = left[c], right[c]
        between.append(jsd(Counter(a[:5]), Counter(b[:5])))
        within.append((jsd(Counter(a[:5]), Counter(a[5:10])) + jsd(Counter(b[:5]), Counter(b[5:10]))) / 2)
    return {"shared_cells": len(cells), "between_jsd": statistics.fmean(between),
            "within_jsd": statistics.fmean(within),
            "excess_jsd": statistics.fmean(between) - statistics.fmean(within)}


def source_stratum_audit(data_zip: Path, bank: dict) -> dict:
    """Matched finite-sample distribution shifts, not source authentication.

    Cross-provider pairs hold collection date fixed; cross-date pairs hold the
    author provider label fixed.  A pair needs ten answers per shared cell so
    both the between and within baseline use disjoint groups of five.
    """
    if hashlib.md5(data_zip.read_bytes()).hexdigest() != DATA_MD5:
        raise ValueError("Published data archive checksum mismatch")
    ids = {m["model"] for m in bank["models"]}
    cells = {c["id"] for c in bank["cells"]}
    groups = defaultdict(lambda: defaultdict(list))
    providers, dates = defaultdict(set), defaultdict(set)
    with zipfile.ZipFile(data_zip) as archive:
        with archive.open("data/derived/normalized.jsonl") as stream:
            for line in stream:
                row = json.loads(line)
                if (row.get("run_id") != "main-02" or row.get("model") not in ids
                        or row.get("temperature") != 1 or row.get("answer_class") != "valid"):
                    continue
                cell_id = row["task_id"] + "|" + row["lang"]
                if cell_id not in cells:
                    continue
                provider, date = row.get("provider") or "unknown", row["request_utc"][:10]
                groups[(row["model"], provider, date)][cell_id].append((row["rep"], row["normalized"]))
                providers[row["model"]].add(provider)
                dates[row["model"]].add(date)
    for group in groups.values():
        for cell_id, rows in group.items():
            group[cell_id] = [answer for _, answer in sorted(rows)]
    result = {"enrolled_labels": len(ids), "labels_with_multiple_author_providers": sum(len(v) > 1 for v in providers.values()),
              "labels_with_multiple_dates": sum(len(v) > 1 for v in dates.values()),
              "minimum_samples_per_group_cell": 10, "comparison_sample_size_per_side": 5, "minimum_shared_cells": 8,
              "selection": "All eligible pairs; date held fixed across providers, provider held fixed across dates."}
    for mode in ("provider", "date"):
        records = []
        by_model = defaultdict(list)
        for model in sorted(ids):
            keys = sorted(k for k in groups if k[0] == model)
            for a, b in itertools.combinations(keys, 2):
                eligible = (a[2] == b[2] and a[1] != b[1]) if mode == "provider" else (a[1] == b[1] and a[2] != b[2])
                if not eligible:
                    continue
                match = _matched_pair(groups[a], groups[b])
                if match:
                    records.append({"model": model, "left_provider": a[1], "right_provider": b[1],
                                    "left_date": a[2], "right_date": b[2], **match})
                    by_model[model].append(match)
        # One model contributes one mean, regardless of its number of providers.
        result["cross_" + mode] = {"eligible_pairs": len(records), "eligible_labels": len(by_model),
            "model_balanced_between_jsd": _summary([statistics.fmean(r["between_jsd"] for r in rows) for rows in by_model.values()]),
            "model_balanced_within_jsd": _summary([statistics.fmean(r["within_jsd"] for r in rows) for rows in by_model.values()]),
            "model_balanced_excess_jsd": _summary([statistics.fmean(r["excess_jsd"] for r in rows) for rows in by_model.values()]),
            "pairs": records}
    result["interpretation"] = "Provider and date labels come from the author. Matched JSD exposes distribution variation; these are not endpoint-independent identity accuracy or causal provider effects. Missing strata remain ineligible."
    return result


def mixture_stress(bank: dict, test: dict) -> dict:
    """Deterministic 50:50 synthetic routing from disjoint held-out responses."""
    ids = sorted(m["model"] for m in bank["models"])
    counts, rows = Counter(), []
    for left_id, right_id in zip(ids[::2], ids[1::2]):
        left, right = test.get(left_id, {}), test.get(right_id, {})
        observed = {}
        for _ in range(8):
            cell = select_next_cell(bank, bank["models"], observed)
            if cell is None or len(left.get(cell["id"], [])) < 2 or len(right.get(cell["id"], [])) < 2:
                break
            observed[cell["id"]] = Counter(left[cell["id"]][:2] + right[cell["id"]][:2])
        if len(observed) < 8:
            counts["incomplete_pairs"] += 1
            continue
        stage = _stage(bank, bank["models"], observed)
        constituent = stage["first"] in {left_id, right_id}
        counts["eligible_pairs"] += 1
        counts["nearest_is_constituent"] += constituent
        counts["gate_passed"] += stage["fixed_horizon_gate_passed"] is True
        counts["gate_passed_nonconstituent"] += stage["fixed_horizon_gate_passed"] is True and not constituent
        rows.append({"left": left_id, "right": right_id, "nearest": stage["first"],
                     "nearest_is_constituent": constituent, "gate_passed": stage["fixed_horizon_gate_passed"],
                     "distance": stage["distance"], "gap": stage["gap"]})
    return {**dict(counts), "pairing": "Sorted enrolled labels, nonoverlapping adjacent pairs; no selection by outcomes.",
            "mixture": "Two held-out valid answers from each label per intact cell, 50:50; not a real gateway experiment.",
            "requests_per_pair": 32, "model_api_calls": 0, "pairs": rows,
            "interpretation": "A single nearest label or passing gate cannot establish a single upstream model, nor identify a routing mixture."}


def validate(data_zip: Path, code_zip: Path) -> dict:
    bank = load_identification_bank()
    rebuilt, splits = corpus(data_zip, code_zip)
    enrolled = {m["model"] for m in bank["models"]}
    # This helper also works in an installed wheel: exact IDs use the published
    # deterministic holdout split, rather than relying on a docs-file location.
    import random
    shuffled = [m["model"] for m in rebuilt["models"]]
    random.Random(20261024).shuffle(shuffled)
    dev_unknown, final_unknown = set(shuffled[:16]), set(shuffled[16:33])
    expected = {m["model"] for m in rebuilt["models"]} - dev_unknown - final_unknown
    if expected != enrolled:
        raise ValueError("Published corpus/holdout labels do not match deployed bank")
    gates = bank["calibration"]["open_set_gates"]
    output = {"schema": "api-mri-identification-stress/1", "computed_at": datetime.now(timezone.utc).isoformat(),
              "bank_sha256": hashlib.sha256(Path(__file__).with_name("data").joinpath("identification/one-token-202607.json").read_bytes()).hexdigest(),
              "model_api_calls": 0, "policy_evaluation_status": "exploratory_historical_holdout_reanalysis",
              "source": bank["source"]}
    for name, split_number, unknown_ids in (("development", 1, dev_unknown), ("historical_holdout", 2, final_unknown)):
        known = {m: simulate(bank, splits[split_number].get(m, {})) for m in sorted(enrolled)}
        unknown = {m: simulate(bank, splits[split_number].get(m, {})) for m in sorted(unknown_ids)}
        output[name] = evaluate_stopping(known, unknown, gates)
        print(f"Computed {name} stopping comparison.", flush=True)
    output["source_strata"] = source_stratum_audit(data_zip, bank)
    print("Computed provider/date matched distribution checks.", flush=True)
    output["mixture_stress"] = mixture_stress(bank, splits[2])
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-zip", type=Path, required=True)
    parser.add_argument("--code-zip", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("Choose a new output file; previous validation evidence is preserved")
    result = validate(args.data_zip, args.code_zip)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out), "historical_holdout": result["historical_holdout"],
        "mixtures": {k: v for k, v in result["mixture_stress"].items() if k != "pairs"}}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
