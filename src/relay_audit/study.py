"""Link operator claims to observations without turning them into verdicts."""

from __future__ import annotations

import copy
import json
import os
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StudyCard(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    case_id: str = Field(default="", max_length=120)
    service_name: str = Field(default="", max_length=200)
    product: str = Field(default="", max_length=300)
    observed_on: str = Field(default="", max_length=10)
    source_url: str = Field(default="", max_length=2048)
    model_claim: str = Field(default="", max_length=2000)
    price_claim: str = Field(default="", max_length=2000)
    route_claim: str = Field(default="", max_length=2000)
    data_policy_claim: str = Field(default="", max_length=2000)
    remedy_claim: str = Field(default="", max_length=2000)
    research_question: str = Field(default="", max_length=2000)
    notes: str = Field(default="", max_length=4000)

    @field_validator("observed_on")
    @classmethod
    def valid_date(cls, value):
        if value and date.fromisoformat(value).isoformat() != value:
            raise ValueError("Use YYYY-MM-DD")
        return value

    @field_validator("source_url")
    @classmethod
    def public_url(cls, value):
        if value:
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("Use a public HTTP(S) source URL without credentials")
        return value


def validate_study(value: object) -> dict:
    if value is None:
        return {}
    try:
        return StudyCard.model_validate(value).model_dump(exclude_defaults=True)
    except ValueError:
        raise ValueError("请检查服务档案：日期为YYYY-MM-DD，来源为HTTP(S)链接，文本不超过各字段限制。") from None


def claim_matrix(report: dict, study: dict) -> list[dict]:
    """Suggestions describe evidence needed, never infer operators' hidden acts."""
    roles = report.get("diagnostics", {}).get("roles", {})
    candidate = roles.get("candidate", {})
    difference = report.get("status") in {"behavior_difference", "difference_detected"}
    behavior = ("本次采样检出了回答分布差异，需核对配置、版本和服务约定。" if difference
                else "本次采样未形成总体差异结论；可查看逐题结果和采样覆盖。")
    is_fingerprint = report.get("kind") == "fingerprint"
    if is_fingerprint:
        behavior = {"difference_detected": "待测回答与所选参考指纹存在显著差异，需核对版本、参数和参考来源。",
                    "no_difference_detected": "本次未检出与所选参考指纹的明显差异；结果与参考包的版本和设置有关。",
                    "insufficient_evidence": "当前指纹证据不足，可检查缺失回答、采集状态和参考覆盖。"}.get(report.get("status"), "当前指纹证据不足。")
    collection = report.get("collection", {})
    service_observation = (f"本次记录{collection.get('requests', 0)}个请求；采集状态见报告，尚未验证投诉、退款或长期可用性。" if is_fingerprint
                           else f"本次待测端记录{candidate.get('records', 0)}次请求，其中{candidate.get('errors', 0)}次错误；尚未验证投诉、退款或长期可用性。")
    definitions = [
        ("model_claim", "模型与能力", behavior,
         "检查提示、参数与版本后，在另一时段复采；必要时向服务方索取上游型号及配置记录。"),
        ("price_claim", "价格与计量", "报告保留服务端自报用量；本次未核对余额变化、实际扣费或结算账单。",
         "保存同一工作负载的账单和价格条款，核对缓存、推理token、倍率、币种及用途限制。"),
        ("route_claim", "上游与路由", "报告保存待测响应与参考材料；响应内容与延迟不能独立证明请求经过哪些主体。",
         "收集上游披露、路由变更条款与可核验的服务日志，区分声明路径和实测路径。"),
        ("data_policy_claim", "数据留存与用途", "本次黑盒检测没有服务端留存、删除或训练用途的直接证据。",
         "核对适用产品、数据类别、保留时长、例外、转委托及删除渠道；进一步证据需运营方配合。"),
        ("remedy_claim", "持续服务与补救",
         service_observation,
         "结合故障记录、服务条款和有同意的用户访谈，核查通知、退款及停运余额处理。"),
    ]
    return [{"field": field, "label": label, "claim": study[field],
             "claim_status": "研究者录入的公开声明，未经工具认证",
             "observation": observation, "next_evidence": next_evidence}
            for field, label, observation, next_evidence in definitions if study.get(field)]


def redact_study(study: dict, pair_payload: dict) -> dict:
    """Remove either endpoint's known key if it was pasted into research notes."""
    from .collect import _redact

    keys = []
    for role in ("reference", "candidate"):
        endpoint = pair_payload.get(role, {})
        key = endpoint.get("api_key") or os.environ.get(endpoint.get("api_key_env") or "")
        if isinstance(key, str) and key:
            keys.append(key)
    return _redact(study, tuple(keys))


def attach_study(report: dict, study: dict, output_dir: Path) -> dict:
    """Complete a newly generated report with a separately stored study card.

    Refuse to attach twice. Statistical results and source records stay intact.
    The source URL is recorded as text and is never fetched by this module.
    """
    cleaned = validate_study(study)
    if not cleaned:
        return report
    from .report import markdown_report
    if report.get("kind") == "fingerprint":
        from .fingerprint import markdown_fingerprint_report
        markdown_report = markdown_fingerprint_report

    enriched = copy.deepcopy(report)
    enriched["study"] = cleaned
    enriched["claim_matrix"] = claim_matrix(enriched, cleaned)
    card_path = output_dir / "study.json"
    with card_path.open("x", encoding="utf-8") as stream:
        json.dump({"schema_version": "0.1", "source_status": "researcher_entered_unverified",
                   "study": cleaned}, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    for name, text in (
        ("report.json", json.dumps(enriched, ensure_ascii=False, indent=2, allow_nan=False)+"\n"),
        ("report.md", markdown_report(enriched)),
    ):
        temporary = output_dir / (name+".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(output_dir / name)
    return enriched
