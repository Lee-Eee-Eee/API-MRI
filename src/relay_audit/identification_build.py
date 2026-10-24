"""Rebuild and evaluate the short-probe bank from pinned public ZIPs, offline.

This is a data transformation, not execution of the author's code.  Source
normalization is checked against our text port; post-reasoning rows stay excluded.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import random
import statistics
import zipfile

from .identification import normalize_answer, rank_candidates, select_next_cell, _common_cells, BLOCK

DATA_MD5 = "f2ce3fba3081f73e9908179fb2f061b6"
CODE_MD5 = "d81de3b8ef5c0bca74fd7c2bdbb41a6b"


def corpus(data_zip: Path, code_zip: Path):
    for path, digest in ((data_zip, DATA_MD5), (code_zip, CODE_MD5)):
        if hashlib.md5(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Published archive checksum mismatch: {path.name}")
    with zipfile.ZipFile(code_zip) as code:
        raw_prompts = code.read("config/prompts.json")
        prompts = json.loads(raw_prompts)
        catalog = json.loads(code.read("config/models.selected.json"))
    tasks = {t["id"]: t for t in prompts["tasks"] if t["paper"] == 1}
    cells = [{"id": task["id"] + "|" + lang, "task_id": task["id"], "lang": lang,
              "system": prompts["system_prompts"][lang], "user": task["prompts"][lang],
              "normalize_as": task["normalize_as"], "answer_space": task["answer_space"]}
             for task in tasks.values() for lang in prompts["languages"]]
    by_id = {c["id"]: c for c in cells}
    models = [{"model": m["id"], "label": m["name"], "family": m["family_guess"]}
              for m in catalog["models"] if m.get("included")]
    ids = {m["model"] for m in models}
    splits = [defaultdict(dict) for _ in range(3)]
    normalization_mismatches, selected, excluded = 0, 0, Counter()
    times, providers = [], defaultdict(set)
    with zipfile.ZipFile(data_zip) as archive:
        manifest = json.loads(archive.read("data/runs/main-02/manifest.json"))
        if manifest["prompts_sha256"] != hashlib.sha256(raw_prompts).hexdigest():
            raise ValueError("Prompt manifest mismatch")
        with archive.open("data/derived/normalized.jsonl") as stream:
            for line in stream:
                row = json.loads(line)
                if (row.get("run_id") != "main-02" or row.get("model") not in ids
                        or row.get("task_id") not in tasks or row.get("temperature") != 1):
                    continue
                cell_id = row["task_id"] + "|" + row["lang"]
                actual, status = normalize_answer(row["raw"], by_id[cell_id])
                if row["answer_class"] != "post_reasoning" and (actual, status) != (row["normalized"], row["answer_class"]):
                    normalization_mismatches += 1
                selected += 1
                times.append(row["request_utc"])
                providers[row["model"]].add(row["provider"] or "unknown")
                if row["answer_class"] != "valid":
                    excluded[row["answer_class"]] += 1
                    continue
                splits[row["rep"] % 3][row["model"]].setdefault(cell_id, []).append((row["rep"], row["normalized"]))
    if normalization_mismatches:
        raise ValueError(f"Normalization port differs for {normalization_mismatches} records")
    for split in splits:
        for cells_for_model in split.values():
            for cell_id, rows in cells_for_model.items():
                cells_for_model[cell_id] = [answer for _, answer in sorted(rows)]
    bank = {"schema": "api-mri-identification-bank/1", "id": "pamela-main02-repmod3-v1", "built_on": "2026-10-24",
            "collected_at": {"first": min(times), "last": max(times)},
            "source": {"paper": "https://arxiv.org/abs/2607.10252v1", "data": "https://doi.org/10.5281/zenodo.21278557",
                       "configuration": "https://doi.org/10.5281/zenodo.21278793", "author": "Tomáš Bruckner, 2026",
                       "data_license": "CC-BY-4.0", "configuration_license": "MIT", "data_md5": DATA_MD5,
                       "configuration_md5": CODE_MD5, "model_labels": "author-provided, not independently authenticated"},
            "reference_split": "main-02, temperature=1, valid only, rep % 3 == 0; providers pooled per model label",
            "normalization": "Python port checked against all non-post_reasoning selected rows; within-language labels",
            "cells": cells, "models": [{**m, "providers": sorted(providers[m["model"]]),
                "counts": {cell_id: dict(Counter(values)) for cell_id, values in splits[0][m["model"]].items()}}
                for m in sorted(models, key=lambda m: m["model"])],
            "build_audit": {"selected_records": selected, "excluded": dict(excluded),
                            "normalization_mismatches": normalization_mismatches, "model_api_calls": 0}}
    return bank, splits


def simulate(bank: dict, observations: dict, model_ids: list[str] | None = None, max_cells: int = 8, static_order: list[str] | None = None):
    models = bank["models"] if model_ids is None else [m for m in bank["models"] if m["model"] in set(model_ids)]
    observed, trajectory = {}, []
    for _ in range(max_cells):
        if static_order is None:
            cell = select_next_cell(bank, models, observed)
        else:
            cell = next((c for c in bank["cells"] if c["id"] == static_order[len(observed)]), None)
        if cell is None:
            break
        values = observations.get(cell["id"], [])[:BLOCK]
        if len(values) < BLOCK:
            break
        observed[cell["id"]] = Counter(values)
        ranking = rank_candidates(bank, models, observed)
        trajectory.append({"cells": len(observed), "requests": len(observed) * BLOCK,
                           "first": ranking[0]["model"], "top3": [r["model"] for r in ranking[:3]],
                           "distance": ranking[0]["distance"], "gap": ranking[1]["distance"] - ranking[0]["distance"],
                           "input_prompt_utf8_bytes": sum(len((c["system"] + c["user"]).encode("utf-8")) * BLOCK for c in bank["cells"] if c["id"] in observed),
                           "selected_cells": list(observed)})
    return trajectory


def evaluate(bank: dict, splits: list[dict]) -> dict:
    ids = [m["model"] for m in bank["models"]]
    shuffled = ids.copy()
    random.Random(20261024).shuffle(shuffled)
    # Held-out labels never contribute their responses to threshold estimation.
    withheld = set(shuffled[:33])
    unknown_calibration_ids = set(shuffled[:16])
    unknown_test_ids = set(shuffled[16:33])
    enrolled = [m for m in ids if m not in withheld]
    bank["build_audit"]["observed_model_labels"] = len(ids)
    bank["build_audit"]["enrolled_model_labels"] = len(enrolled)
    bank["build_audit"]["heldout_model_labels"] = len(withheld)
    bank["models"] = [m for m in bank["models"] if m["model"] in enrolled]
    calibration = {m: simulate(bank, splits[1][m]) for m in ids if m not in withheld}
    unknown_calibration = {m: simulate(bank, splits[1][m]) for m in sorted(unknown_calibration_ids)}
    cutoffs = {}
    gates = {}
    for size in range(3, 9):
        known = [(m, t[size - 1]) for m, t in calibration.items() if len(t) >= size]
        unknown = [t[size - 1] for t in unknown_calibration.values() if len(t) >= size]
        values = sorted(r["distance"] for _, r in known)
        if values:
            cutoffs[str(size)] = values[min(len(values) - 1, math_ceil(.95 * len(values)) - 1)]
            distances = sorted({0.0, *[values[min(len(values) - 1, int(q * (len(values) - 1)))] for q in (.1, .25, .5, .75, .9, .95, 1)]})
            gaps = sorted({0.0, 1.0, *[r["gap"] for _, r in known], *[r["gap"] + 1e-9 for r in unknown]})
            options = []
            for distance in distances:
                for gap in gaps:
                    accepted_unknown = sum(r["distance"] <= distance and r["gap"] >= gap for r in unknown)
                    if accepted_unknown:
                        continue
                    accepted_known = [(m, r) for m, r in known if r["distance"] <= distance and r["gap"] >= gap]
                    correct = sum(r["first"] == m for m, r in accepted_known)
                    options.append((correct, -len(accepted_known) + correct, -gap, distance))
            if options:
                correct, negative_errors, negative_gap, distance = max(options)
                gates[str(size)] = {"max_distance": distance, "min_gap": -negative_gap,
                    "known_calibration_count": len(known), "known_calibration_correct_accepted": correct,
                    "known_calibration_incorrect_accepted": -negative_errors,
                    "unknown_calibration_count": len(unknown), "unknown_calibration_accepted": 0}
    bank["calibration"] = {"method": "joint nearest-JSD and rank-gap gate; maximize correctly accepted known labels with zero accepted development unknown labels",
        "threshold_model_count": len(calibration), "heldout_model_count": len(withheld),
        "distance_cutoffs": cutoffs, "open_set_gates": gates,
        "unknown_development_model_count": len(unknown_calibration_ids), "unknown_test_model_count": len(unknown_test_ids),
        "unknown_test_labels_used_for_threshold": False,
        "test_split": "rep % 3 == 2; first four valid repetitions in each selected cell",
        "scope": "same 2026-07 collection, pooled providers; not a live accuracy guarantee",
        "early_stop": "no automatic positive identification; stop for budget, incompatible protocol, cache, reasoning"}
    known_test = {m: simulate(bank, splits[2][m]) for m in ids if m not in withheld}
    unknown_test = {m: simulate(bank, splits[2][m]) for m in sorted(unknown_test_ids)}
    static_order = [c["id"] for c in _common_cells(bank, bank["models"])]
    random.Random(20261024).shuffle(static_order)
    static_order = static_order[:8]
    fixed_test = {m: simulate(bank, splits[2][m], static_order=static_order) for m in enrolled}
    curves = []
    for size in (1, 2, 3, 4, 6, 8):
        known = [(m, t[size - 1]) for m, t in known_test.items() if len(t) >= size]
        unknown = [(m, t[size - 1]) for m, t in unknown_test.items() if len(t) >= size]
        cutoff = cutoffs.get(str(size))
        gate = gates.get(str(size))
        def passes(row):
            return gate is not None and row["distance"] <= gate["max_distance"] and row["gap"] >= gate["min_gap"]
        accepted_known = [(m, r) for m, r in known if passes(r)]
        fixed = [(m, t[size - 1]) for m, t in fixed_test.items() if len(t) >= size]
        curves.append({"cells": size, "requests": size * BLOCK, "requested_output_token_cap": size * BLOCK * 16,
            "known_test_labels": len(known), "known_top1_accuracy": statistics.fmean(r["first"] == m for m, r in known) if known else None,
            "known_top3_accuracy": statistics.fmean(m in r["top3"] for m, r in known) if known else None,
            "fixed_random_order_test_labels": len(fixed),
            "fixed_random_order_top1_accuracy": statistics.fmean(r["first"] == m for m, r in fixed) if fixed else None,
            "fixed_random_order_top3_accuracy": statistics.fmean(m in r["top3"] for m, r in fixed) if fixed else None,
            "adaptive_mean_input_prompt_utf8_bytes": statistics.fmean(r["input_prompt_utf8_bytes"] for _, r in known) if known else None,
            "fixed_mean_input_prompt_utf8_bytes": statistics.fmean(r["input_prompt_utf8_bytes"] for _, r in fixed) if fixed else None,
            "known_outside_distance_envelope": statistics.fmean(r["distance"] > cutoff for _, r in known) if known and cutoff is not None else None,
            "known_gate_coverage": len(accepted_known) / len(known) if known and gate is not None else None,
            "known_gate_correct_acceptance_rate": sum(r["first"] == m for m, r in accepted_known) / len(known) if known and gate is not None else None,
            "known_gate_accuracy_among_accepted": statistics.fmean(r["first"] == m for m, r in accepted_known) if accepted_known else None,
            "unknown_test_labels": len(unknown),
            "distance_only_unknown_rejection_rate": statistics.fmean(r["distance"] > cutoff for _, r in unknown) if unknown and cutoff is not None else None,
            "unknown_rejection_rate": statistics.fmean(not passes(r) for _, r in unknown) if unknown and gate is not None else None,
            "unknown_false_acceptance_rate": statistics.fmean(passes(r) for _, r in unknown) if unknown and gate is not None else None})
    return {"schema": "api-mri-identification-evaluation/1", "computed_on": "2026-10-24",
        "model_api_calls": 0, "splits": {"reference": "rep%3=0", "threshold_calibration": "rep%3=1, 132 label subset",
            "test": "rep%3=2", "unknown_holdout_seed": 20261024, "heldout_model_labels": sorted(withheld),
            "unknown_development_labels": sorted(unknown_calibration_ids), "unknown_final_test_labels": sorted(unknown_test_ids),
            "independence": "different repetitions in same collection; not temporal/provider independence"},
        "method": "adaptive intact cell selection, pairwise reference JSD per UTF8 cost; mean observed JSD ranking",
        "selection_fitted_on": "reference repetitions only; every answer reused for every candidate",
        "fixed_random_order_baseline": {"seed": 20261024, "cells": static_order, "kind": "one prespecified order, not optimized or random-order expectation"},
        "bank_audit": bank["build_audit"], "calibration": bank["calibration"], "cost_curve": curves,
        "known_test_trajectories": known_test, "unknown_test_trajectories": unknown_test,
        "interpretation": "33 whole model labels are absent from deployed references and query selection. 16 are development unknowns used only to calibrate the rejection gate; 17 are untouched unknown test labels. Known responses split into reference/calibration/test repetitions. All originate from one collection and pooled serving providers."}


def math_ceil(value: float) -> int:
    return int(value) if value == int(value) else int(value) + 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-zip", type=Path, required=True)
    parser.add_argument("--code-zip", type=Path, required=True)
    parser.add_argument("--bank", type=Path, required=True)
    parser.add_argument("--evaluation", type=Path, required=True)
    args = parser.parse_args()
    if args.bank.exists() or args.evaluation.exists():
        parser.error("Choose new bank and evaluation outputs; existing evidence is preserved")
    bank, splits = corpus(args.data_zip, args.code_zip)
    print(f"Built {len(bank['models'])} labels; evaluating independent repeats and unknown label holdout.", flush=True)
    result = evaluate(bank, splits)
    args.bank.parent.mkdir(parents=True, exist_ok=True)
    args.evaluation.parent.mkdir(parents=True, exist_ok=True)
    args.bank.write_text(json.dumps(bank, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    args.evaluation.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["cost_curve"], ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
