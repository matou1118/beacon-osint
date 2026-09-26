from __future__ import annotations

from typing import Any, Mapping

from .html_list import HtmlListCollector


class CountyCollector(HtmlListCollector):
    def __init__(self, source: Mapping[str, Any], **kwargs: Any) -> None:
        normalized = dict(source)
        normalized.setdefault("level", "county")
        normalized.setdefault("gov_level", "县级")
        super().__init__(normalized, **kwargs)
