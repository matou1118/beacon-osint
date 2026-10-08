from __future__ import annotations

import re
from typing import Any, Mapping

from .reasons import lookup, normalize_reasons, render_reason


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").casefold())


def _resolve(value: Any, user: Mapping[str, Any]) -> Any:
    if isinstance(value, str):
        match = re.fullmatch(r"\$\{([^}]+)\}", value.strip())
        if match:
            resolved = lookup({"user": user}, match.group(1))
            return resolved if resolved is not None else value
    return value


def _text_value(record: Mapping[str, Any]) -> str:
    return " ".join(str(record.get(key) or "") for key in ("title", "content", "category_l1", "category_l2"))


def _field_value(record: Mapping[str, Any], user: Mapping[str, Any], field: str) -> Any:
    if field.startswith("user."):
        return lookup({"user": user}, field)
    if field in {"text", "announcement.text"}:
        return _text_value(record)
    if field.startswith("announcement."):
        field = field[len("announcement.") :]
    return record.get(field)


def _contains(value: Any, expected: Any) -> bool:
    if isinstance(expected, (list, tuple, set)):
        return any(_contains(value, item) for item in expected)
    if value is None or expected is None:
        return False
    return _normalize(expected) in _normalize(value)


def _in(value: Any, expected: Any) -> bool:
    values = expected if isinstance(expected, (list, tuple, set)) else [expected]
    normalized = {_normalize(item) for item in values}
    return _normalize(value) in normalized


def _condition_value(record: Mapping[str, Any], user: Mapping[str, Any], condition: Mapping[str, Any]) -> tuple[bool, dict[str, Any]]:
    field = str(condition.get("field") or "")
    operator = str(condition.get("operator") or "eq")
    value = _field_value(record, user, field)
    expected = _resolve(condition.get("value"), user)
    extras: dict[str, Any] = {}
    if operator == "eq":
        return _normalize(value) == _normalize(expected), extras
    if operator == "contains":
        return _contains(value, expected), extras
    if operator == "in":
        return _in(value, expected), extras
    if operator == "not_null":
        return value is not None and str(value).strip() != "", extras
    if operator == "affirmative_contains":
        positive = condition.get("positive_terms", [])
        negative = condition.get("negative_terms", [])
        normalized = _normalize(value)
        matched = any(_normalize(term) in normalized for term in positive)
        blocked = any(_normalize(term) in normalized for term in negative)
        return matched and not blocked, extras
    if operator == "keyword_contains":
        keywords = expected if isinstance(expected, (list, tuple, set)) else [expected]
        matched_keywords = [str(keyword) for keyword in keywords if keyword and _contains(value, keyword)]
        extras["matched_keywords"] = matched_keywords
        return bool(matched_keywords), extras
    if operator == "hierarchy_match":
        return _contains(expected, value) or _contains(value, expected), extras
    raise ValueError(f"unsupported rule operator: {operator}")


def evaluate_rules(
    announcement: Mapping[str, Any],
    user: Mapping[str, Any],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    context = {"announcement": dict(announcement), "user": dict(user)}
    score = 0
    reasons: list[str] = []
    matched_rules: list[str] = []
    for rule in config.get("rules", []):
        extras: dict[str, Any] = {}
        matched = True
        for condition in rule.get("conditions", []):
            condition_matched, condition_extras = _condition_value(announcement, user, condition)
            matched = matched and condition_matched
            extras.update(condition_extras)
            if not matched:
                break
        if not matched:
            continue
        matched_rules.append(str(rule.get("name") or ""))
        score += int(rule.get("score", 0))
        rule_reasons = rule.get("reason", [])
        if isinstance(rule_reasons, str):
            rule_reasons = [rule_reasons]
        for template in rule_reasons:
            reasons.append(render_reason(str(template), context, extras))
    return {
        "score": score,
        "reasons": normalize_reasons(reasons),
        "matched_rules": [name for name in matched_rules if name],
    }


def _list_values(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    return [str(item) for item in values if str(item).strip()]


def _region_matches(record: Mapping[str, Any], region: str | None) -> bool:
    if not region:
        return True
    target = _normalize(region)
    values = [
        record.get("region_province"),
        record.get("region_city"),
        record.get("region_county"),
    ]
    if any(_normalize(value) == target or target in _normalize(value) or _normalize(value) in target for value in values if value):
        return True
    if record.get("gov_level") != "国家部委":
        return False
    # A national announcement carries no region field, so it only reaches a
    # region-scoped user when the text actually names that region. Returning True
    # unconditionally would flood every regional user with all ministry news.
    text = _normalize(f"{record.get('title') or ''} {record.get('content') or ''}")
    return bool(target) and target in text


def _has_positive_employment(value: Any) -> bool:
    text = _normalize(value)
    positive = ("明确保障", "包分配", "就业安排", "就业保障")
    negative = ("不保障", "不承诺", "原则上不安排", "不保证")
    return any(_normalize(term) in text for term in positive) and not any(_normalize(term) in text for term in negative)


def text_matches_keywords(record: Mapping[str, Any], keywords: Any) -> bool:
    """搜索关键词过滤 —— 与评分规则「关键词匹配」共用 `_text_value` 同一段文本。

    keywords 为空恒为 True（不做过滤）；单个关键词内部按空白拆词，
    「养老 补贴」命中任一词即算命中。空格在 `_normalize` 里会被抹掉，
    所以拆词必须在归一化之前做。
    """
    terms = [part for item in _list_values(keywords) for part in str(item).split() if part.strip()]
    if not terms:
        return True
    text = _normalize(_text_value(record))
    return any(_normalize(term) in text for term in terms)


def hard_filter_passes(record: Mapping[str, Any], user: Mapping[str, Any], config: Mapping[str, Any]) -> bool:
    levels = set(_list_values(user.get("levels")))
    media_types = set(config.get("media_source_types") or ["official_media", "mainstream_media", "industry_media"]) | {"official", "mainstream", "industry"}
    is_media = str(record.get("media_type") or "") in media_types
    if levels and not is_media and record.get("gov_level") not in levels:
        return False
    categories = set(_list_values(user.get("categories")))
    if categories and record.get("category_l1") not in categories:
        return False
    if not _region_matches(record, user.get("region")):
        return False
    industry_keys = set(_list_values(user.get("industry_keys")))
    if industry_keys and not set(_list_values(record.get("industry_keys"))).intersection(industry_keys):
        return False
    if user.get("industry_focus_only") and not record.get("industry_focus"):
        return False
    if record.get("category_l1") == "招生信息":
        admission_categories = set(_list_values(user.get("admission_sub_categories") or user.get("admission_sub_category")))
        if admission_categories and record.get("admission_sub_category") not in admission_categories:
            return False
        institution_types = set(_list_values(user.get("institution_types") or user.get("institution_type")))
        if institution_types and record.get("institution_type") not in institution_types:
            return False
        admission_types = set(_list_values(user.get("admission_types") or user.get("admission_type")))
        if admission_types and record.get("admission_type") not in admission_types:
            return False
        if user.get("employment_only") and not _has_positive_employment(record.get("employment_guarantee")):
            return False
    if record.get("validity") is False or record.get("needs_review") and user.get("exclude_review", True):
        return False
    return True


def match_announcements(
    records: list[Mapping[str, Any]],
    user: Mapping[str, Any],
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    matches = []
    for record in records:
        if not hard_filter_passes(record, user, config):
            continue
        result = evaluate_rules(record, user, config)
        matches.append({**dict(record), "matched": True, **result})
    matches.sort(key=lambda item: (-int(item.get("score", 0)), str(item.get("publish_date") or "")), reverse=False)
    return matches
