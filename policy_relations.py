"""Topic-to-announcement relation scoring — deterministic keyword rules.

Scores how strongly an announcement belongs to a comparison topic. Title hits
dominate: a body mention of "文化" in boilerplate is noise, a title mention is
signal. Same weighting philosophy as policy_compare.tag_theme.

Thresholds:
    >= 90  auto      linked without review
    >= 60  pending   queued for the 关联确认 workbench
    <  60  dropped   recorded nowhere
"""
from __future__ import annotations

import re
from typing import Any

AUTO_THRESHOLD = 75
PENDING_THRESHOLD = 60

TITLE_HIT = 30
TITLE_MAX = 60
BODY_HIT = 15
BODY_MAX = 30
CATEGORY_MATCH = 10

# 层级链权重：国家文件置信度天然高（引用链短），县级文档靠关键词硬命中。
LEVEL_BONUS = {"国家部委": 5, "国家级": 5, "省级": 0, "市级": 0, "县级": 0, "区县级": 0}

# 正文共现通道：≥2 个不同关键词同时出现在正文，即使无标题命中也值得人工复核。
BODY_COOCURRENCE_FLOOR = 2


def normalize(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or "")).casefold()


def score_link(record: dict[str, Any], topic: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Return (confidence 0-100, evidence dict) for one announcement vs one topic."""
    title = normalize(record.get("title"))
    body = normalize(record.get("content"))
    category = str(record.get("category_l1") or "")
    topic_category = str(topic.get("category") or "")

    title_hits: list[str] = []
    body_hits: list[str] = []
    for keyword in topic.get("keywords") or []:
        token = normalize(keyword)
        if not token:
            continue
        if token in title and token not in title_hits:
            title_hits.append(token)
        if token in body and token not in body_hits:
            body_hits.append(token)

    score = min(len(title_hits) * TITLE_HIT, TITLE_MAX)
    score += min(len(body_hits) * BODY_HIT, BODY_MAX)
    if topic_category and category == topic_category:
        score += CATEGORY_MATCH
    score += LEVEL_BONUS.get(str(record.get("gov_level") or ""), 0)
    score = min(score, 100)

    evidence = {
        "title_hits": title_hits,
        "body_hits": body_hits,
        "category_match": bool(topic_category and category == topic_category),
        "level_bonus": LEVEL_BONUS.get(str(record.get("gov_level") or ""), 0),
    }
    return score, evidence


def link_status(confidence: int, body_hits: int = 0) -> str | None:
    """Map a score to a link status; None means below the review floor (drop).

    Two channels reach review: score-based (>= 60) and co-occurrence based
    (>= 2 distinct keywords in the body even when the title has no hit).
    """
    if confidence >= AUTO_THRESHOLD:
        return "auto"
    if confidence >= PENDING_THRESHOLD or body_hits >= BODY_COOCURRENCE_FLOOR:
        return "pending"
    return None
