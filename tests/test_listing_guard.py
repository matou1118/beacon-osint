"""栏目/列表页守卫：URL、标题、正文形态三条判据。

守卫是照着**实际落库的脏数据**调出来的，所以测试固件就是那些页面的原文
（tests/fixtures/listing_cases.py，由 scripts/_dump_fixtures.py 从库里导出）。
手抄原文会引入编码漂移，而且这台机器的控制台会把中文显示成乱码。

调这个守卫时踩过的两个坑，都写进用例注释里了：
  1. 第一版按"标题必须是已知栏目名"来判，结果「通知公告」根本不在
     SECTION_TITLE_MARKERS 里，判据自己把自己拦了，两条真栏目页漏网。
  2. 第二版按"正文里日期 >= 3 个"来判，误伤了「浙江省成品油价格调整」
     （正文里有 2026年9月4日/9月1日/9月8日 三处）和「创业培训讲师选评公示」
     （引用多个文号日期）。真公告本来就会有多个完整日期。
     现在判据是"日期后面紧跟一段像标题的文字"。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collectors.html_list import HtmlListCollector  # noqa: E402
from tests.fixtures.listing_cases import CASES  # noqa: E402


def collector() -> HtmlListCollector:
    c = HtmlListCollector.__new__(HtmlListCollector)
    c.source = {"url": "https://x.gov.cn/col/col1/index.html"}
    c.list_url = c.source["url"]
    return c


class NonDocUrlTests(unittest.TestCase):
    """/wza/ 这类路径必须在 _link_allowed 里拦，不能等拿到标题再判。"""

    def test_wza_path(self):
        c = collector()
        self.assertTrue(c.NON_DOC_URLS.search("/wza/"))
        self.assertTrue(c.NON_DOC_URLS.search("/wza/index.html"))

    def test_ordinary_article_path_is_untouched(self):
        c = collector()
        for path in ("/col/col123/2024/05/06/abc.shtml",
                     "/tzgg/20240506/abc_1.shtml"):
            self.assertFalse(c.NON_DOC_URLS.search(path), path)


class NonDocTitleTests(unittest.TestCase):
    def test_copyright_and_disclaimer(self):
        c = collector()
        for title in ("四川省农业农村厅官网版权与免责声明", "网站声明", "隐私政策"):
            self.assertTrue(c._is_section_page(title), title)

    def test_real_notices_survive(self):
        """契约：短标题的真公告必须放行。"""
        c = collector()
        for title in ("山东省人民政府关于印发《十五五规划》的通知",
                      "关于开展2026年度申报工作的通知",
                      "锦江区公告", "工作动态", "信息公开"):
            self.assertFalse(c._is_section_page(title), title)


class ListingGuardTests(unittest.TestCase):
    def test_real_stored_pages(self):
        c = collector()
        for rid, expect, title, body in CASES:
            with self.subTest(id=rid, note=title):
                self.assertEqual(c._looks_like_listing(title, body), expect,
                                 f"id={rid} {title}")

    def test_listing_count_needs_three_entries(self):
        """只出现一两个"日期+标题"不足以判定，正文可能只是提到了日期。"""
        c = collector()
        body = "2026-09-21 关于2026年度项目拟立项项目的公示内容说明"
        self.assertFalse(c._looks_like_listing("通知公告", body))

    def test_announce_shaped_title_is_always_kept(self):
        """标题是公告形状就放行，不管正文里有几个日期。"""
        c = collector()
        body = ("2026-09-21 关于甲事项的公示 2026-09-18 关于乙事项的公告 "
                "2026-09-15 关于丙事项的通告 2026-09-10 关于丁事项的通知")
        self.assertFalse(c._looks_like_listing("关于2026年度申报工作的通知", body))

    def test_long_title_is_never_a_listing(self):
        c = collector()
        body = "2026-09-21 甲 2026-09-18 乙 2026-09-15 丙 2026-09-10 丁"
        self.assertFalse(c._looks_like_listing("关于开展二〇二六年度某某工作的通知", body))


if __name__ == "__main__":
    unittest.main()
