from __future__ import annotations

import re
from typing import Any, Mapping


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").casefold())


def find_focus_tags(record: Mapping[str, Any], config: Mapping[str, Any]) -> list[str]:
    focus_config = config.get("focus_tracking", config)
    if not focus_config.get("enabled", False):
        return []
    allowed_levels = {str(level) for level in focus_config.get("source_focus_levels", [])}
    source_level = str(record.get("focus_level") or "standard")
    if allowed_levels and source_level not in allowed_levels:
        return []
    fields = [
        record.get("title"),
        record.get("content"),
        record.get("text"),
        record.get("category_l1"),
        record.get("category_l2"),
        record.get("target_group"),
        record.get("target_industry"),
    ]
    keywords = record.get("keywords", [])
    if isinstance(keywords, list):
        fields.extend(keywords)
    haystack = _normalize(" ".join(str(value) for value in fields if value))
    tags = []
    for tag in focus_config.get("tags", []):
        normalized_tag = _normalize(tag)
        if normalized_tag and normalized_tag in haystack and tag not in tags:
            tags.append(str(tag))
    return tags


def _find_tags(record: Mapping[str, Any], tags: list[Any]) -> list[str]:
    fields = [
        record.get("title"),
        record.get("content"),
        record.get("text"),
        record.get("category_l1"),
        record.get("category_l2"),
        record.get("target_group"),
        record.get("target_industry"),
    ]
    keywords = record.get("keywords", [])
    if isinstance(keywords, list):
        fields.extend(keywords)
    haystack = _normalize(" ".join(str(value) for value in fields if value))
    matched = []
    for tag in tags:
        normalized_tag = _normalize(tag)
        if normalized_tag and normalized_tag in haystack and str(tag) not in matched:
            matched.append(str(tag))
    return matched


def find_project_tags(record: Mapping[str, Any], config: Mapping[str, Any]) -> list[str]:
    project_config = config.get("project_tracking", {})
    if not project_config.get("enabled", False):
        return []
    if record.get("project_role") != project_config.get("source_role"):
        return []
    return _find_tags(record, list(project_config.get("tags", [])))


def apply_focus_tracking(record: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    focus_config = config.get("focus_tracking", config)
    tags = find_focus_tags(record, config)
    project_tags = find_project_tags(record, config)
    tracked = dict(record)
    tracked["focus_tracked"] = bool(tags)
    tracked["focus_tags"] = tags
    tracked["focus_reasons"] = [f"重点标签命中：{tag}" for tag in tags]
    tracked["project_source"] = bool(record.get("project_role"))
    tracked["project_tracked"] = bool(project_tags)
    tracked["project_tags"] = project_tags
    tracked["project_reasons"] = [f"发改项目主线：{tag}" for tag in project_tags]
    if not focus_config.get("enabled", False):
        tracked["focus_reasons"] = []
    return tracked
