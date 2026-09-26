from __future__ import annotations

from typing import Any, Mapping

from .html_list import HtmlListCollector


class NationalCollector(HtmlListCollector):
    def __init__(self, source: Mapping[str, Any], **kwargs: Any) -> None:
        normalized = dict(source)
        normalized.setdefault("level", "national")
        normalized.setdefault("gov_level", "国家部委")
        super().__init__(normalized, **kwargs)
