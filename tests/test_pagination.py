"""翻页链接识别与静态翻页的回归测试。

背景：跨年对比要求能拿到往年页面，于是去查各源能不能翻页。三轮实测的结论是
"按 URL 猜页码"这条路子整体不成立：
  - 江苏/浙江/山东的 col/col284/index.html 加 ?pageNum=2 或 ?page=2，返回的
    链接集合与第 1 页**完全相同**（站点不认这个参数）
  - 广东/河北的 index_1.shtml 直接 404
  - 安徽的 index_1.shtml 返回 4 条链接，且是第 1 页的子集
所以改成跟着页面上真实的"下一页"链接走。这些用例固定住该识别的三种写法，以及
必须被拒掉的几种（javascript: 伪链接、防环、重复内容）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collectors.html_list import HtmlListCollector  # noqa: E402

BASE = "https://fzggw.jiangsu.gov.cn/col/col284/index.html"


def collector() -> HtmlListCollector:
    c = HtmlListCollector.__new__(HtmlListCollector)
    c.source = {"url": BASE, "archive_pages": 6}
    c.list_url = BASE
    return c


class NextLinkTests(unittest.TestCase):
    def test_recognises_chinese_next_text(self):
        html = ('<div class="pager"><a href="/col/col284/index.html">首页</a>'
                '<a href="index_1.html">下一页</a></div>')
        got = collector()._next_page_link(html, BASE, set())
        self.assertEqual(got, "https://fzggw.jiangsu.gov.cn/col/col284/index_1.html")

    def test_recognises_title_attribute(self):
        """不少站点翻页控件是个 > 或 »，没有"下一页"三个字。"""
        html = '<a href="index_2.shtml" title="下一页">&gt;</a>'
        got = collector()._next_page_link(html, BASE, set())
        self.assertEqual(got, "https://fzggw.jiangsu.gov.cn/col/col284/index_2.shtml")

    def test_recognises_index_n_href_without_next_text(self):
        html = '<a href="index_1.html">2</a><a href="index_2.html">3</a>'
        got = collector()._next_page_link(html, BASE, set())
        self.assertIn("index_1.html", got)

    def test_recognises_page_query_href(self):
        html = '<a href="/col/col284/index.html?pageNum=2">2</a>'
        got = collector()._next_page_link(html, BASE, set())
        self.assertIn("pageNum=2", got)

    def test_ignores_javascript_pseudo_links(self):
        """政务站极常见：onclick 里换页，href 是 javascript:void(0)。"""
        html = ('<a href="javascript:void(0)" onclick="go(2)">下一页</a>'
                '<a href="index_1.html">2</a>')
        got = collector()._next_page_link(html, BASE, set())
        self.assertNotIn("javascript", got or "")
        self.assertIn("index_1.html", got)

    def test_ignores_anchor_and_mail(self):
        html = '<a href="#top">下一页</a><a href="mailto:a@b.c">联系我们</a>'
        self.assertIsNone(collector()._next_page_link(html, BASE, set()))

    def test_seen_set_prevents_loops(self):
        """有些站最后一页的"下一页"仍指向自己，跟下去会死循环。"""
        html = '<a href="index_1.html">下一页</a>'
        got = collector()._next_page_link(
            html, BASE, {"https://fzggw.jiangsu.gov.cn/col/col284/index_1.html"})
        self.assertIsNone(got)

    def test_does_not_return_the_current_page(self):
        html = f'<a href="{BASE}">下一页</a>'
        self.assertIsNone(collector()._next_page_link(html, BASE, set()))


    def test_pseudo_protocol_hrefs_rejected_whatever_the_spelling(self):
        # ★ 伪协议链接按**协议白名单**拒，不能枚举拼写。
        #
        # 实测 2026-10-02：mem.gov.cn 的翻页控件里写的是
        #     href="javascirpt:void(0)"      <- 少一个 e
        # 原来的 startswith(("javascript:", "#", "mailto:")) 漏掉它，于是把
        # 「点击无效」当成真实 URL 去请求，翻页永远停在第 1 页，现象却只是
        # 「源没产出」——完全看不出是这里错了。
        c = collector()
        base = "https://www.mem.gov.cn/xw/"
        for href in ("javascirpt:void(0)", "javascript:void(0)",
                     "JavaScript:;", "vbscript:msgbox",
                     "mailto:a@b.gov.cn", "#top", "tel:010-12345678"):
            html = '<a href="%s">下一页</a>' % href
            self.assertIsNone(
                c._next_page_link(html, base, set()),
                "伪协议 %r 被当成了真实翻页链接" % href)

    def test_real_next_link_still_recognised_after_the_fix(self):
        # 白名单不能把真链接也拦掉——改这块时最容易出的回归。
        c = collector()
        base = "https://x.gov.cn/col/col1/index.html"
        cases = (
            '<a href="/col/col1/index_2.shtml">下一页</a>',
            '<a href="index_3.shtml">下页</a>',
            '<a href="/jpage/dataproxy.jsp?page=2&amp;colid=1">下一页</a>',
            '<a href="?pageNum=4">next</a>',
        )
        for html in cases:
            self.assertIsNotNone(
                c._next_page_link(html, base, set()),
                "真翻页链接被误拒：%s" % html)


if __name__ == "__main__":
    unittest.main()