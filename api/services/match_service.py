from __future__ import annotations

from typing import Any, Mapping

import yaml

from rules.engine import evaluate_rules, hard_filter_passes


class MatchService:
    def __init__(self, rules_path: str = "config/rules.yaml") -> None:
        with open(rules_path, "r", encoding="utf-8") as handle:
            self.config = yaml.safe_load(handle) or {}
        if not isinstance(self.config, dict):
            raise ValueError("config/rules.yaml must contain a mapping")

    def match(self, record: Mapping[str, Any], user: Mapping[str, Any] | None) -> dict[str, Any] | None:
        profile = dict(user or {})
        profile.setdefault("exclude_review", True)
        if not hard_filter_passes(record, profile, self.config):
            return None
        result = evaluate_rules(record, profile, self.config)
        matched = dict(record)
        matched["matched"] = True
        matched["match_score"] = int(result.get("score") or 0)
        matched["match_reasons"] = list(result.get("reasons") or [])
        return matched

    def match_many(self, records: list[Mapping[str, Any]], user: Mapping[str, Any] | None) -> list[dict[str, Any]]:
        results = [self.match(record, user) for record in records]
        return sorted((item for item in results if item is not None), key=lambda item: (-int(item.get("match_score") or 0), str(item.get("publish_date") or "")), reverse=False)
