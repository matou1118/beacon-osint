from __future__ import annotations

import os
from typing import Any, Mapping

from ..base import BaseCollector


class ApiCollector(BaseCollector):
    def _request_json(
        self,
        url: str,
        method: str = "GET",
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> Any:
        if not self._is_allowed_url(url):
            raise PermissionError(f"URL outside source allowlist: {url}")
        if self.source.get("check_robots", True):
            self._assert_robots_allows(url)
        if self.client is None:
            raise RuntimeError("httpx is required; install requirements.txt")
        self._wait_for_rate_limit()
        headers = {"Accept": "application/json", "User-Agent": self.user_agent}
        auth = self.source.get("auth", {})
        token_env = auth.get("token_env")
        if token_env:
            token = os.getenv(str(token_env))
            if not token:
                raise RuntimeError(f"missing API credential environment variable: {token_env}")
            headers[str(auth.get("token_header", "Authorization"))] = f"Bearer {token}"
        response = self.client.request(
            method.upper(),
            url,
            params=dict(params or {}) or None,
            json=dict(json_body) if json_body is not None else None,
            headers=headers,
        )
        status = int(getattr(response, "status_code", 200))
        if status >= 400:
            raise RuntimeError(f"HTTP {status} for {url}")
        return response.json()

    def _items(self, payload: Any) -> list[Any]:
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return []
        key = self.source.get("items_key")
        if key and isinstance(payload.get(key), list):
            return list(payload[key])
        for candidate in ("items", "records", "data", "results"):
            if isinstance(payload.get(candidate), list):
                return list(payload[candidate])
        return []

    def _item_record(self, item: Mapping[str, Any], url: str | None = None) -> dict[str, Any]:
        parsed = {
            "title": item.get("title") or item.get("name") or "",
            "publish_date": item.get("publish_date") or item.get("date") or item.get("published_at"),
            "content": item.get("content") or item.get("text") or item.get("body") or "",
            "url": item.get("url") or item.get("link") or url or self.list_url,
        }
        return self.normalize_record(parsed)

    def fetch_list(self) -> list[dict[str, Any]]:
        endpoint = self.source.get("list_endpoint") or self.list_url
        payload = self._request_json(
            str(endpoint),
            method=str(self.source.get("method", "GET")),
            params=self.source.get("params"),
            json_body=self.source.get("json_body"),
        )
        items = self._items(payload)
        return [self._item_record(item) for item in items[: int(self.source.get("max_items", 20))]]

    def fetch_detail(self, url: str) -> dict[str, Any]:
        if not self._is_allowed_url(url):
            raise PermissionError(f"URL outside source allowlist: {url}")
        payload = self._request_json(url)
        if isinstance(payload, Mapping):
            return self._item_record(payload, url)
        response = self._request(url)
        return self.normalize_record(self.parse(response, url))

    def collect(self, limit: int | None = None) -> list[dict[str, Any]]:
        records = self.fetch_list()
        return records[:limit] if limit is not None else records
