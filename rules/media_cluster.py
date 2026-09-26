from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from privacy import redact_sensitive

DEFAULT_WEIGHTS = {"official": 3.0, "mainstream": 2.0, "industry": 1.0}
DEFAULT_CATEGORY_WEIGHTS = {"policy": 2.0, "livelihood": 1.5, "industry": 1.0}
DEFAULT_TIME_DECAY = {"hours_24": 1.0, "hours_48": 0.5, "older": 0.2}


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def simhash(text: Any, bits: int = 64) -> int:
    tokens = re.findall(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]", str(text or "").casefold())
    if not tokens:
        return 0
    vector = [0] * bits
    for token in tokens:
        digest = int(hashlib.sha1(token.encode("utf-8")).hexdigest(), 16)
        for index in range(bits):
            vector[index] += 1 if (digest >> index) & 1 else -1
    value = 0
    for index, weight in enumerate(vector):
        if weight >= 0:
            value |= 1 << index
    return value


def hamming_distance(left: int, right: int) -> int:
    return (int(left) ^ int(right)).bit_count()


def _text(record: Mapping[str, Any]) -> str:
    return " ".join(str(record.get(key) or "") for key in ("title", "category_l1", "category_l2", "target_industry", "media_tags"))


def _matches(text: str, keywords: list[str]) -> list[str]:
    normalized = _normalize(text)
    return [str(keyword) for keyword in keywords if _normalize(keyword) and _normalize(keyword) in normalized]


def _category(record: Mapping[str, Any], settings: Mapping[str, Any]) -> tuple[str, float]:
    weights = dict(settings.get("category_weights") or DEFAULT_CATEGORY_WEIGHTS)
    text = _text(record)
    explicit = {"行业政策": "policy", "行业补贴": "policy", "人才政策": "policy", "养老与社区": "livelihood", "活动通知": "livelihood", "招生信息": "livelihood", "其他": "industry"}.get(str(record.get("category_l1") or ""))
    if explicit:
        return explicit, float(weights.get(explicit, 1.0))
    for category in ("policy", "livelihood", "industry"):
        keywords = settings.get(f"{category}_keywords") or []
        if _matches(text, list(keywords)):
            return category, float(weights.get(category, 1.0))
    return "industry", float(weights.get("industry", 1.0))


def _decay(latest_publish: Any, now: datetime, settings: Mapping[str, Any]) -> float:
    parsed = _parse_datetime(latest_publish)
    if parsed is None:
        return float((settings.get("time_decay") or DEFAULT_TIME_DECAY).get("older", 0.2))
    age = max(0.0, (now - parsed).total_seconds() / 3600)
    if age <= 24:
        return float((settings.get("time_decay") or DEFAULT_TIME_DECAY).get("hours_24", 1.0))
    if age <= 48:
        return float((settings.get("time_decay") or DEFAULT_TIME_DECAY).get("hours_48", 0.5))
    return float((settings.get("time_decay") or DEFAULT_TIME_DECAY).get("older", 0.2))


def calculate_heat(cluster: Mapping[str, Any], now: datetime | None = None, settings: Mapping[str, Any] | None = None) -> float:
    settings = dict(settings or {})
    weights = dict(settings.get("weights") or DEFAULT_WEIGHTS)
    current = now or datetime.now(timezone.utc)
    source_items = cluster.get("sources") or []
    source_weight = sum(float(weights.get(str(item.get("media_type") or "industry"), 1.0)) for item in source_items)
    density = len({str(item.get("source_id") or "") for item in source_items if item.get("source_id")})
    category_weight = float(cluster.get("category_weight") or 1.0)
    return round(source_weight * density * _decay(cluster.get("latest_publish"), current, settings) * category_weight, 2)


def cluster_media_records(records: list[Mapping[str, Any]], config: Mapping[str, Any], now: datetime | None = None) -> list[dict[str, Any]]:
    settings = dict(config.get("media_heat") or {})
    if not settings:
        return []
    current = now or datetime.now(timezone.utc)
    window = timedelta(hours=int(settings.get("cluster_window_hours", 72)))
    excluded = [str(item) for item in settings.get("exclude_keywords", [])]
    groups: list[dict[str, Any]] = []
    for record in records:
        if not record.get("media_type") and record.get("source_type") != "media":
            continue
        if record.get("needs_review"):
            continue
        text = _text(record)
        if _matches(text, excluded):
            continue
        category, category_weight = _category(record, settings)
        terms = _matches(text, list(settings.get("industry_keywords") or []))
        if not terms:
            terms = _matches(text, list(settings.get("policy_keywords") or []) + list(settings.get("livelihood_keywords") or []))
        if not terms:
            continue
        signature = simhash(f"{record.get('title') or ''} {' '.join(str(item) for item in (record.get('media_tags') or []))}")
        distance = int(settings.get("simhash_distance", 2))
        strong_terms = set(str(item) for item in settings.get("strong_topic_terms", []))
        current_strong = set(terms) & strong_terms
        min_shared = int(settings.get("min_shared_strong_terms", 1))
        def same_topic(group: dict[str, Any]) -> bool:
            overlap = bool(set(terms).intersection(group["terms"])) if not strong_terms else len(current_strong & set(group["strong_terms"])) >= min_shared
            return overlap or hamming_distance(signature, group["simhash"]) <= distance
        matched_group = next((group for group in groups if group["category"] == category and same_topic(group)), None)
        if matched_group is None:
            groups.append({"category": category, "category_weight": category_weight, "terms": sorted(set(terms)), "strong_terms": current_strong, "simhash": signature, "records": [record]})
        else:
            matched_group["records"].append(record)
            matched_group["terms"] = sorted(set(matched_group["terms"]) | set(terms))
            matched_group["strong_terms"] = sorted(set(matched_group["strong_terms"]) | current_strong)
    topics = []
    for group_data in groups:
        category = group_data["category"]
        terms = group_data["terms"]
        group = group_data["records"]
        dated = [item for item in group if _parse_datetime(item.get("publish_date")) is not None]
        anchor = max(dated or group, key=lambda item: _parse_datetime(item.get("publish_date")) or datetime.min.replace(tzinfo=timezone.utc))
        latest_item = max(dated or group, key=lambda item: _parse_datetime(item.get("publish_date")) or datetime.min.replace(tzinfo=timezone.utc))
        if current - (_parse_datetime(latest_item.get("publish_date")) or current) > window and dated:
            continue
        sources = []
        seen_sources = set()
        for item in group:
            source_id = str(item.get("source_id") or "")
            if not source_id or source_id in seen_sources:
                continue
            seen_sources.add(source_id)
            sources.append({"source_id": source_id, "source_name": item.get("issuing_body") or source_id, "media_type": item.get("media_type") or "industry", "source_url": item.get("source_url")})
        source_names = list(dict.fromkeys(str(item.get("source_name") or item.get("source_id")) for item in sources))
        if len(sources) < int(settings.get("min_sources", 2)):
            continue
        category_l1 = str(latest_item.get("category_l1") or "其他")
        cluster_id = hashlib.sha1(f"{category}:{','.join(terms)}".encode("utf-8")).hexdigest()[:16]
        topic = {
            "cluster_id": cluster_id,
            "title": str(anchor.get("title") or "未命名话题"),
            "category_l1": category_l1,
            "category_l2": latest_item.get("category_l2"),
            "terms": list(terms),
            "simhash": f"{group_data['simhash']:016x}",
            "sources": sources,
            "source_names": source_names,
            "source_count": len(sources),
            "latest_publish": latest_item.get("publish_date"),
            "article_urls": list(dict.fromkeys(str(item.get("source_url")) for item in group if item.get("source_url"))),
            "summary": redact_sensitive(str(latest_item.get("summary") or (latest_item.get("key_points") or [latest_item.get("content") or ""])[0]))[:400],
        }
        topic["category"] = category
        topic["category_weight"] = float((settings.get("category_weights") or DEFAULT_CATEGORY_WEIGHTS).get(category, 1.0))
        topic["heat_score"] = calculate_heat(topic, now=current, settings=settings)
        official_count = sum(1 for item in sources if item.get("media_type") == "official")
        topic["heat_reason"] = f"{len(sources)}家媒体报道，其中{official_count}家官方媒体"
        topics.append(topic)
    return sorted(topics, key=lambda item: (-float(item["heat_score"]), str(item.get("latest_publish") or "")), reverse=False)


def heat_summary(topics: list[Mapping[str, Any]]) -> dict[str, Any]:
    return {"total": len(topics), "official_topics": sum(1 for item in topics if any(source.get("media_type") == "official" for source in item.get("sources") or [])), "top_score": max((float(item.get("heat_score") or 0) for item in topics), default=0.0)}
