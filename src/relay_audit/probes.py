"""Small public synthetic tasks for service checks, with explicit expectations."""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProbeCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["exact", "json_object", "contains"]
    expected: str | dict[str, Any]

    @model_validator(mode="after")
    def validate_expected(self) -> "ProbeCheck":
        if self.kind == "json_object" and not isinstance(self.expected, dict):
            raise ValueError("json_object expects an object")
        if self.kind != "json_object" and not isinstance(self.expected, str):
            raise ValueError("exact and contains expect text")
        if isinstance(self.expected, str) and not self.expected:
            raise ValueError("expected text cannot be empty")
        try:
            encoded = json.dumps(self.expected, ensure_ascii=False, allow_nan=False)
        except (ValueError, TypeError):
            raise ValueError("expected must be a finite JSON value") from None
        if len(encoded) > 8000:
            raise ValueError("expected value is too large")
        return self


_PROBES = [
    {
        "id": "arithmetic", "name": "两步算术", "description": "检查简单计算和只返回答案的要求。",
        "input": "计算 (17 + 8) × 3 - 30。只返回最终整数，不加解释。",
        "check": {"kind": "exact", "expected": "45"},
    },
    {
        "id": "json_format", "name": "JSON 格式", "description": "检查能否返回指定字段及类型的有效 JSON 对象。",
        "input": "只返回一个 JSON 对象，不要 Markdown 或解释：name 字段为字符串 relay，count 字段为整数 3，active 字段为布尔值 true。不要增加其他字段。",
        "check": {"kind": "json_object", "expected": {"name": "relay", "count": 3, "active": True}},
    },
    {
        "id": "extract_order", "name": "信息提取", "description": "从一段合成文字中提取订单号。",
        "input": "以下均为合成测试数据：联系人小林，订单号 TEST-2048，交付日期 2030-01-01。请只返回订单号。",
        "check": {"kind": "exact", "expected": "TEST-2048"},
    },
    {
        "id": "sort_numbers", "name": "排序与格式", "description": "检查数字排序和输出格式能否同时满足。",
        "input": "把 12, -3, 7, 0 按从小到大排列，只返回逗号分隔的数字，不加空格或解释。",
        "check": {"kind": "exact", "expected": "-3,0,7,12"},
    },
    {
        "id": "context_lookup", "name": "上下文查找", "description": "从合成清单找出指定标签，检查基本指令跟随。",
        "input": "合成清单：alpha=河流；beta=松树；gamma=星光。请用一句话回答 beta 对应什么，并在回答中保留原词。",
        "check": {"kind": "contains", "expected": "松树"},
    },
]


def list_probes() -> list[dict[str, Any]]:
    """Return independent copies so UI selection cannot modify shared defaults."""
    return deepcopy(_PROBES)
