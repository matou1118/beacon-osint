from __future__ import annotations

import unittest

from collectors.media.media_collector import MediaCollector


class FakeResponse:
    def __init__(self, text: str, url: str) -> None:
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = 200
        self.url = url


class FakeClient:
    def get(self, url: str, headers: dict[str, str] | None = None) -> FakeResponse:
        if url.endswith("/robots.txt"):
            return FakeResponse("User-agent: *\nAllow: /\n", url)
        if url.endswith("/list"):
            return FakeResponse("<main class='news-list'><a href='/news/1'>B300芯片政策发布</a><a href='/about'>关于</a></main>", url)
        return FakeResponse("<html><head><title>B300芯片政策发布</title><meta name='publish_date' content='2026-09-25'></head><body><article>面向行业发布人工智能算力与新能源政策，企业可申报项目。</article></body></html>", url)

    def close(self) -> None:
        return


class MediaCollectorTests(unittest.TestCase):
    def test_media_source_is_parsed_and_labeled(self) -> None:
        source = {"id": "media_fixture", "name": "测试行业媒体", "domain": "media.example.cn", "list_url": "https://media.example.cn/list", "type": "media", "media_type": "industry", "tags": ["芯片", "AI"], "check_robots": True, "minimum_content_chars": 20}
        collector = MediaCollector(source, client=FakeClient())
        records = collector.collect(limit=1)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["media_type"], "industry")
        self.assertEqual(records[0]["source_type"], "media")
        self.assertIn("芯片", records[0]["media_tags"])
        self.assertEqual(collector.errors, [])


if __name__ == "__main__":
    unittest.main()
