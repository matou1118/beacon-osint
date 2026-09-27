from __future__ import annotations

import unittest

from extractors.clean_summarize import dedupe_sentences, strip_chrome
from extractors.summary_refine import (
    COMPRESS_CEILING,
    COMPRESS_FLOOR,
    MAX_POINTS,
    MAX_SUMMARY_CHARS,
    audit,
    build_summary,
)

NAV = "首页 > 要闻动态 > 公示公告 > 公示区 > 公示"
TOOLBAR = "[打印]字号：[大][中][小]"
ARTICLE = (
    "为加强互联网经营农药的监督管理，保障农产品质量安全，根据《农药管理条例》有关规定，"
    "拟研究出台《互联网经营农药负面清单（2026版）》。现将有关事项通知如下。"
    "各省、自治区、直辖市农业农村厅负责组织辖区内企业申报，申报截止时间为2026年9月30日。"
    "支持标准为500万元，申报主体为农药经营企业。特此公告。"
)
# Long enough for the compression rates to be scored at all.
LONG_BODY = ARTICLE * 12


class SplitTests(unittest.TestCase):
    def test_newline_separated_blocks_become_sentences(self) -> None:
        # Gov pages lay out with newlines, not full stops. Splitting on 。 alone
        # left 2k-character blocks that swallowed the notice.
        raw = "现将事项通知如下事项说明。\n第一项内容说明如下事项。\n第二项内容说明如下事项。"
        self.assertGreaterEqual(len(dedupe_sentences(raw)), 2)

    def test_overlong_block_is_split_on_commas(self) -> None:
        from extractors.clean_summarize import MAX_SENTENCE

        block = "电子邮箱：pmd@agri.gov.cn" + "，通讯地址：北京市朝阳区农展馆南里11号" * 30
        pieces = dedupe_sentences(block)
        self.assertGreater(len(pieces), 1)
        self.assertTrue(all(len(p) <= MAX_SENTENCE for p in pieces))

    def test_enumeration_commas_do_not_split_a_clause(self) -> None:
        # "各省、自治区、直辖市" is one clause, not three sentences. Splitting on
        # 、 truncated policy clauses mid-word and lost the deadline.
        clause = "各省、自治区、直辖市农业农村厅负责组织辖区内企业申报，申报截止时间为2026年9月30日。"
        self.assertEqual(len(dedupe_sentences(clause)), 1)


class RefineTests(unittest.TestCase):
    def test_removes_breadcrumb_from_summary(self) -> None:
        result = build_summary(NAV + " " + TOOLBAR + " " + ARTICLE, "关于农药负面清单的函")
        self.assertNotIn("首页", result["summary"])
        self.assertNotIn("打印", result["summary"])

    def test_keeps_article_verbatim(self) -> None:
        result = build_summary(ARTICLE, "关于农药负面清单的函")
        self.assertIn("保障农产品质量安全", result["summary"])
        self.assertIn("500万元", result["summary"])
        self.assertIn("2026年9月30日", result["summary"])

    def test_never_invents_text_absent_from_source(self) -> None:
        result = build_summary(ARTICLE, "标题")
        for sentence in result["key_points"]:
            self.assertIn(sentence, ARTICLE)

    def test_compression_stays_in_band(self) -> None:
        body = ARTICLE * 12
        result = build_summary(body, "标题")
        ratio = len(result["summary"]) / max(1, len(result["clean_text"]))
        self.assertLessEqual(ratio, COMPRESS_CEILING * 1.15)

    def test_completes_a_truncated_tail(self) -> None:
        result = build_summary("现将加强互联网经营农药监督管理有关事项通知如下；", "标题")
        self.assertTrue(result["summary"].endswith("。"))
        self.assertIn("completed_truncated_tail", result["repairs"])

    def test_roster_ending_is_left_alone(self) -> None:
        roster = (
            "参与单位包括中国农业行业协会、中国国际商会农业行业商会、"
            "中国国际减贫中心等多家机构共同参与本次申报工作"
        )
        result = build_summary(roster, "标题")
        self.assertNotIn("completed_truncated_tail", result["repairs"])

    def test_short_input_is_safe(self) -> None:
        result = build_summary("", "标题")
        self.assertEqual(result["summary"], "")
        self.assertIn("no_usable_sentence", result["repairs"])

    def test_is_deterministic(self) -> None:
        self.assertEqual(build_summary(ARTICLE, "t")["summary"], build_summary(ARTICLE, "t")["summary"])


class AuditTests(unittest.TestCase):
    def test_flags_known_defects(self) -> None:
        # The compression rates are only scored on bodies long enough for a
        # summary to be a summary, so the under-compressed fixture needs a long one.
        rows = [
            {"content": ARTICLE * 4, "summary": "首页 > 公示 " + ARTICLE},
            {"content": LONG_BODY, "summary": "摘要"},
        ]
        report = audit(rows)
        self.assertEqual(report["counts"]["breadcrumb"], 1)
        self.assertEqual(report["counts"]["compression_low"], 1)

    def test_short_bodies_are_not_scored_for_compression(self) -> None:
        # A 400-char notice with a 200-char abstract has ratio 0.5 and is a short
        # document, not a missing summary. Blanking it would leave the user with
        # nothing, so the row is reported as skipped rather than scored.
        short = "正文内容。" * 80
        summary = "正文内容。" * 40
        self.assertGreater(len(summary) / len(short), COMPRESS_CEILING)
        report = audit([{"content": short, "summary": summary}])
        self.assertEqual(report["counts"]["compression_high"], 0)
        self.assertEqual(report["compression_scored"], 0)
        self.assertEqual(report["compression_skipped_short_body"], 1)

    def test_clean_summaries_report_zero_defects(self) -> None:
        rows = []
        for raw in (ARTICLE, NAV + " " + TOOLBAR + " " + ARTICLE):
            built = build_summary(raw, "t")
            rows.append({"content": built["clean_text"], "summary": built["summary"]})
        report = audit(rows)
        self.assertEqual(report["counts"]["breadcrumb"], 0)
        self.assertEqual(report["counts"]["toolbar"], 0)
        self.assertEqual(report["counts"]["no_terminator"], 0)

    def test_hard_char_cap_binds_even_with_only_two_points(self) -> None:
        """恰好 2 条要点时字数上限也必须生效。

        踩过的坑：硬上限的触发条件写的是 `len(points) > 2`，而**恰好 2 条时
        上限形同虚设**——那恰恰是最常见的超长形态，因为 MAX_SENTENCE 允许单句
        到 400 字，2 条就是 800。线上 17 条超长摘要全部是 2 条要点、
        最长单条 346-400 字，一条例外都没有。
        """
        # 直接用真实形态：政务电话表正文整段没有句号，dedupe_sentences 的
        # _split_long 只能按逗号切，产出接近 MAX_SENTENCE 的长块。造样本时
        # 用重复填充会被 dedupe_sentences 去重，句子互不相同才切得开。
        def phones(city: str, start: int) -> str:
            return "，".join(f"{city}{i:02d}：028-{i:08d}" for i in range(start, start + 40)) + "。"

        body = ("四川省各市县农民工服务电话一览表 " + phones("成都", 1)
                + phones("自贡", 100) + phones("攀枝花", 200))
        result = build_summary(body, "农民工服务电话一览表")
        self.assertGreater(len(body), MAX_SUMMARY_CHARS, "样本本身要够长")
        self.assertLessEqual(len(result["summary"]), MAX_SUMMARY_CHARS)
        self.assertIn("capped_summary_chars", result.get("repairs") or [])

    def test_huge_body_cannot_drive_the_summary_past_the_caps(self) -> None:
        """超长正文不能把摘要撑破上限。

        线上实测：id=2203 的 content 有 187,122 字（一个列表页被当公告存进来了），
        压缩率下限补句的循环只按"正文的 10%"和"句子用完"两个条件停，既不看
        MAX_POINTS 也不看 MAX_SUMMARY_CHARS，于是摘要被补到 18,993 字、49 条要点。
        515/1576 条（33%）超限都是这一个原因，而且 MAX_SUMMARY_CHARS 这个常量
        当时定义了却全代码没有任何地方引用。
        """
        # 用短句堆出超长正文，模拟列表页灌进 content 的情形
        body = "。".join(f"第{i}条关于申报事项的说明材料请按规定报送" for i in range(1, 4000))
        result = build_summary(body + "。", "测试标题")
        self.assertLessEqual(len(result["summary"]), MAX_SUMMARY_CHARS,
                             f"摘要 {len(result['summary'])} 字，超出上限")
        self.assertGreater(len(result["summary"]), 0)

    def test_compression_floor_still_applies_to_short_notices(self) -> None:
        """上限不能把"别压太狠"这个意图一起弄丢：下限仍要能补句。

        这是上面那个取舍的正向用例。N=80 时初始 8 条要点只有 448 字，够不到
        10% 下限 455，于是补到 9 条、504 字——点数被突破，但这是有意的：
        点数是软目标，MAX_SUMMARY_CHARS 才是硬上限。
        """
        body = "。".join(
            f"第{i}条申报要求请各申报单位于本月底前将纸质材料报送至受理窗口，"
            f"并电话确认材料清单与份数，逾期不再受理，特此通知"
            for i in range(1, 81)
        )
        result = build_summary(body + "。", "测试标题")
        floor = min(int(len(body) * COMPRESS_FLOOR), MAX_SUMMARY_CHARS)
        self.assertIn("raised_compression", result.get("repairs") or [],
                      "压缩率下限没有触发")
        self.assertGreaterEqual(len(result["summary"]), floor)
        self.assertLessEqual(len(result["summary"]), MAX_SUMMARY_CHARS)

    def test_hard_char_cap_binds_even_when_points_are_allowed_to_grow(self) -> None:
        """点数放开后，字数硬上限必须仍然守住。

        这两条规则是一对取舍：下限修正会突破 MAX_POINTS，所以唯一能兜住失控的是
        MAX_SUMMARY_CHARS。正文 13 万字时 need 被 min() 压到 600，循环必须在那里停。
        """
        body = "。".join(
            f"第{i}条关于申报事项的说明材料请按规定报送至受理窗口并电话确认" for i in range(1, 4000)
        )
        result = build_summary(body + "。", "测试标题")
        self.assertLessEqual(len(result["summary"]), MAX_SUMMARY_CHARS)
        self.assertGreater(len(result["summary"]), 0)


if __name__ == "__main__":
    unittest.main()
