"""Offline classroom scenarios: known constructed causes, observable effects.

No model or HTTP client is invoked. Artificial scenario labels are available
for teaching and must not be interpreted as diagnoses of real API services.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .probes import list_probes
from .records import AuditRecord, content_hash, file_hash, save_records
from .report import write_report
from .stats import compare_records


_SCENARIOS = [
    {
        "id": "honest", "name": "同源对照：正常波动",
        "description": "两端按相同规则回答，偶尔增减换行。看看普通波动如何出现在记录里。",
        "mechanism": "两端使用相同的人工响应规则，分别抽取格式变体。",
        "expected_signal": "任务通过率通常一致，分布检验可能不拒绝；这不等于认证了模型身份。",
        "alternatives": ["不同模型也可能在简单题上给出相同答案。", "题目太容易、样本太少或指标不敏感，都可能看不到差异。"],
    },
    {
        "id": "substitution", "name": "暗中替换：回答变了",
        "description": "待测端切换为另一套回答规则，三个任务都出现容易观察的变化。",
        "mechanism": "人工把待测端替换为另一套响应模式；这只是可控教学设置，没有真实模型。",
        "expected_signal": "回答分布和任务通过率可能改变，接口仍可正常返回。",
        "alternatives": ["提示被改写也能产生完全相同的输出。", "模型版本、推理配置或上下文变化也可能改变回答。"],
    },
    {
        "id": "mixed_route", "name": "混合路由：时好时坏",
        "description": "待测端每次以一半概率选择另一套响应模式，观察单次好答案为何不足以说明整体服务。",
        "mechanism": "用种子固定的伪随机数模拟逐请求路由，两种响应规则的设定概率各为 50%。",
        "expected_signal": "同一题可能出现两类答案，任务通过率降低；少量样本可能漏掉某条路线。",
        "alternatives": ["同一模型的随机采样也可能产生多种答案。", "时段、提示或参数改变也会造成回答不稳定。"],
    },
    {
        "id": "prompt_rewrite", "name": "改写提示：相同现象",
        "description": "保持教学设定中的模型不变，模拟中间层改写题意或格式要求。与“暗中替换”并排比较。",
        "mechanism": "人工设定中间层把算术条件、JSON 类型要求或回答格式改写，再生成另一套响应模式。",
        "expected_signal": "相同种子下，输出、耗时和计量与“暗中替换”完全相同；仅凭这些记录无法区分两个原因。",
        "alternatives": ["模型替换可以产生同样的可观察结果。", "应用层系统提示和上下文整理也可能影响输出。"],
    },
    {
        "id": "output_cap", "name": "输出截断：答案只剩一半",
        "description": "请求参数保持一致，待测端模拟额外截断，检查不完整 JSON 与短答案。",
        "mechanism": "人工把待测回答截到原长度的约三分之一；按字符截断，不模拟真实 tokenizer 或 token 上限。",
        "expected_signal": "响应仍成功，但格式或答案检查失败，模拟输出长度与计量降低。",
        "alternatives": ["模型自然结束、停止词、内容过滤或客户端截取，都可能产生短输出。", "真实 token 计量还取决于分词和隐藏推理，不能从字符长度直接推断账单。"],
    },
    {
        "id": "unstable", "name": "服务抖动：超时与排队",
        "description": "待测端每四轮模拟一次失败，部分成功请求也更慢；看看失败样本为何需要保留。",
        "mechanism": "人工安排每第四轮返回错误，并给部分请求加入虚构等待时间。没有实际等待或网络请求。",
        "expected_signal": "成功率和耗时分位数改变；失败记录使总体分布结论保持证据不足。",
        "alternatives": ["用户网络、限流、上游负载和中间层排队都可能导致类似表现。", "超时和慢响应本身无法说明模型是否被替换。"],
    },
]
_BY_ID = {item["id"]: item for item in _SCENARIOS}
_SCENARIO_IDS = Literal["honest", "substitution", "mixed_route", "prompt_rewrite", "output_cap", "unstable"]
_DECLARED_MODEL = "synthetic-lab/declared-a"


class _LabSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    scenario: _SCENARIO_IDS
    repeats: int = Field(default=12, ge=2, le=100)
    seed: int = Field(default=11, ge=0, lt=2**63)
    permutations: int = Field(default=199, ge=19, le=9999)
    min_samples: int = Field(default=8, ge=2, le=100)
    alpha: float = Field(default=0.05, gt=0.0, lt=1.0, allow_inf_nan=False)


def list_scenarios() -> list[dict[str, Any]]:
    """Return independent scenario cards for a UI or a classroom handout."""
    return deepcopy(_SCENARIOS)


def _rng(seed: int, role: str, prompt_id: str) -> random.Random:
    # Exclude scenario from the seed so substitution and prompt rewrite have
    # exactly the same observable trace when a learner compares them.
    digest = hashlib.sha256(f"{seed}:{role}:{prompt_id}".encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _answer(probe: dict[str, Any], *, alternate: bool) -> str:
    if alternate:
        return {
            "arithmetic": "44",
            "json_format": '{"name":"relay","count":"3","active":"true"}',
            "extract_order": "订单号为 TEST-2048。",
        }[probe["id"]]
    expected = probe["check"]["expected"]
    return json.dumps(expected, ensure_ascii=False, separators=(",", ":")) if isinstance(expected, dict) else expected


def _make_records(settings: _LabSettings, probes: list[dict[str, Any]], *,
                  role: str, run_id: str, config_hash: str, plan_hash: str) -> list[AuditRecord]:
    rows = []
    for probe in probes:
        rng = _rng(settings.seed, role, probe["id"])
        for replicate in range(settings.repeats):
            route_draw, format_draw, latency_draw = rng.random(), rng.random(), rng.random()
            candidate = role == "candidate"
            alternate = candidate and (settings.scenario in {"substitution", "prompt_rewrite"}
                                        or (settings.scenario == "mixed_route" and route_draw < 0.5))
            response = _answer(probe, alternate=alternate)
            # Whitespace variants pass all three narrow checks while allowing
            # response variation under the identical-rule control.
            if format_draw < 0.15:
                response = "\n" + response
            elif format_draw > 0.85:
                response += "\n"
            cap = None
            if candidate and settings.scenario == "output_cap":
                cap = max(1, len(response.strip()) // 3)
                response = response.strip()[:cap]
            failed = candidate and settings.scenario == "unstable" and replicate % 4 == 3
            elapsed = round(0.18 + 0.12 * latency_draw, 4)
            if candidate and settings.scenario == "unstable":
                elapsed += 8.0 if failed else 3.0 if replicate % 4 == 2 else 0.0
            # Invented accounting values are for exercising summaries. They
            # are not tokenization results, provider usage or cost estimates.
            output_tokens = max(1, len(response.strip()) // 3)
            metadata = {
                "backend": "offline_scenario_generator", "lab_scenario": settings.scenario,
                "simulated_route": "alternate_pattern" if alternate else "reference_pattern",
                "expected_prompt_ids": [item["id"] for item in probes],
                "expected_repeats": settings.repeats,
                "probe_check": deepcopy(probe["check"]),
                "collection_schema_version": "0.2", "sampling_plan_sha256": plan_hash,
                "generator_seed": settings.seed, "all_values_synthetic": True,
                "usage_origin": "artificial_example_counts_not_tokenizer_or_billing",
                "elapsed_basis": "artificial_example_seconds_no_network_or_wall_clock_measurement",
                "inspect_retries": [], "client_cache_hit": False,
                "simulated_output_character_cap": cap,
                "attribution": "Scenario labels describe this constructed exercise only; they cannot diagnose a real API.",
            }
            rows.append(AuditRecord(
                run_id=f"{run_id}:{role}", endpoint_id=f"lab-{role}", prompt_id=probe["id"],
                replicate=replicate,
                prompt_hash=content_hash({"messages": [{"role": "user", "content": probe["input"]}], "tools": []}),
                config_hash=config_hash, model_declared=_DECLARED_MODEL,
                reference_kind="synthetic_control", synthetic=True,
                status="error" if failed else "success", response=None if failed else response,
                error="simulated_timeout; artificial classroom event" if failed else None,
                elapsed_seconds=elapsed,
                usage={} if failed else {"input_tokens": 24, "output_tokens": output_tokens,
                                         "total_tokens": 24 + output_tokens},
                source_log=None, metadata=metadata,
            ))
    return rows


def run_scenario(scenario: str, out: Path, *, repeats: int = 12, seed: int = 11,
                 permutations: int = 199, min_samples: int = 8, alpha: float = 0.05) -> dict[str, Any]:
    """Generate at most 600 artificial records and analyze them without API calls."""
    try:
        settings = _LabSettings.model_validate({
            "scenario": scenario, "repeats": repeats, "seed": seed,
            "permutations": permutations, "min_samples": min_samples, "alpha": alpha,
        })
    except (ValidationError, TypeError):
        raise ValueError("Invalid lab settings: choose a listed scenario, repeats 2–100, seed 0–2^63-1, permutations 19–9999, min_samples 2–100 and alpha between 0 and 1") from None
    output = Path(out)
    if output.exists():
        raise FileExistsError("Output directory already exists; choose a new directory")
    probes = list_probes()[:3]
    run_id = uuid4().hex
    generation = {"model_declared": _DECLARED_MODEL, "max_tokens": 128, "temperature": 0.0,
                  "single_user_turn": True, "client_cache": False, "retries": 0}
    scenario_info = deepcopy(_BY_ID[settings.scenario])
    lab = {
        "scenario": scenario_info, "all_values_synthetic": True, "real_model_calls": 0,
        "seed": settings.seed, "repeats": settings.repeats, "records_generated": len(probes) * settings.repeats * 2,
        "generator": "relay_audit.lab deterministic synthetic rules; no Inspect or model execution",
        "power_calibration": False,
        "explanation": "回答、错误、路由、耗时和计量均由程序人工构造。情境标签是课堂揭示的后台设定，不是检测器从输出认定的真实原因。",
        "identifiability_demo": "相同种子下 substitution 与 prompt_rewrite 的回答、错误、耗时和计量完全相同。比较它们，讨论还需要哪些独立证据。",
        "teaching_questions": [
            "先只看回答与请求表现，你能观察到什么？",
            "查看幕后机制后，哪些其他原因也能解释这些现象？",
            "若要判断服务是否履行承诺，还需要哪些条款、配置或运营证据？",
        ],
        "measurement_note": "这里的耗时没有发生实际等待，token 也没有调用分词器；数值只用于演示诊断表，不能估算真实价格或检出能力。",
    }
    output.mkdir(parents=True, exist_ok=False)
    plan_path = output / "sampling_plan.json"
    with plan_path.open("x", encoding="utf-8") as stream:
        json.dump({"workflow": "lab", "run_id": run_id, "lab": lab,
                   "prompts": [{name: probe[name] for name in ("id", "input", "check")} for probe in probes],
                   "expected_prompt_ids": [probe["id"] for probe in probes], "expected_repeats": settings.repeats,
                   "generation": generation, "analysis": settings.model_dump()},
                  stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    plan_hash = file_hash(plan_path)
    records = {role: _make_records(settings, probes, role=role, run_id=run_id,
                                   config_hash=content_hash(generation), plan_hash=plan_hash)
               for role in ("reference", "candidate")}
    paths = {role: output / f"{role}.jsonl" for role in records}
    for role, rows in records.items():
        save_records(paths[role], rows)
    result = compare_records(records["reference"], records["candidate"],
                             permutations=settings.permutations, seed=settings.seed,
                             min_samples=settings.min_samples, alpha=settings.alpha)
    result.update(workflow="lab", lab=lab)
    return write_report(result, output, reference=paths["reference"], candidate=paths["candidate"])
