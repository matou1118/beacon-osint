from __future__ import annotations

import unittest

from extractors.clean_summarize import (
    dedupe_sentences,
    score_sentence,
    strip_chrome,
    summarize,
)

CHROME_TAIL = (
    " 附件下载: 附件1：投标书.docx 相关文章: 发布日期: 2026年09月22日 分享到微信朋友圈 "
    "【 大 中 小 】 【打印此页】 【关闭窗口】 网站地图 联系我们 "
    "主办单位：中华人民共和国财政部 网站标识码：bm14000001 京ICP备05002860号 技术支持：财政部信息网络中心"
)
BODY = (
    "现就本次发行工作有关事宜通知如下：一、债券要素。本期国债竞争性招标面值总额400亿元。"
    "本期国债自2026年9月24日开始计息，于2026年12月24日按面值偿还。"
    "申报主体为商业银行，申报截止时间为2026年9月30日。特此公告。"
)


class CleanTests(unittest.TestCase):
    def test_removes_footer_boilerplate(self) -> None:
        cleaned = strip_chrome(BODY + CHROME_TAIL)
        for marker in ("打印此页", "网站地图", "ICP备", "分享到微信", "主办单位", "技术支持", "相关文章"):
            self.assertNotIn(marker, cleaned)

    def test_keeps_the_article_body(self) -> None:
        cleaned = strip_chrome(BODY + CHROME_TAIL)
        self.assertIn("面值总额400亿元", cleaned)
        self.assertIn("2026年12月24日", cleaned)

    def test_keeps_the_download_block(self) -> None:
        # Attachment names tell the reader what to file, so they stay.
        cleaned = strip_chrome(BODY + CHROME_TAIL)
        self.assertIn("投标书.docx", cleaned)

    def test_drops_breadcrumb_prefix(self) -> None:
        cleaned = strip_chrome("当前位置：首页 > 通知公告 " + BODY)
        self.assertNotIn("当前位置", cleaned)
        self.assertIn("面值总额400亿元", cleaned)

    def test_handles_spaced_breadcrumb_marker(self) -> None:
        cleaned = strip_chrome("当前位置 : 首页 > 政务公开 " + BODY)
        self.assertNotIn("当前位置", cleaned)

    def test_breadcrumb_prefix_still_trims_the_footer(self) -> None:
        # Regression: an early "continue" skipped footer trimming entirely, so
        # every page starting with a breadcrumb kept all of its chrome.
        cleaned = strip_chrome("当前位置：首页 > 通知公告 " + BODY + CHROME_TAIL)
        self.assertNotIn("打印此页", cleaned)
        self.assertNotIn("网站地图", cleaned)

    def test_dedupe_removes_repeats(self) -> None:
        sentence = "这是一句会重复出现的中文句子。"
        self.assertEqual(dedupe_sentences(sentence + sentence), [sentence])

    def test_dedupe_drops_fragments(self) -> None:
        self.assertEqual(dedupe_sentences("短。"), [])
class SummarizeTests(unittest.TestCase):
    def test_summary_uses_source_sentences_verbatim(self) -> None:
        result = summarize(BODY + CHROME_TAIL)
        for sentence in result["key_points"]:
            self.assertIn(sentence.replace(" ", ""), (BODY + CHROME_TAIL).replace(" ", ""))

    def test_summary_prefers_actionable_sentences(self) -> None:
        result = summarize(BODY + CHROME_TAIL)
        self.assertIn("400亿元", result["summary"])
        self.assertIn("2026年9月30日", result["summary"])

    def test_tags_reflect_detected_signals(self) -> None:
        result = summarize(BODY + CHROME_TAIL)
        self.assertIn("金额", result["tags"])
        self.assertIn("生效", result["tags"])
        self.assertIn("申报", result["tags"])
        self.assertIn("对象", result["tags"])

    def test_empty_input_is_safe(self) -> None:
        result = summarize("")
        self.assertEqual(result["summary"], "")
        self.assertEqual(result["key_points"], [])

    def test_deterministic(self) -> None:
        self.assertEqual(summarize(BODY), summarize(BODY))

    def test_shrinks_the_body(self) -> None:
        raw = BODY + CHROME_TAIL
        self.assertLess(len(summarize(raw)["summary"]), len(strip_chrome(raw)) + 1)

    def test_scoring_rewards_earlier_sentences(self) -> None:
        self.assertGreater(score_sentence("自2026年9月24日开始计息。", 0, 5), score_sentence("特此公告。", 4, 5))


class FontSwitcherTests(unittest.TestCase):
    """The switcher is a header control on most gov pages.

    Trimming it unconditionally as a footer marker cut an 807-char article to 37
    chars on 81 of 245 stored pages. Position decides which side survives.
    """

    BODY_TEXT = "为加强互联网经营农药的监督管理，保障农产品质量安全，拟出台负面清单。现将有关事项通知如下。"

    def test_header_switcher_keeps_what_follows(self) -> None:
        raw = "机构职能 工作动态 当前位置：首页 农业农村部关于征求意见的函 日期：2026-09-16 【字号： 大 中 小 】 打印本页 " + self.BODY_TEXT
        cleaned = strip_chrome(raw)
        self.assertIn("保障农产品质量安全", cleaned)
        self.assertNotIn("打印本页", cleaned)

    def test_reversed_order_switcher_is_recognised(self) -> None:
        raw = "新华网 > 时政 > 正文 来源：新华网 标题 字体： 小 中 大 分享到： " + self.BODY_TEXT
        cleaned = strip_chrome(raw)
        self.assertIn("保障农产品质量安全", cleaned)

    def test_footer_switcher_keeps_what_precedes(self) -> None:
        chrome = "网站地图 主办单位：某某部 版权所有"
        raw = self.BODY_TEXT + chrome + " 大 中 小 【打印此页】"
        cleaned = strip_chrome(raw)
        self.assertIn("保障农产品质量安全", cleaned)
        self.assertNotIn("大 中 小", cleaned)

    def test_header_footer_labels_do_not_cut_the_body(self) -> None:
        # Real pages repeat these labels in the header nav and the footer.
        # Cutting at the first occurrence wiped the body of 27 of 245 pages.
        body = FontSwitcherTests.BODY_TEXT
        raw = (
            "网站地图\n主办单位：某某部\n版权所有\n" + body + "\n"
            "网站地图\n主办单位：某某部\n技术支持：某某中心\n版权所有"
        )
        cleaned = strip_chrome(raw)
        self.assertIn("保障农产品质量安全", cleaned)
        self.assertNotIn("技术支持", cleaned)

    def test_related_reads_block_cuts_anywhere(self) -> None:
        raw = self.BODY_TEXT + " 相关文章: 第一篇 第二篇"
        cleaned = strip_chrome(raw)
        self.assertNotIn("相关文章", cleaned)
        self.assertIn("保障农产品质量安全", cleaned)


class RealStoreDefectTests(unittest.TestCase):
    """Defects measured on the real store, not invented. Each string is a verbatim
    (de-mangled) sample of what beacon.db actually held."""

    def test_mee_breadcrumb_prefix_is_dropped(self) -> None:
        # 25 of 25 mee.gov.cn rows opened with a channel chain glued to the title.
        raw = (
            "要闻动态>公示公告>公示>公示生态环境部关于2026年7月20日建设项目环境影响评价文件"
            "受理情况的公示2026-09-15 2026-09-15[大][中][小]"
            "根据建设项目环境影响评价审批程序的有关规定，我部受理了19家企业提交的环境影响"
            "评价文件。现将受理情况予以公示，公示期为2026年9月15日至9月23日。"
        )
        cleaned = strip_chrome(raw)
        self.assertNotIn("要闻动态", cleaned)
        self.assertNotIn("公示公告", cleaned)
        self.assertNotIn("[大]", cleaned)
        self.assertNotIn("2026-09-15 2026-09-15", cleaned)
        self.assertIn("根据建设项目环境影响评价审批程序的有关规定", cleaned)

    def test_gov_detail_page_metadata_run_removed_not_cut(self) -> None:
        # The metadata sits *between* title and body, so a cut-from rule would
        # delete the article.
        raw = (
            "文化和旅游部关于印发《非物质文化遗产保护传承“十五五”规划》的通知"
            "索引号：357A08-03-2026-0003文号：文旅非遗发〔2026〕6号"
            "各省、自治区、直辖市文化和旅游厅（局）：现将《规划》印发给你们。"
            "请遵照执行。资金来源：财政资金安排。"
        )
        cleaned = strip_chrome(raw)
        self.assertNotIn("索引号", cleaned)
        self.assertNotIn("文旅非特发", cleaned)
        self.assertIn("现将《规划》印发给你们", cleaned)
        # A real policy fact must survive.
        self.assertIn("资金来源：财政资金安排", cleaned)

    def test_desk_attribution_removed_but_funding_source_kept(self) -> None:
        raw = (
            "商务部等8部门关于印发《促进智能家居消费行动方案》的通知"
            "首页>政策发布>国内贸易管理来源：消费促进司类型：原创AI阅读"
            "各省、自治区、直辖市商务主管部门：现将《方案》印发给你们。"
            "项目资金来源为中央财政预算内投资。"
        )
        cleaned = strip_chrome(raw)
        self.assertNotIn("消费促进司", cleaned)
        self.assertNotIn("AI阅读", cleaned)
        self.assertIn("现将《方案》印发给你们", cleaned)
        self.assertIn("项目资金来源", cleaned)

    def test_metadata_label_cluster_yields_no_summary(self) -> None:
        # sc_mohurd_gov rows stored a metadata form instead of an article, so an
        # empty summary is the honest outcome.
        raw = (
            "政府信息公开＞政策文件＞其他文件索引号：主题分类：发布机构：索引号："
            "008283930/2026-公文种类：发布机构：008283930/2026-u37663300/2026-00260"
        )
        self.assertEqual(summarize(raw)["summary"], "")

    def test_nav_bar_body_yields_no_summary(self) -> None:
        # cnenergy/cena rows stored the whole navigation; a nav bar outranks real
        # sentences on length and position, so it used to become the summary.
        raw = (
            "八月全社会用电量再破万亿广告人民日报主管《中国能源报》社有限公司主办"
            "网站地图联系我们首页即时新闻能源要闻焦点关注能源评论能源党建热点专题"
            "生态环保人事动态能源城市环球视野产业聚焦电网电力新能源油气"
        )
        result = summarize(raw)
        self.assertEqual(result["summary"], "")
        self.assertEqual(result.get("drop_reason"), "nav_chrome")

    def test_font_switch_residue_removed_anywhere(self) -> None:
        self.assertNotIn("[中]", strip_chrome("[中] [小] " + FontSwitcherTests.BODY_TEXT))
        self.assertNotIn("选择字体", strip_chrome("选择字体：[大][中][小]" + FontSwitcherTests.BODY_TEXT))

    def test_real_notice_keeps_its_facts(self) -> None:
        # The reason any of the above is allowed to run: a real notice must still
        # yield amount, deadline and applicant.
        result = summarize(BODY)
        self.assertTrue(result["summary"])
        for fact in ("400亿元", "2026年9月30日", "申报主体"):
            self.assertIn(fact, result["summary"])


if __name__ == "__main__":
    unittest.main()
