from __future__ import annotations

from typing import Any, Mapping

from .html_list import HtmlListCollector


class ProvincialCollector(HtmlListCollector):
    def __init__(self, source: Mapping[str, Any], **kwargs: Any) -> None:
        normalized = dict(source)
        normalized.setdefault("level", "provincial")
        normalized.setdefault("gov_level", "省级")
        super().__init__(normalized, **kwargs)
