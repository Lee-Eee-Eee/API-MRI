"""Offline inventory of reference evidence and planned sampling cost.

This describes retained data; it does not estimate model-identification accuracy.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from .fingerprint import list_reference_packs, plan_fingerprint
from .reference_library import load_reference


def _usable_vector(run: object, size: int) -> bool:
    if not isinstance(run, dict) or type(run.get("is_self")) is not bool:
        return False
    values = run.get("values")
    return (isinstance(values, list) and len(values) == size
            and any(v is not None for v in values)
            and all(v is None or (type(v) in (int, float) and math.isfinite(v)) for v in values))


def audit_references(*, library_dir: Path | None = None) -> dict:
    rows = []
    for meta in list_reference_packs(library_dir):
        _, data = load_reference(meta["id"], library_dir)
        retained = list(data.get("target_results", {}).values())
        usable = [run for run in retained if _usable_vector(run, len(data["probes"]))]
        self_runs = [run for run in usable if run["is_self"] is True]
        other_runs = [run for run in usable if run["is_self"] is False]
        plans = {}
        for profile in ("screening", "full"):
            plan = plan_fingerprint({
                "reference_pack": meta["id"], "profile": profile,
                "candidate": {"api_base": "https://example.invalid/v1", "model": meta["model"]},
            }, library_dir=library_dir)
            plans[profile] = {
                "probe_count": plan["probe_count"], "batch_count": plan["batch_count"],
                "max_requests": plan["max_requests"],
                "max_output_tokens_total": plan["max_output_tokens_total"],
            }
        endpoint = meta.get("source_endpoint")
        rows.append({
            "id": meta["id"], "model": meta["model"], "label": meta["label"],
            "sha256": meta["sha256"], "source_url": meta["source_url"],
            "source_endpoint": endpoint if endpoint and endpoint != "unknown" else None,
            "source_independently_verified": meta.get("source_verified") is True,
            "probe_count": len(data["probes"]),
            "retained_self_runs": len(self_runs), "retained_other_model_runs": len(other_runs),
            "unusable_result_entries": len(retained) - len(usable),
            "self_mismatches": meta["self_hamming"], "self_compared": meta["self_total"],
            "plans": plans,
        })
    return {
        "schema": "api-mri-reference-readiness/1", "external_model_calls": 0,
        "scope": "retained_reference_data_and_request_plans",
        "summary": {
            "pack_count": len(rows),
            "packs_with_recorded_endpoint": sum(r["source_endpoint"] is not None for r in rows),
            "packs_with_independently_verified_source": sum(r["source_independently_verified"] for r in rows),
            "packs_with_multiple_self_runs": sum(r["retained_self_runs"] > 1 for r in rows),
            "packs_with_other_model_runs": sum(r["retained_other_model_runs"] > 0 for r in rows),
            "unusable_result_entries": sum(r["unusable_result_entries"] for r in rows),
        },
        "empirical_accuracy": None,
        "notes": [
            "统计对象是参考文件保留的记录，未记录不等于作者从未开展相关实验。",
            "计入的测试记录须保留与题数等长、至少有一个数值的合法回答向量；这项格式检查不认证其来源或实验条件。",
            "探针生成时的多温度筛选不计为独立审计重复；参考自测偏离不是实际误报率。",
            "请求数与输出配额是计划上限，不是实际消耗或账单；输入与其他计费项另计。",
            "短初筛尚未校准，不计算模型识别准确率；完整比较也需独立的真实模型校准。",
        ],
        "references": rows,
    }


def write_reference_audit(out: Path, *, library_dir: Path | None = None) -> dict:
    result = audit_references(library_dir=library_dir)
    summary = result["summary"]
    lines = [
        "# 参考证据与调用计划盘点", "",
        f"本机共有 {summary['pack_count']} 份可用参考，其中 {summary['packs_with_recorded_endpoint']} 份记录完整采集端点。",
        f"具有独立来源认证标记的参考为 {summary['packs_with_independently_verified_source']} 份；保留多次自测向量的参考为 {summary['packs_with_multiple_self_runs']} 份；保留其他模型测试向量的参考为 {summary['packs_with_other_model_runs']} 份。", "",
        "这份盘点在本机读取文件和计算计划，没有调用模型。它说明现有数据能支持哪些分析；真实误报率、检出率与鉴别准确率仍需独立采样。", "",
        "| 参考模型 | 题数 | 留存自测 / 其他模型记录 | 初筛请求 / 输出配额上限 | 完整请求 / 输出配额上限 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for row in result["references"]:
        short, full = row["plans"]["screening"], row["plans"]["full"]
        label = row["label"].replace("|", "\\|").replace("\n", " ").replace("[", "\\[").replace("]", "\\]")
        lines.append(f"| [{label}]({row['source_url']}) | {row['probe_count']} | {row['retained_self_runs']} / {row['retained_other_model_runs']} | {short['max_requests']} / {short['max_output_tokens_total']:,} | {full['max_requests']} / {full['max_output_tokens_total']:,} |")
    lines.extend(["", *[f"- {note}" for note in result["notes"]], ""])
    out.mkdir(parents=True, exist_ok=False)
    (out / "reference-audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "reference-audit.md").write_text("\n".join(lines), encoding="utf-8")
    return result
