from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from collectors.base import BaseCollector


class MediaCollector(BaseCollector):
    def __init__(self, source: Mapping[str, Any], **kwargs: Any) -> None:
        normalized = dict(source)
        normalized.setdefault("url", normalized.get("list_url") or normalized.get("url") or "")
        super().__init__(normalized, **kwargs)
        self.errors: list[dict[str, str]] = []
        self.media_type = str(self.source.get("media_type") or "official")
        self.media_tags = [str(item) for item in (self.source.get("tags") or []) if str(item).strip()]

    def _soup(self, html: str) -> Any:
        return BeautifulSoup(html, "html.parser")

    def _link_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        path = parsed.path.lower()
        if parsed.scheme not in {"http", "https"} or not self._is_allowed_url(url):
            return False
        if path in {"", "/"} or path.endswith((".jpg", ".jpeg", ".png", ".gif", ".css", ".js", ".zip", ".rar")):
            return False
        if any(token in path for token in ("login", "search", "tag", "author", "advert", "comment", "about", "contact", "map", "special", "data-service", "our", "privacy", "terms")):
            return False
        detail_pattern = self.source.get("detail_pattern")
        if detail_pattern:
            return bool(re.search(str(detail_pattern), url))
        if bool(self.source.get("require_article_path", True)):
            parts = [part for part in path.split("/") if part]
            if len(parts) < 2 or parts[-1] in {"index.html", "index.htm", "list", "news"}:
                return False
        return True

    def _extract_links(self, html: str, base_url: str) -> list[str]:
        soup = self._soup(html)
        list_selector = self.source.get("list_selector")
        container = soup.select_one(str(list_selector)) if list_selector else soup
        if container is None:
            raise ValueError(f"list_selector did not match: {list_selector}")
        link_selector = str(self.source.get("link_selector") or "article a[href], .news-list a[href], .article-list a[href], .content-list a[href], .list a[href]")
        anchors = container.select(link_selector)
        if not anchors:
            anchors = container.select("a[href]")
        links = []
        for anchor in anchors:
            href = anchor.get("href") if hasattr(anchor, "get") else None
            if not href or not isinstance(href, str) or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            url = urljoin(base_url, href.strip())
            if self._link_allowed(url) and url.rstrip("/") != base_url.rstrip("/"):
                links.append(url)
        return list(dict.fromkeys(links))

    def fetch_list(self) -> list[str]:
        if self.source.get("rss_url"):
            return self.feed_urls()[: int(self.source.get("max_items", 20))]
        response = self._request(self.list_url)
        base_url = str(getattr(response, "url", self.list_url))
        return self._extract_links(self._response_text(response), base_url)[: int(self.source.get("max_items", 20))]

    def fetch_detail(self, url: str) -> dict[str, Any]:
        if not self._is_allowed_url(url):
            raise PermissionError(f"URL outside source allowlist: {url}")
        response = self._request(url)
        parsed = self.parse(response, str(getattr(response, "url", url)))
        if not parsed.get("title") or not parsed.get("content"):
            raise ValueError(f"media page has no usable title or content: {url}")
        record = self.normalize_record(parsed)
        record["source_type"] = "media"
        record["media_type"] = self.media_type
        record["media_tags"] = self.media_tags
        record["content_source"] = "media_html"
        record["needs_review"] = int(not parsed.get("publish_date") or len(str(parsed.get("content") or "")) < int(self.source.get("minimum_content_chars", 80)))
        return record

    def collect(self, limit: int | None = None) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        try:
            urls = self.fetch_list()
        except Exception as exc:
            self.errors.append({"url": self.list_url, "error": str(exc)})
            return records
        for url in urls:
            if limit is not None and len(records) >= limit:
                break
            try:
                records.append(self.fetch_detail(url))
            except Exception as exc:
                self.errors.append({"url": url, "error": str(exc)})
        return records
