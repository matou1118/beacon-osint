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


class PageUrlTests(unittest.TestCase):
    """保留按 URL 猜的能力：财政部/教育部那批是静态 index_N.shtml，没有翻页控件。"""

    def _p(self, url: str, index: int):
        c = collector()
        c.list_url = url
        return c._page_url(index)

    def test_index_shtml(self):
        self.assertEqual(self._p("https://x.gov.cn/a/index.shtml", 2),
                         "https://x.gov.cn/a/index_2.shtml")

    def test_query_page(self):
        self.assertEqual(self._p("https://x.gov.cn/a/list?page=1", 3),
                         "https://x.gov.cn/a/list?page=3")

    def test_zero_based_family_left_to_renderer(self):
        """list_1.shtml 已经带序号，再套会套错，交给渲染器。"""
        self.assertIsNone(self._p("https://x.gov.cn/a/list_1.shtml", 2))

    def test_placeholder_is_actually_substituted(self):
        # re.sub(pattern, template, text) returned a literal "index_{n}.shtml":
        # the {n} was never substituted, so archive_pages never worked for the
        # list.shtml / index.shtml family. Measured as "page 2 has 0 rows".
        got = self._p("https://x.gov.cn/a/index.shtml", 2)
        self.assertNotIn("{n}", got)
        self.assertNotIn("{", got)

    def test_list_shtml(self):
        self.assertEqual(self._p("https://x.gov.cn/a/list.shtml", 3),
                         "https://x.gov.cn/a/list_3.shtml")

    def test_col_index_html_guesses_as_fallback(self):
        # Measured 404 on Guangdong/Hebei, so the real next-link is tried
        # first and this guess is only a fallback. Pins current behaviour.
        self.assertEqual(self._p("https://x.gov.cn/col/col284/index.html", 2),
                         "https://x.gov.cn/col/col284/index_2.shtml")


if __name__ == "__main__":
    unittest.main()
