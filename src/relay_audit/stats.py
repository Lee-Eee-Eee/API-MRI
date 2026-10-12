"""Conservative, per-prompt adapter around the published MET implementation.

This module does not implement a kernel or permutation test.  It uses MET with
one prompt at a time because MET's own permutation routine is not stratified.
Non-rejection is deliberately not an identity or equivalence certification.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from importlib.metadata import PackageNotFoundError, version
import hashlib
import math
from typing import Any, Sequence

from .records import AuditRecord


def _positive_integer(name: str, value: int, *, minimum: int = 1) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}.")


def _holm_adjust(pvalues: dict[str, float]) -> dict[str, float]:
    """Holm correction over the whole observed prompt family, including gaps."""
    ordered = sorted(pvalues.items(), key=lambda item: (item[1], item[0]))
    adjusted: dict[str, float] = {}
    running = 0.0
    for rank, (prompt_id, pvalue) in enumerate(ordered):
        running = max(running, (len(ordered) - rank) * pvalue)
        adjusted[prompt_id] = min(1.0, running)
    return adjusted


def _prompt_seed(seed: int, prompt_id: str) -> int:
    digest = hashlib.sha256(f"{seed}\0{prompt_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % (2**63)


def _dependent_metadata(record: AuditRecord) -> bool:
    retries = record.metadata.get("inspect_retries", [])
    if retries is None:
        retries = []
    return (
        record.metadata.get("client_cache_hit") is True
        or record.metadata.get("independent_sample") is False
        or record.metadata.get("retry_of") is not None
        or not isinstance(retries, list)
        or any(type(value) is not int or value != 0 for value in retries)
    )


def _validated_records(
    records: Sequence[AuditRecord], role: str
) -> tuple[list[AuditRecord], list[str]]:
    validated: list[AuditRecord] = []
    errors: list[str] = []
    for index, record in enumerate(records):
        if not isinstance(record, AuditRecord):
            errors.append(f"{role}:invalid_record_at_index:{index}")
            continue
        try:
            # Also check instances constructed without Pydantic validation.
            validated.append(AuditRecord.model_validate(record.model_dump()))
        except (TypeError, ValueError):
            errors.append(f"{role}:invalid_record_at_index:{index}")
    return validated, errors


def _met_test(
    reference: list[str], candidate: list[str], *, permutations: int, seed: int
) -> tuple[float, float, int, str]:
    """Actually call MET; isolate CPU Torch and NumPy's legacy RNG state."""
    import numpy as np
    import torch
    from model_equality_testing.algorithm import run_two_sample_test
    from model_equality_testing.distribution import CompletionSample
    from model_equality_testing.utils import pad_to_length, tokenize_unicode

    left = np.asarray(tokenize_unicode(reference), dtype=np.int64)
    right = np.asarray(tokenize_unicode(candidate), dtype=np.int64)
    length = max(left.shape[1], right.shape[1])
    if length == 0:
        raise ValueError("MET cannot normalize the Hamming kernel at length zero.")
    left = pad_to_length(left, length, pad_token_id=-1)
    right = pad_to_length(right, length, pad_token_id=-1)
    left_sample = CompletionSample(np.zeros(len(reference), dtype=np.int64), left, m=1)
    right_sample = CompletionSample(np.zeros(len(candidate), dtype=np.int64), right, m=1)
    numpy_state = np.random.get_state()
    try:
        with torch.random.fork_rng(devices=[]):
            # Use the CPU generator; do not change an unrelated CUDA generator.
            torch.random.default_generator.manual_seed(seed)
            np.random.seed(seed % (2**32))
            raw_p, statistic = run_two_sample_test(
                left_sample,
                right_sample,
                pvalue_type="permutation_pvalue",
                stat_type="mmd_hamming",
                b=permutations,
            )
    finally:
        np.random.set_state(numpy_state)
    return float(raw_p), float(statistic), length, version("model-equality-testing")


def compare_records(
    reference: list[AuditRecord],
    candidate: list[AuditRecord],
    *,
    permutations: int = 499,
    seed: int = 11,
    alpha: float = 0.05,
    min_samples: int = 8,
    max_completion_chars: int = 4096,
) -> dict[str, Any]:
    """Compare matched evidence; return only JSON-serializable, bounded claims.

    ``min_samples`` is an engineering threshold, not a power guarantee.  The
    family includes recorded planned and observed prompt IDs; if plan metadata
    is absent, a prompt missing on both sides cannot be reconstructed. Missing/error
    observations block the overall behavioral conclusion rather than silently
    conditioning on whichever requests succeeded.
    """
    _positive_integer("permutations", permutations)
    _positive_integer("min_samples", min_samples, minimum=2)
    _positive_integer("max_completion_chars", max_completion_chars)
    _positive_integer("seed", seed, minimum=0)
    if seed >= 2**63:
        raise ValueError("seed must be < 2**63.")
    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not 0 < alpha < 1:
        raise ValueError("alpha must be finite and strictly between 0 and 1.")

    left, left_errors = _validated_records(reference, "reference")
    right, right_errors = _validated_records(candidate, "candidate")
    global_reasons = left_errors + right_errors
    for role, records in (("reference", left), ("candidate", right)):
        if not records:
            global_reasons.append(f"{role}:no_valid_records")
        for field in ("endpoint_id", "run_id", "model_declared", "config_hash", "reference_kind"):
            if len({getattr(record, field) for record in records}) > 1:
                global_reasons.append(f"{role}:mixed_{field}")

    if left and any(record.reference_kind not in {"official", "synthetic_control"} for record in left):
        global_reasons.append("reference:unverified_reference_kind")
    for field in ("model_declared", "config_hash"):
        if left and right and {getattr(record, field) for record in left} != {
            getattr(record, field) for record in right
        }:
            global_reasons.append(f"between_roles:{field}_mismatch")

    all_records = left + right
    left_observations = {(row.run_id, row.prompt_id, row.replicate) for row in left}
    right_observations = {(row.run_id, row.prompt_id, row.replicate) for row in right}
    if left_observations & right_observations:
        global_reasons.append("between_roles:overlapping_observation_ids")
    if {row.source_log for row in left if row.source_log} & {row.source_log for row in right if row.source_log}:
        global_reasons.append("between_roles:shared_source_log")
    synthetic_flags = {record.synthetic for record in all_records}
    synthetic = next(iter(synthetic_flags)) if len(synthetic_flags) == 1 else None
    if len(synthetic_flags) > 1:
        global_reasons.append("synthetic_and_real_records_mixed")
    for record in all_records:
        if record.synthetic != (record.reference_kind == "synthetic_control"):
            global_reasons.append("synthetic_label_and_reference_kind_inconsistent")
            break

    grouped: dict[str, dict[str, list[AuditRecord]]] = defaultdict(
        lambda: {"reference": [], "candidate": []}
    )
    for role, records in (("reference", left), ("candidate", right)):
        for record in records:
            grouped[record.prompt_id][role].append(record)
    planned: dict[str, tuple[set[str], int] | None] = {}
    for role, rows in (("reference", left), ("candidate", right)):
        manifests = []
        for row in rows:
            prompt_ids = row.metadata.get("expected_prompt_ids")
            repeats = row.metadata.get("expected_repeats")
            if prompt_ids is None and repeats is None:
                continue
            if (
                not isinstance(prompt_ids, list) or not prompt_ids
                or any(not isinstance(value, str) or not value for value in prompt_ids)
                or len(prompt_ids) != len(set(prompt_ids))
                or isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < 1
            ):
                global_reasons.append(f"{role}:invalid_sampling_plan_metadata")
                continue
            manifests.append((frozenset(prompt_ids), repeats))
        if manifests and (len(set(manifests)) != 1 or len(manifests) != len(rows)):
            global_reasons.append(f"{role}:inconsistent_sampling_plan_metadata")
        planned[role] = (set(manifests[0][0]), manifests[0][1]) if manifests else None
    for plan in planned.values():
        if plan:
            for prompt_id in plan[0]:
                grouped[prompt_id]
    family = sorted(grouped)
    prompt_results: list[dict[str, Any]] = []
    package_version: str | None = None
    try:
        package_version = version("model-equality-testing")
    except PackageNotFoundError:
        pass

    for prompt_id in family:
        sides = grouped[prompt_id]
        reasons: list[str] = []
        counts: dict[str, dict[str, int]] = {}
        replicate_sets: dict[str, set[int]] = {}
        for role in ("reference", "candidate"):
            rows = sides[role]
            counts[role] = {
                "records": len(rows),
                "successes": sum(record.status == "success" for record in rows),
                "errors": sum(record.status == "error" for record in rows),
            }
            replicate_counts = Counter(record.replicate for record in rows)
            replicate_sets[role] = set(replicate_counts)
            if any(count > 1 for count in replicate_counts.values()):
                reasons.append(f"{role}:duplicate_replicate")
            if not rows:
                reasons.append(f"{role}:missing_prompt")
            if counts[role]["errors"]:
                reasons.append(f"{role}:failed_requests")
            if counts[role]["successes"] < min_samples:
                reasons.append(f"{role}:below_engineering_sample_threshold")
            if any(_dependent_metadata(record) for record in rows):
                reasons.append(f"{role}:dependent_or_retried_observations")
            if planned[role] is not None:
                expected_ids, expected_repeats = planned[role]
                if prompt_id not in expected_ids:
                    reasons.append(f"{role}:unexpected_prompt_outside_plan")
                if replicate_sets[role] != set(range(expected_repeats)):
                    reasons.append(f"{role}:planned_replicate_coverage_mismatch")
        if replicate_sets["reference"] != replicate_sets["candidate"]:
            reasons.append("between_roles:replicate_coverage_mismatch")
        hashes = {record.prompt_hash for record in sides["reference"] + sides["candidate"]}
        if len(hashes) != 1:
            reasons.append("prompt_hash_mismatch")
        texts = {
            role: [record.response for record in sides[role] if record.status == "success"]
            for role in ("reference", "candidate")
        }
        if any(len(text) > max_completion_chars for rows in texts.values() for text in rows):
            reasons.append("completion_exceeds_character_limit_no_truncation_applied")
        if texts["reference"] and texts["candidate"] and not any(
            texts["reference"] + texts["candidate"]
        ):
            reasons.append("all_successful_responses_empty")

        result: dict[str, Any] = {
            "prompt_id": prompt_id,
            "status": "insufficient_evidence",
            "reasons": reasons,
            "counts": counts,
            "missing_replicates": {
                "reference": sorted(replicate_sets["candidate"] - replicate_sets["reference"]),
                "candidate": sorted(replicate_sets["reference"] - replicate_sets["candidate"]),
            },
            "unique_responses": {role: len(set(rows)) for role, rows in texts.items()},
            "tested": False,
            "raw_p": None,
            "corrected_p": None,
            "adjusted_p": None,
            "family_p": 1.0,
            "statistic": None,
            "comparison_length": None,
            "seed": _prompt_seed(seed, prompt_id),
            "statistical_non_rejection": None,
        }
        if not global_reasons and not reasons:
            try:
                raw_p, statistic, length, package_version = _met_test(
                    texts["reference"],
                    texts["candidate"],
                    permutations=permutations,
                    seed=result["seed"],
                )
                exceedances = round(permutations * raw_p) if math.isfinite(raw_p) else -1
                if (
                    not math.isfinite(statistic)
                    or not 0 <= raw_p <= 1
                    or not math.isclose(permutations * raw_p, exceedances, abs_tol=max(1e-7, permutations * 1.2e-7))
                ):
                    result["reasons"].append("met_invalid_or_nonfinite_result")
                else:
                    corrected_p = (exceedances + 1) / (permutations + 1)
                    result.update(
                        tested=True,
                        raw_p=raw_p,
                        corrected_p=corrected_p,
                        family_p=corrected_p,
                        statistic=statistic,
                        comparison_length=length,
                        permutation_exceedances=exceedances,
                    )
            except ImportError:
                result["reasons"].append("met_dependency_unavailable")
            except Exception as exc:
                # Do not copy response content or credentials from an exception.
                result["reasons"].append(f"met_execution_failed:{type(exc).__name__}")
        elif global_reasons:
            result["reasons"].append("blocked_by_global_evidence_mismatch")
        prompt_results.append(result)

    adjusted = _holm_adjust({result["prompt_id"]: result["family_p"] for result in prompt_results})
    for result in prompt_results:
        if result["tested"]:
            result["adjusted_p"] = adjusted[result["prompt_id"]]
            rejected = result["adjusted_p"] <= alpha
            result["statistical_non_rejection"] = not rejected
            if rejected:
                result["status"] = "behavior_difference"
                result["reasons"].append("holm_corrected_distribution_test_rejected")
            else:
                result["reasons"].extend(("statistical_non_rejection", "power_not_calibrated"))

    tested = sum(result["tested"] for result in prompt_results)
    rejected = sum(result["status"] == "behavior_difference" for result in prompt_results)
    complete = bool(family) and tested == len(family) and not global_reasons
    reasons = list(dict.fromkeys(global_reasons))
    if not complete:
        reasons.append("incomplete_or_invalid_observed_prompt_family")
    if not rejected:
        reasons.extend(("no_holm_corrected_behavior_difference_detected", "power_not_calibrated"))
    if rejected and not complete:
        reasons.append("individual_signals_do_not_resolve_family_coverage_or_matching_failures")

    return {
        "status": "behavior_difference" if complete and rejected else "insufficient_evidence",
        "reasons": list(dict.fromkeys(reasons)),
        "synthetic": synthetic,
        "evidence_scope": (
            "synthetic_control" if synthetic is True else "real_endpoint" if synthetic is False else "invalid_or_empty"
        ),
        "method": {
            "engine": "model-equality-testing",
            "engine_version": package_version,
            "statistic": "mmd_hamming",
            "sampling_unit": "one_prompt_with_independent_repeated_completions",
            "permutation": "MET permutation_pvalue with m=1 per prompt",
            "encoding": "Unicode code points, common length, padding=-1; no truncation",
            "monte_carlo_correction": "(exceedances+1)/(permutations+1); raw MET p retained",
            "multiple_testing": "Holm over recorded planned and observed prompt IDs; untested prompts assigned family_p=1",
            "identity_certification": False,
            "power_calibrated": False,
            "limitations": [
                "Rejection supports a behavior distribution difference only for this reference, task, configuration and sampling period.",
                "Hamming MMD can miss distribution differences and is not a semantic metric.",
                "Non-rejection is not equivalence, model identity, absence of risk, or a calibrated power guarantee.",
                "No conclusion about fraud, data retention, training, distillation or server-side model weights.",
                "Independence, provider-side caching and completeness of the planned task set are not established by these records.",
                "Synthetic controls validate program behavior only; they are not measurements of real models or calibration of detection power.",
            ],
        },
        "prompt_results": prompt_results,
        "coverage": {
            "basis": "recorded_plan_metadata_and_observed_prompt_ids" if all(planned.values()) else "observed_prompt_ids_with_partial_or_missing_plan",
            "plan_metadata_present": {role: plan is not None for role, plan in planned.items()},
            "family_size": len(family),
            "tested_prompts": tested,
            "untested_prompts": len(family) - tested,
            "rejected_prompts": rejected,
            "complete_observed_family": complete,
            "input_records": {"reference": len(reference), "candidate": len(candidate)},
            "valid_records": {"reference": len(left), "candidate": len(right)},
            "invalid_records": {"reference": len(left_errors), "candidate": len(right_errors)},
            "failed_records": {
                "reference": sum(record.status == "error" for record in left),
                "candidate": sum(record.status == "error" for record in right),
            },
        },
        "settings": {
            "permutations": permutations,
            "seed": seed,
            "alpha": float(alpha),
            "min_samples": min_samples,
            "min_samples_is_engineering_threshold_only": True,
            "max_completion_chars": max_completion_chars,
            "monte_carlo_resolution": 1 / (permutations + 1),
        },
    }
