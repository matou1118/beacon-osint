from __future__ import annotations

import unittest

from collectors.base import optional_deps_health
from collectors.national import NationalCollector


class FakeResponse:
    def __init__(self, text: str, status_code: int = 200, url: str = "") -> None:
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = status_code
        self.url = url


class FakeClient:
    def __init__(self, responses: dict[str, FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url: str, headers: dict[str, str] | None = None) -> FakeResponse:
        self.calls.append(url)
        if url not in self.responses:
            raise AssertionError(f"unexpected URL: {url}")
        response = self.responses[url]
        if not response.url:
            response.url = url
        return response

    def close(self) -> None:
        return None


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


class NationalCollectorTests(unittest.TestCase):
    def test_collects_normalized_records_and_honors_robots(self) -> None:
        source = {
            "id": "test_gov",
            "name": "测试部委",
            "domain": "example.gov.cn",
            "url": "https://www.example.gov.cn/archive/",
            "gov_level": "国家部委",
            "type": "archive",
            "region_province": "四川省",
            "retention_class": "active",
            "retention_days": 30,
            "max_items": 1,
            "check_robots": True,
        }
        robots_url = "https://www.example.gov.cn/robots.txt"
        list_url = source["url"]
        detail_url = "https://www.example.gov.cn/article/1"
        client = FakeClient(
            {
                robots_url: FakeResponse("User-agent: *\nAllow: /"),
                list_url: FakeResponse(
                    '<html><body><a href="/article/1">政策公告</a><a href="javascript:void(0)">忽略</a></body></html>',
                    url=list_url,
                ),
                detail_url: FakeResponse(
                    '<html><head><meta name="publishdate" content="2026-01-02"></head>'
                    '<body><h1>政策公告</h1><p>这是公告正文。</p></body></html>',
                    url=detail_url,
                ),
            }
        )
        clock = FakeClock()
        collector = NationalCollector(source, client=client, clock=clock, sleep=clock.sleep)
        records = collector.collect(limit=1)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["source_id"], "test_gov")
        self.assertEqual(records[0]["gov_level"], "国家部委")
        self.assertEqual(records[0]["region_province"], "四川省")
        self.assertEqual(records[0]["retention_class"], "active")
        self.assertEqual(records[0]["retention_until"], "2026-02-01T00:00:00+00:00")
        self.assertEqual(records[0]["publish_date"], "2026-01-02")
        self.assertIn("公告正文", records[0]["content"])
        self.assertEqual(len(records[0]["content_hash"]), 64)
        self.assertGreaterEqual(clock.value, 3.0)
        self.assertEqual(client.calls.count(detail_url), 1)

    def test_content_hash_changes_only_with_content(self) -> None:
        first = {"title": "标题", "publish_date": "2026-01-01", "url": "https://x", "content": "正文"}
        second = {**first}
        third = {**first, "content": "修改后的正文"}
        self.assertEqual(NationalCollector.compute_content_hash(first), NationalCollector.compute_content_hash(second))
        self.assertNotEqual(NationalCollector.compute_content_hash(first), NationalCollector.compute_content_hash(third))

    def _list_only_collector(self, list_url: str) -> NationalCollector:
        return NationalCollector(
            {
                "id": "test_gov",
                "name": "测试部委",
                "domain": "example.gov.cn",
                "url": list_url,
                "gov_level": "国家部委",
                "type": "archive",
                "retention_class": "active",
                "check_robots": False,
            }
        )

    def test_navigation_links_are_not_treated_as_announcements(self) -> None:
        collector = self._list_only_collector("https://www.example.gov.cn/index.html")
        for url in (
            "https://www.example.gov.cn/zwgk/",
            "https://www.example.gov.cn/about/index.html",
            "https://www.example.gov.cn/xxgkml/index.shtml",
            "https://www.example.gov.cn/lxwm/",
        ):
            with self.subTest(url=url):
                self.assertFalse(collector._link_allowed(url))

    def test_real_announcement_urls_still_allowed(self) -> None:
        collector = self._list_only_collector("https://www.example.gov.cn/index.html")
        for url in (
            "https://www.example.gov.cn/zwgk/202601/t20260102_1.html",
            "https://www.example.gov.cn/content/2026-01/02/c123.html",
            "https://www.example.gov.cn/art/2026/1/2/art_1_1.html",
        ):
            with self.subTest(url=url):
                self.assertTrue(collector._link_allowed(url))

    def test_source_can_extend_navigation_segments(self) -> None:
        collector = NationalCollector(
            {
                "id": "test_gov",
                "name": "测试部委",
                "domain": "example.gov.cn",
                "url": "https://www.example.gov.cn/",
                "nav_registry": True,
                "navigation_segments": ["jggk", "zcjd"],
                "check_robots": False,
            }
        )
        self.assertFalse(collector._link_allowed("https://www.example.gov.cn/jggk/index.html"))

    def test_real_column_names_are_not_treated_as_navigation(self) -> None:
        # tzgg/gsgg are the actual parent columns of announcement URLs on
        # several ministry sites, so filtering them would drop valid details.
        collector = self._list_only_collector("https://zzys.example.gov.cn/tzgg/")
        for url in (
            "https://zzys.example.gov.cn/tzgg/202609/t20260925_1.html",
            "https://example.gov.cn/gsgg/2026-09/25/c123.html",
        ):
            with self.subTest(url=url):
                self.assertFalse(collector._navigation_like(url))
                self.assertTrue(collector._link_allowed(url))

    def test_site_chrome_is_still_filtered(self) -> None:
        collector = self._list_only_collector("https://www.example.gov.cn/index.html")
        for url in (
            "https://www.example.gov.cn/zwgk/index.html",
            "https://www.example.gov.cn/xxgkml/",
            "https://www.example.gov.cn/lxwm/index.shtml",
            "https://www.example.gov.cn/search/",
        ):
            with self.subTest(url=url):
                self.assertTrue(collector._navigation_like(url))

    def test_section_landing_titles_are_rejected(self) -> None:
        collector = self._list_only_collector("https://www.example.gov.cn/index.html")
        for title in (
            "机构沿革 - 四川省商务厅",
            "四川省商务厅首页 - 四川省商务厅",
            "机关简介 - 四川省商务厅",
            "机构职能 - 四川省人民政府网站",
            "网站地图",
            "2025年度政府网站工作年度报表",
            "四川省教育厅",
        ):
            with self.subTest(title=title):
                self.assertTrue(collector._is_section_page(title))

    def test_real_announcements_are_not_rejected(self) -> None:
        collector = self._list_only_collector("https://www.example.gov.cn/index.html")
        for title in (
            "关于印发《文化产业发展“十五五”规划》的通知",
            "省市场监管局关于开展产品质量监督抽查的通告",
            "关于开展2026年高职单招报名工作的通知",
            "生态环境部",
            "工作动态",
            "信息公开",
        ):
            with self.subTest(title=title):
                self.assertFalse(collector._is_section_page(title))

    def test_static_failure_falls_back_to_rendered_page_when_enabled(self) -> None:
        collector = self._list_only_collector("https://www.example.gov.cn/list.shtml")
        collector.source["playwright_fallback"] = True
        calls: list[str] = []

        def failing() -> tuple[str, str]:
            calls.append("static")
            raise RuntimeError("HTTP 412 for https://www.example.gov.cn/list.shtml")

        collector._list_html = failing  # type: ignore[method-assign]
        collector._render_with_playwright = lambda url: (  # type: ignore[method-assign]
            calls.append(f"render:{url}") or '<html><body><a href="/art/1.html">公告</a></body></html>'
        )
        links = collector.fetch_list()
        self.assertEqual(links, ["https://www.example.gov.cn/art/1.html"])
        self.assertIn("render:https://www.example.gov.cn/list.shtml", calls)
        self.assertTrue(any("static list fetch failed" in item["error"] for item in collector.errors))

    def test_static_failure_is_raised_when_fallback_disabled(self) -> None:
        collector = self._list_only_collector("https://www.example.gov.cn/list.shtml")

        def failing() -> tuple[str, str]:
            raise RuntimeError("HTTP 412")

        collector._list_html = failing  # type: ignore[method-assign]
        with self.assertRaises(RuntimeError):
            collector.fetch_list()

    def test_detail_page_falls_back_to_rendered_page_on_static_error(self) -> None:
        collector = self._list_only_collector("https://www.example.gov.cn/list.shtml")
        collector.source["playwright_fallback"] = True
        collector._request = lambda url: (_ for _ in ()).throw(RuntimeError("HTTP 412"))  # type: ignore[method-assign]
        collector._render_with_playwright = lambda url: (  # type: ignore[method-assign]
            '<html><head><title>锦江区公告</title></head><body><p>正文内容足够长用于通过质量检查。</p></body></html>'
        )
        record = collector.fetch_detail("https://www.example.gov.cn/art/1.html")
        self.assertEqual(record["title"], "锦江区公告")
        self.assertTrue(any("static fetch failed" in item["error"] for item in collector.errors))

    def test_body_extraction_dependencies_are_actually_importable(self) -> None:
        """正文抽取的可选依赖必须真的能用，不许静默退化。

        trafilatura 曾因 lxml.html.clean 拆包而 import 失败，base.py 的
        except ImportError 把它吞成 None，于是正文抽取退化为
        soup.get_text()——整页文字含导航/页眉/页脚。实测后果是整库 10% 的
        记录被站点框架污染（门户页 12%、摘要超 600 字 22%、归类落到"其他" 95%），
        而日志里一行提示都没有。这个测试就是为了让那种退化立刻变红。
        """
        health = optional_deps_health()
        self.assertEqual(health, {}, f"正文抽取依赖降级中：{health}")

    def test_content_excludes_site_chrome_when_trafilatura_available(self) -> None:
        """抽出来的正文不能把导航/页脚一起吞进去。

        已知边界（实测）：正文短到 2 段（约 115 字）时 trafilatura 判断不出主内容
        区，会把整页留下；4 段起就干净了。所以这里用 6 段 realistic 正文，
        短记录的框架文字残留属于已知限制，不在这里假装解决。
        """
        paragraphs = "".join(
            f"<p>第{i}段正文内容，说明某项政策的执行要求、申报方式与截止时间，"
            f"供符合条件的企业和個人参照执行。</p>".replace("個人", "个人")
            for i in range(1, 7)
        )
        html = (
            "<html><head><title>通知</title></head><body>"
            "<div class='nav'>首页 字号：打印 搜索 上一篇 下一篇</div>"
            f"<div class='art'><h1>关于开展申报工作的通知</h1>{paragraphs}</div>"
            "<div class='foot'>版权所有 备案号 浏览次数</div>"
            "</body></html>"
        )
        collector = self._list_only_collector("https://www.example.gov.cn/list.shtml")
        parsed = collector.parse(
            FakeResponse(html, "https://www.example.gov.cn/art/1.html"),
            "https://www.example.gov.cn/art/1.html",
        )
        content = parsed.get("content") or ""
        self.assertIn("第1段正文内容", content)
        for junk in ("字号", "上一篇", "版权所有", "浏览次数"):
            self.assertNotIn(junk, content, f"正文里混进了站点框架文字：{junk}")


if __name__ == "__main__":
    unittest.main()
