"""Readable reports and response-free CSV exports of saved evidence."""

from __future__ import annotations

import copy
import csv
import json
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from .diagnostics import build_diagnostics
from .records import file_hash, load_records

STATUS_LABELS = {
    "behavior_difference": "发现行为差异",
    "no_difference_detected": "未发现差异",
    "insufficient_evidence": "证据不足",
}
ROLE_LABELS = {"reference": "参考端", "candidate": "待测端", "between_roles": "两端"}
REASON_LABELS = {
    "no_valid_records": "没有可用记录",
    "unverified_reference_kind": "参考来源尚未确认，需先核对参考端的来源",
    "model_declared_mismatch": "填写的模型名称或版本不一致，请核对是否为同一型号及版本",
    "config_hash_mismatch": "采集参数不一致，需要在匹配配置下重新采样",
    "overlapping_observation_ids": "两端包含相同观测编号，不能作为独立采样比较",
    "shared_source_log": "两端使用了同一份来源日志，不能作为独立采样比较",
    "synthetic_and_real_records_mixed": "合成记录与真实端点记录混合，需分开比较",
    "synthetic_label_and_reference_kind_inconsistent": "记录的合成标记与来源类型不一致",
    "invalid_sampling_plan_metadata": "采样计划格式有误",
    "inconsistent_sampling_plan_metadata": "记录中的采样计划不一致或部分缺失",
    "duplicate_replicate": "同一道题存在重复样本编号",
    "missing_prompt": "缺少这道题的记录",
    "failed_requests": "存在失败请求，需要先排查连接、权限、额度或接口错误",
    "below_engineering_sample_threshold": "成功样本少于当前最低要求，可增加重复次数后重新采样",
    "dependent_or_retried_observations": "记录包含重试、缓存或非独立采样标记",
    "unexpected_prompt_outside_plan": "出现采样计划之外的题目",
    "planned_replicate_coverage_mismatch": "实际样本编号未覆盖完整采样计划",
    "replicate_coverage_mismatch": "两端的重复采样编号不一致",
    "prompt_hash_mismatch": "同一题号下的提示内容不一致",
    "completion_exceeds_character_limit_no_truncation_applied": "回答超过分析长度上限，需核对任务或调整上限后重新分析",
    "all_successful_responses_empty": "两端的成功响应内容均为空",
    "met_invalid_or_nonfinite_result": "统计引擎返回了不可用结果",
    "met_dependency_unavailable": "统计依赖尚未安装完整，请按安装指南补齐依赖",
    "blocked_by_global_evidence_mismatch": "整体数据条件不匹配，本题未进行分布比较",
    "holm_corrected_distribution_test_rejected": "多题校正后仍检出回答分布差异",
    "statistical_non_rejection": "本题未检出回答分布差异",
    "power_not_calibrated": "尚未校准真实模型的检出能力，未检出差异仍保留为证据不足",
    "incomplete_or_invalid_observed_prompt_family": "部分题目缺少样本或比较条件不匹配，整体比较未完成",
    "no_holm_corrected_behavior_difference_detected": "在本次样本中没有检出通过多题校正的行为差异",
    "individual_signals_do_not_resolve_family_coverage_or_matching_failures": "部分题目出现差异信号，但完整采样或配置问题尚未解决",
    "collection_cancelled": "采集已取消，当前记录只覆盖已完成的请求",
    "collection_incomplete": "采集尚未完成，当前结果仅供检查已完成的请求",
    "planned_collection_not_completed": "本次采样计划尚未完成，可查看已保存的请求并安排新的完整采集",
    "cancelled": "采集已取消，当前记录只覆盖已完成的请求",
}


def reason_text(reason: str) -> str:
    """Translate stable reason codes without dropping original JSON codes."""
    if reason in REASON_LABELS:
        return REASON_LABELS[reason]
    prefix, _, rest = reason.partition(":")
    if prefix in ROLE_LABELS:
        if rest.startswith("mixed_"):
            names = {"endpoint_id": "服务标识", "run_id": "采集批次", "model_declared": "模型名称",
                     "config_hash": "采集配置", "reference_kind": "来源类型"}
            return f"{ROLE_LABELS[prefix]}混用了不同的{names.get(rest[6:], '记录条件')}"
        if rest.startswith("invalid_record_at_index:"):
            return f"{ROLE_LABELS[prefix]}存在格式无效的记录"
        return f"{ROLE_LABELS[prefix]}：{REASON_LABELS.get(rest, rest)}"
    if prefix == "met_execution_failed":
        return "统计计算没有完成，请核对安装环境和运行日志"
    return reason


def runtime_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for package in ["relay-audit", "inspect-ai", "model-equality-testing", "torch", "numpy"]:
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = "not installed"
    return versions


def _cell(value: Any) -> str:
    text = str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\r", " ").replace("\n", " ")
    for character in ("\\", "`", "*", "_", "[", "]", "!", "|"):
        text = text.replace(character, "\\" + character)
    return text


def _number(value: Any, *, digits: int = 3) -> str:
    return "未报告" if value is None else f"{value:.{digits}f}"


def _rate(value: Any) -> str:
    return "—" if value is None else f"{value:.1%}"


def _checks(summary: dict[str, Any]) -> str:
    checks = summary.get("checks", {})
    if not checks.get("evaluated"):
        return "未设置检查"
    return f"{checks['passed']}/{checks['evaluated']}（{_rate(checks['pass_rate'])}）"


def markdown_report(result: dict[str, Any]) -> str:
    status = result.get("status", "insufficient_evidence")
    coverage = result.get("coverage", {})
    diagnostics = result.get("diagnostics", {})
    lines = ["# API 服务一致性报告", "", f"本次结果：**{STATUS_LABELS.get(status, status)}**。", ""]
    if status == "behavior_difference":
        lines += [f"完成比较的 {coverage.get('tested_prompts', 0)} 道题中，{coverage.get('rejected_prompts', 0)} 道题的回答分布存在差异。建议先核对模型版本、参数和请求路径，再用新的样本复核。", ""]
    else:
        lines += ["当前样本还不足以判断两端的模型服务是否一致。下面列出影响判断的原因，以及已经观察到的请求表现。", ""]
    if result.get("synthetic"):
        lines += ["这是本地合成演示，用于熟悉操作和报告；没有调用真实模型。", ""]
    if result.get("lab"):
        lab = result["lab"]
        scenario = lab.get("scenario", {})
        lines += ["## 实验情境", "", f"情境：**{_cell(scenario.get('name', scenario.get('id', '可控离线实验')))}**。", ""]
        if scenario.get("description"):
            lines += [_cell(scenario["description"]), ""]
        lines += ["回答、耗时和用量均为人工构造的数据，用来比较不同机制会留下什么观察结果。实验中的真实机制已由程序设定；检测到的信号仍需按观察层面解释。", ""]
        if scenario.get("mechanism"):
            lines += [f"程序设定的机制：{_cell(scenario['mechanism'])}", ""]
        if lab.get("identifiability_demo"):
            lines += [_cell(lab["identifiability_demo"]), ""]
        if lab.get("teaching_questions"):
            lines += ["可以讨论：", ""] + [f"- {_cell(question)}" for question in lab["teaching_questions"]] + [""]
    collection = result.get("collection", {})
    if collection:
        lines += [f"采集进度：{collection.get('completed', '—')}/{collection.get('total', '—')} 个请求已完成" +
                  ("；已取消。" if collection.get("cancelled") else "。"), ""]
    lines += ["## 主要原因", ""]
    reasons = result.get("reason_messages") or [reason_text(reason) for reason in result.get("reasons", [])]
    lines += [f"- {_cell(reason)}" for reason in dict.fromkeys(reasons)] or ["- 比较条件满足要求，详见逐题结果。"]
    if diagnostics.get("roles"):
        lines += ["", "## 请求表现", "", "| 指标 | 参考端 | 待测端 |", "| --- | --- | --- |"]
        left, right = diagnostics["roles"]["reference"], diagnostics["roles"]["candidate"]
        for name, get in [
            ("请求成功", lambda row: f"{row['successes']}/{row['records']}（{_rate(row['success_rate'])}）"),
            ("请求失败", lambda row: str(row["errors"])),
            ("耗时中位数 / P95（秒）", lambda row: f"{_number(row['elapsed_seconds']['median'])} / {_number(row['elapsed_seconds']['p95'])}"),
            ("有耗时记录", lambda row: str(row["elapsed_seconds"]["count"])),
            ("限定任务通过", _checks),
        ]:
            lines.append(f"| {name} | {get(left)} | {get(right)} |")
        lines += ["", "请求成功表示接口正常返回；限定任务通过表示回答满足该题预设的答案或格式。耗时含网络、排队和服务处理时间。", "",
                  "### 来源自报 token", "", "| 计量项 | 参考端合计（有报告的请求数） | 待测端合计（有报告的请求数） |", "| --- | --- | --- |"]
        for field, title in (("input_tokens", "输入"), ("output_tokens", "输出"), ("total_tokens", "总量")):
            def usage_cell(row: dict[str, Any]) -> str:
                value = row["reported_tokens"][field]
                return "未报告" if value is None else f"{value}（{row['reported_token_records'][field]}/{row['records']}）"
            lines.append(f"| {title} | {usage_cell(left)} | {usage_cell(right)} |")
        lines += ["", "缺失计量保持缺失，合计只包含已报告的值。token 由来源提供；演示中的计量是人为设定的示例。", ""]
    lines += ["## 逐题结果", "", "| 题目 | 参考端成功/请求 | 待测端成功/请求 | 参考端任务通过 | 待测端任务通过 | 校正后 p 值 | 判断 |", "| --- | --- | --- | --- | --- | --- | --- |"]
    per_prompt = {row["prompt_id"]: row for row in diagnostics.get("prompts", [])}
    for item in result.get("prompt_results", []):
        summary = per_prompt.get(item["prompt_id"], {}).get("roles", {})
        counts = item.get("counts", {})
        def prompt_count(role: str) -> str:
            row = summary.get(role) or counts.get(role, {})
            return f"{row.get('successes', 0)}/{row.get('records', 0)}"
        conclusion = "发现差异" if item.get("status") == "behavior_difference" else "未检出差异" if item.get("tested") else "未比较"
        lines.append(f"| {_cell(item['prompt_id'])} | {prompt_count('reference')} | {prompt_count('candidate')} | {_checks(summary.get('reference', {}))} | {_checks(summary.get('candidate', {}))} | {_number(item.get('adjusted_p'), digits=4) if item.get('adjusted_p') is not None else '—'} | {conclusion} |")
    if not result.get("prompt_results"):
        lines.append("| 暂无可比较题目 | — | — | — | — | — | — |")
    lines += ["", "p 值描述本次分布检验的结果，不是模型为真的概率。任务通过率只反映这些题目；真实模型检出能力尚未校准，因此未检出差异仍保留为证据不足。", ""]
    for item in result.get("prompt_results", []):
        if not item.get("tested") and item.get("reasons"):
            messages = item.get("reason_messages") or [reason_text(reason) for reason in item["reasons"]]
            lines.append(f"- **{_cell(item['prompt_id'])}**：{'；'.join(_cell(text) for text in dict.fromkeys(messages))}。")
    study = result.get("study", {})
    if study:
        lines += ["", "## 服务档案与研究问题", "", "以下档案由研究者填写，公开声明与本次观察分别列出。", ""]
        for field, label in (("case_id", "案例编号"), ("service_name", "服务名称"), ("product", "套餐或产品"),
                             ("observed_on", "资料观察日期"), ("source_url", "资料来源"),
                             ("research_question", "研究问题"), ("notes", "研究备注")):
            if study.get(field):
                lines.append(f"- {label}：{_cell(study[field])}")
        for item in result.get("claim_matrix", []):
            lines += ["", f"### {_cell(item['label'])}", "", f"**声明**：{_cell(item['claim'])}", "",
                      f"**本次观察**：{_cell(item['observation'])}", "",
                      f"**下一步证据**：{_cell(item['next_evidence'])}"]
    settings = result.get("settings", {})
    lines += ["", "## 如何复核", "",
              f"本次采用逐题 MET 字符分布检验和 Holm 多题校正。置换次数 {settings.get('permutations', '—')}，显著性阈值 {settings.get('alpha', '—')}，最低样本数 {settings.get('min_samples', '—')}。最低样本数只是运行门槛。", "",
              "原始设置、逐题统计量、计量覆盖和组件版本保存在同目录的 `report.json`；`prompts.csv` 可在 Excel 中按题查看汇总。CSV 不含回答正文。", ""]
    for evidence in result.get("evidence_files", []):
        lines.append(f"- {ROLE_LABELS.get(evidence['role'], evidence['role'])}文件：`{_cell(evidence['filename'])}`；SHA-256 `{evidence['sha256']}`。")
    lines += ["", "文件哈希用于检查本地输入是否改变。行为差异需要结合服务承诺和配置解释；这份报告不观察服务端权重或内部数据用途。", "",
              "方法：[Model Equality Testing](https://github.com/irenasaracay/model-equality-testing)；采集框架：[Inspect AI](https://github.com/UKGovernmentBEIS/inspect_ai)。", ""]
    return "\n".join(lines)


def _csv_cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, str) and (value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@"))):
        return "'" + value
    return value


def write_prompt_csv(result: dict[str, Any], path: Path) -> None:
    """Export only per-prompt summaries, safely opened as an Excel CSV."""
    fields = ["prompt_id", "role", "records", "successes", "errors", "success_rate",
              "latency_count", "latency_median_seconds", "latency_p95_seconds",
              "reported_input_tokens", "reported_output_tokens", "reported_total_tokens",
              "input_tokens_reported_records", "output_tokens_reported_records", "total_tokens_reported_records",
              "checks_evaluated", "checks_passed", "checks_pass_rate", "tested", "adjusted_p", "status"]
    tests = {row["prompt_id"]: row for row in result.get("prompt_results", [])}
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for prompt in result.get("diagnostics", {}).get("prompts", []):
            test = tests.get(prompt["prompt_id"], {})
            for role in ("reference", "candidate"):
                row = prompt["roles"][role]
                output = {"prompt_id": prompt["prompt_id"], "role": role,
                          **{key: row[key] for key in ("records", "successes", "errors", "success_rate")},
                          "latency_count": row["elapsed_seconds"]["count"],
                          "latency_median_seconds": row["elapsed_seconds"]["median"],
                          "latency_p95_seconds": row["elapsed_seconds"]["p95"],
                          **{f"reported_{field}": row["reported_tokens"][field] for field in row["reported_tokens"]},
                          **{f"{field}_reported_records": count for field, count in row["reported_token_records"].items()},
                          **{f"checks_{field}": value for field, value in row["checks"].items()},
                          "tested": test.get("tested", False), "adjusted_p": test.get("adjusted_p"),
                          "status": test.get("status", "insufficient_evidence")}
                writer.writerow({key: _csv_cell(value) for key, value in output.items()})


def write_report(result: dict[str, Any], output_dir: Path, *, reference: Path, candidate: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in ("report.json", "report.md", "prompts.csv"):
        if (output_dir / name).exists():
            raise FileExistsError(f"Report already exists: {name}")
    enriched = copy.deepcopy(result)
    enriched["runtime_versions"] = runtime_versions()
    enriched["evidence_files"] = [
        {"role": role, "filename": path.name, "sha256": file_hash(path)}
        for role, path in [("reference", reference), ("candidate", candidate)]
    ]
    def read_evidence(path: Path):
        return load_records(path) if path.read_text(encoding="utf-8").strip() else []
    enriched["diagnostics"] = build_diagnostics(read_evidence(reference), read_evidence(candidate),
        prompt_ids=[item["prompt_id"] for item in enriched.get("prompt_results", [])])
    enriched["reason_messages"] = [reason_text(reason) for reason in enriched.get("reasons", [])]
    for item in enriched.get("prompt_results", []):
        item["reason_messages"] = [reason_text(reason) for reason in item.get("reasons", [])]
    with (output_dir / "report.json").open("x", encoding="utf-8") as stream:
        json.dump(enriched, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    with (output_dir / "report.md").open("x", encoding="utf-8") as stream:
        stream.write(markdown_report(enriched))
    write_prompt_csv(enriched, output_dir / "prompts.csv")
    return enriched
