"""Policy field extraction — deterministic, rule-based.

Extracts the comparison schema fields from an announcement record. Priority is
always the structured column already on the row (highest confidence), falling
back to regex windows over the body text. No model inference: a field either
matches a rule or lands in the review queue.

Confidence bands:
    >= 80  auto     trusted, goes straight into the comparison table
    >= 50  pending  plausible but needs human confirmation
    <  50  pending with empty value (nothing usable was found)
"""
from __future__ import annotations

import re
from typing import Any

# 通用对比字段：field_key → 提取规则。value_column 指向 Announcement 已有列。
FIELD_SCHEMA: list[dict[str, str]] = [
    {"field_key": "subsidy_amount", "label": "补贴标准", "value_column": "amount"},
    {"field_key": "target_group", "label": "申报条件", "value_column": "target_group"},
    {"field_key": "deadline", "label": "申报截止", "value_column": "deadline"},
    {"field_key": "institution", "label": "责任机构", "value_column": "institution_name"},
    {"field_key": "region", "label": "所属地区", "value_column": "region_province"},
]

# 正文兜底正则：仅在结构化列缺失时使用，置信度低于列来源。
BODY_PATTERNS: dict[str, tuple[re.Pattern[str], int]] = {
    "subsidy_amount": (
        re.compile(r"(?:补贴|资助|奖励|补助|扶持)[^。；\n]{0,30}?(\d+(?:\.\d+)?\s*(?:万元|亿元|万|亿|元))"),
        65,
    ),
    "target_group": (re.compile(r"(?:适用对象|补贴对象|申报条件|支持对象)[：:为]?\s*([^。；\n]{4,60})"), 60),
    "deadline": (re.compile(r"(?:截止|截至|申报时间|申报期限)[：:]?\s*([^。；\n]{4,40})"), 55),
    "institution": (re.compile(r"(?:联系单位|经办机构|咨询电话)[：:]?\s*([^。；\n，,]{3,30})"), 50),
}

AUTO_THRESHOLD = 80
PENDING_THRESHOLD = 50

COLUMN_CONFIDENCE = {
    "region": 95,
    "institution": 90,
    "subsidy_amount": 85,
    "target_group": 80,
    "deadline": 80,
    "key_measures": 75,
}


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _band(confidence: int) -> str:
    if confidence >= AUTO_THRESHOLD:
        return "auto"
    if confidence >= PENDING_THRESHOLD:
        return "pending"
    return "pending"


def extract_fields(record: dict[str, Any], field_schema: list[dict[str, str]] | None = None) -> list[dict[str, Any]]:
    """Extract schema fields from one announcement record (plain dict, no DB)."""
    schema = field_schema or FIELD_SCHEMA
    body = _clean(record.get("content"))[:600]
    results: list[dict[str, Any]] = []
    for spec in schema:
        key = spec["field_key"]
        column = spec.get("value_column", "")
        value = _clean(record.get(column)) if column else ""
        evidence = f"结构化字段 {column}"
        if key in COLUMN_CONFIDENCE:
            confidence = COLUMN_CONFIDENCE[key]
        else:
            confidence = 70
        if not value:
            pattern = BODY_PATTERNS.get(key)
            if pattern and body:
                match = pattern[0].search(body)
                if match:
                    value = _clean(match.group(1))
                    confidence = pattern[1]
                    evidence = f"正文命中：…{body[max(0, match.start() - 10):match.end() + 10]}…"
        if not value:
            value = None
            confidence = 0
            evidence = "结构化列与正文均未命中"
        results.append(
            {
                "field_key": key,
                "label": spec.get("label", key),
                "value": value,
                "confidence": confidence,
                "evidence_text": evidence,
                "method": "rule",
                "status": _band(confidence) if value else "pending",
            }
        )
    return results


def key_measures_from(record: dict[str, Any]) -> dict[str, Any]:
    """Key points live in a JSON list column; returned separately by callers that need it."""
    points = record.get("key_points") or []
    joined = "；".join(_clean(p) for p in points if _clean(p))
    confidence = COLUMN_CONFIDENCE["key_measures"] if joined else 0
    return {
        "field_key": "key_measures",
        "label": "关键举措",
        "value": joined or None,
        "confidence": confidence,
        "evidence_text": "结构化字段 key_points" if joined else "无 key_points 数据",
        "method": "rule",
        "status": _band(confidence) if joined else "pending",
    }
