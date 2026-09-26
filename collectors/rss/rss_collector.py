from __future__ import annotations

from typing import Any, Mapping

from ..base import BaseCollector


class RssCollector(BaseCollector):
    def __init__(self, source: Mapping[str, Any], **kwargs: Any) -> None:
        normalized = dict(source)
        normalized.setdefault("access_method", "rss")
        super().__init__(normalized, **kwargs)
        self.errors: list[dict[str, str]] = []

    def fetch_list(self) -> list[str]:
        return self.feed_urls()[: int(self.source.get("max_items", 20))]

    def fetch_detail(self, url: str) -> dict[str, Any]:
        if not self._is_allowed_url(url):
            raise PermissionError(f"URL outside source allowlist: {url}")
        response = self._request(url)
        final_url = str(getattr(response, "url", url))
        return self.normalize_record(self.parse(response, final_url))

    def collect(self, limit: int | None = None) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for url in self.fetch_list():
            if limit is not None and len(records) >= limit:
                break
            try:
                records.append(self.fetch_detail(url))
            except Exception as exc:
                self.errors.append({"url": url, "error": str(exc)})
        return records
