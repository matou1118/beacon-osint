from __future__ import annotations

import re
from typing import Any, Mapping


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").casefold())


def _record_text(record: Mapping[str, Any], fields: list[Any]) -> str:
    values = []
    for field in fields:
        value = record.get(str(field))
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        else:
            values.append(str(value or ""))
    return _normalize(" ".join(values))


def find_industry_matches(record: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    matching = config.get("matching", {})
    fields = list(matching.get("fields", ["title", "content", "category_l1", "category_l2", "target_industry"]))
    text = _record_text(record, fields)
    matched: list[str] = []
    matched_labels: list[str] = []
    for key, industry in config.get("industries", {}).items():
        tags = list(industry.get("tags", []))
        hits = [tag for tag in tags if _normalize(tag) in text]
        if hits:
            matched.append(key)
            matched_labels.append(str(industry.get("label") or key))
    guidance_ids = {str(item) for item in config.get("guidance", {}).get("source_ids", [])}
    source_id = str(record.get("source_id") or "")
    source_role = str(record.get("source_role") or record.get("project_role") or "")
    guidance_source = source_id in guidance_ids or source_role == config.get("guidance", {}).get("source_role")
    guidance_year = None
    if guidance_source and record.get("publish_date"):
        guidance_year = str(record["publish_date"])[:4]
    return {
        "industry_focus": bool(matched),
        "industry_keys": matched,
        "industry_tags": matched_labels,
        "industry_reasons": [f"产业重点命中：{label}" for label in matched_labels],
        "guidance_source": guidance_source,
        "guidance_year": guidance_year,
    }


def apply_industry_focus(record: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(record)
    result.update(find_industry_matches(record, config))
    return result
