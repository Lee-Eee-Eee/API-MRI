"""Human-readable evidence reports; statistical decisions come from stats.py."""

from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from .records import file_hash

STATUS_LABELS = {
    "behavior_difference": "发现行为差异",
    "no_difference_detected": "未发现差异",
    "insufficient_evidence": "证据不足",
}


def runtime_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for package in ["relay-audit", "inspect-ai", "model-equality-testing", "torch", "numpy"]:
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = "not installed"
    return versions


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def markdown_report(result: dict[str, Any]) -> str:
    status = result.get("status", "insufficient_evidence")
    synthetic = result.get("synthetic", False)
    lines = [
        "# API 服务一致性证据报告",
        "",
        f"结论：**{STATUS_LABELS.get(status, status)}**。",
        "",
        "本报告针对记录中的任务、配置、参考与采样时段。差异不能认证模型权重、识别替代模型、证明欺诈、数据泄露或蒸馏。",
        "",
    ]
    if synthetic:
        lines += ["**本次为合成 fixture 流程验证，未调用真实模型；结果不能当作真实模型检测准确率。**", ""]
    lines += [
        "非拒绝零假设不能证明模型相同。当前首版没有真实模型功效校准；未检出差异时总体结论保留为证据不足。",
        "",
        "## 判断理由",
        "",
    ]
    reasons = result.get("reasons", [])
    lines += [f"- {_cell(reason)}" for reason in reasons] or ["- 参见逐题检验与覆盖记录。"]
    lines += ["", "## 检验与覆盖", "", "```json", json.dumps({
        "method": result.get("method"),
        "settings": result.get("settings"),
        "coverage": result.get("coverage"),
        "prompt_results": result.get("prompt_results"),
    }, ensure_ascii=False, indent=2), "```", "", "## 来源与复算", ""]
    for evidence in result.get("evidence_files", []):
        lines.append(f"- {_cell(evidence['role'])}：{_cell(evidence['filename'])}；SHA-256 `{evidence['sha256']}`。")
    lines += [
        "",
        "哈希验证本组保存文件的一致性，不证明供应商后台行为。源 Inspect 日志与派生 JSONL 的关系另见每条记录。",
        "",
        "组件版本：",
        "",
        "```json",
        json.dumps(result.get("runtime_versions", {}), ensure_ascii=False, indent=2),
        "```",
        "",
        "方法来源：[Model Equality Testing](https://github.com/irenasaracay/model-equality-testing)；执行框架：[Inspect AI](https://github.com/UKGovernmentBEIS/inspect_ai)。",
        "",
        "本报告由研究工具生成，需小组成员核对来源、实验条件和解释。",
        "",
    ]
    return "\n".join(lines)


def write_report(result: dict[str, Any], output_dir: Path, *, reference: Path, candidate: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    enriched = dict(result)
    enriched["runtime_versions"] = runtime_versions()
    enriched["evidence_files"] = [
        {"role": role, "filename": path.name, "sha256": file_hash(path)}
        for role, path in [("reference", reference), ("candidate", candidate)]
    ]
    with (output_dir / "report.json").open("x", encoding="utf-8") as stream:
        json.dump(enriched, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    with (output_dir / "report.md").open("x", encoding="utf-8") as stream:
        stream.write(markdown_report(enriched))
    return enriched

