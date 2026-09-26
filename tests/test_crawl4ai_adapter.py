from __future__ import annotations

import unittest

from collectors.crawl4ai_adapter import build_browser_config, build_crawler_config
from collectors.html_list import HtmlListCollector


class Crawl4AIAdapterTests(unittest.TestCase):
    def test_builds_safe_markdown_config_without_llm(self) -> None:
        config = build_crawler_config({"remove_overlay_elements": True, "page_timeout": 12000})
        self.assertTrue(config.remove_overlay_elements)
        self.assertTrue(config.check_robots_txt)
        self.assertEqual(config.page_timeout, 12000)
        self.assertIsNone(config.extraction_strategy)
        browser = build_browser_config({"browser_channel": "msedge"})
        self.assertEqual(browser.chrome_channel, "msedge")
        self.assertFalse(browser.enable_stealth)

    def test_css_schema_is_configured_without_llm(self) -> None:
        schema = {
            "name": "政策文件列表",
            "baseSelector": "div.policy-list",
            "fields": [
                {"name": "标题", "selector": "h3", "type": "text"},
                {"name": "链接", "selector": "a", "type": "attribute", "attribute": "href"},
            ],
        }
        config = build_crawler_config({"css_schema": schema})
        self.assertIsNotNone(config.extraction_strategy)
        self.assertEqual(config.extraction_strategy.schema["name"], "政策文件列表")

    def test_quality_and_markdown_parsing(self) -> None:
        source = {
            "id": "fixture_crawl",
            "domain": "example.gov.cn",
            "url": "https://example.gov.cn/list",
            "minimum_content_chars": 5,
        }
        collector = HtmlListCollector(source, client=object())
        result = {
            "markdown": "# 项目公告\n\n正文内容",
            "raw_markdown": "# 项目公告\n\n正文内容\n导航",
            "metadata": {"title": "项目公告"},
        }
        parsed = collector._parse_crawl4ai_result(result, "https://example.gov.cn/article/1")
        self.assertEqual(parsed["title"], "项目公告")
        self.assertEqual(parsed["content"], "# 项目公告\n\n正文内容")
        self.assertTrue(collector._crawl4ai_quality(result)["usable"])
        collector.close()


if __name__ == "__main__":
    unittest.main()
