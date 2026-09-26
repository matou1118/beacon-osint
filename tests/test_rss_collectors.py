from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from collectors.rss.rss_collector import RssCollector


class FakeResponse:
    def __init__(self, text: str, url: str) -> None:
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = 200
        self.url = url


class FakeClient:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def get(self, url: str, headers: dict[str, str] | None = None) -> FakeResponse:
        self.urls.append(url)
        return FakeResponse("<html><head><title>测试公告</title></head><body><h1>测试公告</h1><p>正文内容</p></body></html>", url)

    def close(self) -> None:
        return None


class RssCollectorTests(unittest.TestCase):
    def test_rss_entries_become_normalized_records(self) -> None:
        source = {
            "id": "fixture_rss",
            "name": "测试来源",
            "domain": "example.gov.cn",
            "url": "https://example.gov.cn/",
            "rss_url": "https://example.gov.cn/rss",
            "check_robots": False,
        }
        fake_feed = SimpleNamespace(entries=[{"link": "https://example.gov.cn/article/1"}])
        with patch("collectors.base.feedparser", SimpleNamespace(parse=lambda content: fake_feed)):
            records = RssCollector(source, client=FakeClient()).collect()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["title"], "测试公告")
        self.assertEqual(records[0]["source_id"], "fixture_rss")


if __name__ == "__main__":
    unittest.main()
