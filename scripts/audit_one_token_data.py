"""Offline audit of Bruckner's published One Token Is Enough artifacts.

Reads ZIP members as data; no extraction, network access or upstream code execution.
Data: https://doi.org/10.5281/zenodo.21278557 (CC-BY-4.0).
Configuration: https://doi.org/10.5281/zenodo.21278793 (MIT metadata).
We reuse the author's normalization labels and independently recompute JSD/ROC.
This checks one published corpus, not live model identity or cross-day reliability.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import zipfile


DATA_MD5 = "f2ce3fba3081f73e9908179fb2f061b6"
CODE_MD5 = "d81de3b8ef5c0bca74fd7c2bdbb41a6b"


def check_archive(path: Path, expected_md5: str) -> dict:
    raw = path.read_bytes()
    actual_md5 = hashlib.md5(raw).hexdigest()
    if actual_md5 != expected_md5:
        raise ValueError(f"Archive hash mismatch: {path.name}")
    with zipfile.ZipFile(path) as archive:
        if sum(x.file_size for x in archive.infolist()) > 1_000_000_000:
            raise ValueError("Uncompressed archive exceeds 1 GB inspection bound")
    return {"filename": path.name, "bytes": len(raw), "md5": actual_md5,
            "sha256": hashlib.sha256(raw).hexdigest()}


def jsonl(archive: zipfile.ZipFile, name: str):
    with archive.open(name) as stream:
        for line_number, line in enumerate(stream, 1):
            if len(line) > 1_000_000:
                raise ValueError(f"Oversize JSONL line {name}:{line_number}")
            if line.strip():
                yield json.loads(line)


def row_signature(row: dict) -> bytes:
    fields = [row.get(k) for k in
              ("run_id", "model", "provider", "task_id", "lang", "temperature", "rep", "request_utc", "raw")]
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode()).digest()


def jsd(left: Counter, right: Counter) -> float:
    nl, nr = sum(left.values()), sum(right.values())
    result = 0.0
    for answer in left.keys() | right.keys():
        p, q = left[answer] / nl, right[answer] / nr
        m = (p + q) / 2
        if p:
            result += p * math.log2(p / m) / 2
        if q:
            result += q * math.log2(q / m) / 2
    return result


def operating_summary(scores: list[tuple[float, bool]]) -> dict:
    """Low score supports same-label pairing. Ties receive half credit for AUC."""
    n_same = sum(same for _, same in scores)
    n_other = len(scores) - n_same
    if not n_same or not n_other:
        raise ValueError("ROC requires both classes")
    groups = defaultdict(lambda: [0, 0])
    for score, same in scores:
        groups[score][0 if same else 1] += 1
    accepted_same = accepted_other = 0
    rank_credit = 0.0
    best_gap, best_eer, best_threshold = 1.0, 0.5, None
    for score, (same, other) in sorted(groups.items()):
        rank_credit += other * (accepted_same + same / 2)
        accepted_same += same
        accepted_other += other
        false_accept = accepted_other / n_other
        false_reject = 1 - accepted_same / n_same
        gap = abs(false_accept - false_reject)
        if gap < best_gap:
            best_gap = gap
            best_eer = (false_accept + false_reject) / 2
            best_threshold = score
    return {"auc": rank_credit / (n_same * n_other), "eer": best_eer,
            "descriptive_eer_threshold": best_threshold,
            "same_label_pairs": n_same, "different_label_pairs": n_other}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-zip", type=Path, required=True)
    parser.add_argument("--code-zip", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--resamples", type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.resamples <= 200:
        raise ValueError("resamples must be between 1 and 200")
    assert jsd(Counter(a=1), Counter(a=1)) == 0
    assert jsd(Counter(a=1), Counter(b=1)) == 1
    assert operating_summary([(0.0, True), (1.0, False)])["auc"] == 1
    assert operating_summary([(0.0, True), (1.0, False)])["eer"] == 0
    assert operating_summary([(0.0, True), (0.0, False)])["auc"] == 0.5

    data_metadata = check_archive(args.data_zip, DATA_MD5)
    code_metadata = check_archive(args.code_zip, CODE_MD5)
    with zipfile.ZipFile(args.code_zip) as code:
        catalog = json.loads(code.read("config/models.selected.json"))
        prompt_bytes = code.read("config/prompts.json")
        prompts = json.loads(prompt_bytes)
    included = {m["id"] for m in catalog["models"] if m.get("included")}
    task_ids = {t["id"] for t in prompts["tasks"] if t["paper"] == 1}
    print("Verified both archive hashes; inspecting published records.", flush=True)

    with zipfile.ZipFile(args.data_zip) as data:
        manifest = json.loads(data.read("data/runs/main-02/manifest.json"))
        if manifest["prompts_sha256"] != hashlib.sha256(prompt_bytes).hexdigest():
            raise ValueError("Released prompts do not match main-02 manifest")
        author_result = json.loads(data.read("results/verification.json"))
        raw_signatures = {}
        raw_models, raw_providers = Counter(), Counter()
        usage, included_usage = Counter(), Counter()
        included_raw_count = 0
        included_providers = set()
        raw_count, duplicate_raw = 0, 0
        first_utc, last_utc = None, None
        for row in jsonl(data, "data/runs/main-02/responses.jsonl"):
            raw_count += 1
            key = row["key"]
            duplicate_raw += key in raw_signatures
            raw_signatures[key] = row_signature(row)
            raw_models[row["model"]] += 1
            raw_providers[row.get("provider") or "unknown"] += 1
            if row["model"] in included:
                included_raw_count += 1
                included_providers.add(row.get("provider") or "unknown")
            when = row.get("request_utc")
            if when:
                first_utc = min(first_utc, when) if first_utc else when
                last_utc = max(last_utc, when) if last_utc else when
            for key, value in (row.get("usage") or {}).items():
                if isinstance(value, (float, int)) and not isinstance(value, bool):
                    usage[key] += value
                    if row["model"] in included:
                        included_usage[key] += value

        run_counts, class_counts, all_classes = Counter(), Counter(), Counter()
        selected_models, selected_cells = set(), set()
        halves = defaultdict(Counter)
        normalized_count = selected_count = valid_count = unmatched_rows = 0
        normalized_keys = set()
        for row in jsonl(data, "data/derived/normalized.jsonl"):
            normalized_count += 1
            run_counts[row.get("run_id")] += 1
            all_classes[row.get("answer_class")] += 1
            if row["key"] in normalized_keys:
                raise ValueError("Duplicate key in normalized records")
            normalized_keys.add(row["key"])
            if raw_signatures.get(row["key"]) != row_signature(row):
                unmatched_rows += 1
            if (row.get("run_id") != "main-02" or row["model"] not in included
                    or row["task_id"] not in task_ids or row["temperature"] != 1):
                continue
            selected_count += 1
            class_counts[row["answer_class"]] += 1
            selected_models.add(row["model"])
            cell = (row["task_id"], row["lang"])
            selected_cells.add(cell)
            if row["answer_class"] == "valid":
                valid_count += 1
                halves[(row["model"], cell, row["rep"] % 2)][row["normalized"]] += 1

    if unmatched_rows:
        raise ValueError(f"{unmatched_rows} normalized records differ from main-02 raw input")
    if duplicate_raw:
        raise ValueError("Duplicate raw keys require an explicit resolution rule")
    models, cells = sorted(selected_models), sorted(selected_cells)
    pairs = [(a, b) for a in models for b in models]
    cell_values = []
    for cell in cells:
        values = []
        for a, b in pairs:
            left, right = halves[(a, cell, 0)], halves[(b, cell, 1)]
            # Match the published per-half minimum and four-decimal score storage.
            values.append(round(jsd(left, right), 4)
                          if sum(left.values()) >= 5 and sum(right.values()) >= 5 else None)
        cell_values.append(values)

    def aggregate(indices: list[int]) -> dict:
        scores = []
        for pair_index, (a, b) in enumerate(pairs):
            observed = [cell_values[i][pair_index] for i in indices
                        if cell_values[i][pair_index] is not None]
            if observed:
                scores.append((statistics.fmean(observed), a == b))
        return operating_summary(scores)

    full = aggregate(list(range(len(cells))))
    rng = random.Random(20261015)
    budget = []
    for k in (4, 8, 16):
        eers = [aggregate(rng.sample(range(len(cells)), k))["eer"]
                for _ in range(args.resamples)]
        budget.append({"cells": k, "subset_resamples": len(eers),
                       "eer_mean": statistics.fmean(eers),
                       "eer_min": min(eers), "eer_max": max(eers)})
        print(f"Computed {k}-cell exploratory curve point.", flush=True)
    half_sizes = [sum(c.values()) for c in halves.values() if c]
    result = {
        "schema_version": "1.0", "computed_on": "2026-10-15",
        "method": "Independent Python JSD and ROC calculations from author's normalized main-02 records",
        "source": {"paper": "https://arxiv.org/abs/2607.10252v1",
                   "data": "https://doi.org/10.5281/zenodo.21278557",
                   "data_license": "CC-BY-4.0", "data_attribution": "Tomáš Bruckner, 2026",
                   "code_configuration": "https://doi.org/10.5281/zenodo.21278793",
                   "configuration_license": "MIT (Zenodo metadata)",
                   "data_archive": data_metadata, "code_archive": code_metadata},
        "execution": {"network_calls": 0, "model_api_calls": 0,
                      "upstream_code_executed": False, "archive_extracted": False},
        "corpus": {"catalog_models": len(catalog["models"]),
                   "included_catalog_models": len(included), "raw_main02_records": raw_count,
                   "raw_model_identifiers": len(raw_models), "raw_provider_labels": len(raw_providers),
                   "raw_first_utc": first_utc, "raw_last_utc": last_utc,
                   "raw_duplicate_keys": duplicate_raw, "reported_usage_main02": dict(usage),
                   "included_models_main02_records_all_tasks_temperatures": included_raw_count,
                   "included_model_provider_labels": len(included_providers),
                   "reported_usage_included_models_all_tasks_temperatures": dict(included_usage),
                   "normalized_records": normalized_count, "normalized_runs": dict(run_counts),
                   "normalized_classes_all_tasks": dict(all_classes),
                   "normalized_raw_signature_mismatches": unmatched_rows},
        "analysis": {"run_id": "main-02", "temperature": 1,
                     "study1_tasks": sorted(task_ids), "cells": len(cells),
                     "model_identifiers": len(models), "records_before_validity_filter": selected_count,
                     "valid_records": valid_count, "answer_classes": dict(class_counts),
                     "half_split": "rep parity, pooled across providers within a model identifier",
                     "minimum_valid_answers_per_half": 5,
                     "valid_half_sample_sizes": {"min": min(half_sizes),
                                                "median": statistics.median(half_sizes),
                                                "max": max(half_sizes)},
                     "eligible_half_sample_sizes": {"min": min(n for n in half_sizes if n >= 5),
                                                    "median": statistics.median(n for n in half_sizes if n >= 5),
                                                    "max": max(half_sizes)},
                     "normalization": "Author-supplied normalized/answer_class fields reused; text provenance checked against raw records"},
        "recomputed_full_battery": full,
        "author_archived_full_battery": author_result["full_battery"],
        "absolute_difference": {"auc": abs(full["auc"] - author_result["full_battery"]["auc"]),
                                "eer": abs(full["eer"] - author_result["full_battery"]["eer"])},
        "exploratory_budget_curve": budget,
        "exploratory_seed": 20261015,
        "limitations": [
            "Offline reuse of one published corpus; model/provider identity labels are not independently authenticated.",
            "Parity halves share a collection period and may share provider mixtures; this is not cross-day or origin-verified evaluation.",
            "Author normalization is reused; multilingual normalization was not independently reimplemented.",
            "EER threshold is chosen on the same scores for description, not calibrated as a deployable decision threshold.",
            "Exploratory budget subsets use Python RNG and fewer resamples than the paper; ranges are subset variation, not confidence intervals.",
            "Reported usage is server metadata, not independently verified billing. Per-half repetitions vary, so cells do not equal request counts."
        ]
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out), "models": len(models),
                      "full": full, "differences": result["absolute_difference"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
