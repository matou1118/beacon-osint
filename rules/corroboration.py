from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import urlparse

from extractors.verification_gate import classify_for_verification


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _domain(record: Mapping[str, Any]) -> str:
    return (urlparse(str(record.get("source_url") or record.get("url") or "")).hostname or "").lower()


def _terms(record: Mapping[str, Any], vocabulary: list[str]) -> tuple[str, ...]:
    text = _normalize(" ".join(str(record.get(key) or "") for key in ("title", "content", "category_l1", "category_l2", "target_industry")))
    return tuple(_normalize(term) for term in vocabulary if _normalize(term) and _normalize(term) in text)


def _month(value: Any) -> str:
    text = str(value or "")
    return text[:7] if len(text) >= 7 else "unknown"


def _admissible(record: Mapping[str, Any]) -> bool:
    """Whether a record may count as an independent corroborating source.

    Government sites are authoritative on their own publication. Media has to
    pass the scope gate (AGENTS.md 1.6: industry, livelihood and economic
    reporting only) and carry a comparable fact, otherwise a 时政 wire story
    would happily raise a policy item's corroboration count.
    """
    if str(record.get("source_type") or "").strip().lower() != "media":
        return True
    return bool(classify_for_verification(record)["usable"])


def corroborate_records(records: list[Mapping[str, Any]], config: Mapping[str, Any]) -> list[dict[str, Any]]:
    settings = dict(config.get("corroboration") or {})
    if not settings.get("enabled", True):
        return [{**dict(record), "corroboration_status": "disabled", "corroboration_count": 0, "corroborating_sources": [], "corroboration_reason": "交叉验证已关闭"} for record in records]
    vocabulary = [str(item) for item in settings.get("topic_terms", []) if str(item).strip()]
    minimum = max(2, int(settings.get("min_distinct_sources", 2)))
    groups: dict[tuple[str, ...], list[Mapping[str, Any]]] = {}
    for record in records:
        terms = _terms(record, vocabulary)
        if terms:
            key = (str(record.get("region_province") or record.get("region_city") or ""), str(record.get("category_l1") or ""), _month(record.get("publish_date")), *terms)
        else:
            title = _normalize(record.get("title"))[:24]
            key = (str(record.get("region_province") or record.get("region_city") or ""), str(record.get("category_l1") or ""), _month(record.get("publish_date")), "title", title)
        groups.setdefault(key, []).append(record)
    results = []
    for record in records:
        terms = _terms(record, vocabulary)
        if terms:
            key = (str(record.get("region_province") or record.get("region_city") or ""), str(record.get("category_l1") or ""), _month(record.get("publish_date")), *terms)
        else:
            key = (str(record.get("region_province") or record.get("region_city") or ""), str(record.get("category_l1") or ""), _month(record.get("publish_date")), "title", _normalize(record.get("title"))[:24])
        group = groups.get(key, [])
        pool = [item for item in group if _admissible(item)]
        excluded = len(group) - len(pool)
        domains = {_domain(item) for item in pool if _domain(item)}
        sources = [{"source_id": item.get("source_id"), "domain": _domain(item), "source_url": item.get("source_url"), "source_type": item.get("source_type")} for item in pool if _domain(item)]
        status = "corroborated" if len(domains) >= minimum else "single_source"
        reason = f"交叉来源验证：{len(domains)}个独立域名"
        if excluded:
            reason += f"（已排除{excluded}条超出§1.6范围的媒体源）"
        else:
            reason += "，等待交叉验证" if status == "single_source" else ""
        result = {**dict(record), "corroboration_status": status, "corroboration_count": len(domains), "corroborating_sources": sources, "corroboration_reason": reason, "corroboration_excluded_media": excluded}
        results.append(result)
    return results


def corroboration_summary(records: list[Mapping[str, Any]]) -> dict[str, int]:
    return {
        "total": len(records),
        "corroborated": sum(1 for record in records if record.get("corroboration_status") == "corroborated"),
        "single_source": sum(1 for record in records if record.get("corroboration_status") == "single_source"),
        "disabled": sum(1 for record in records if record.get("corroboration_status") == "disabled"),
    }
