"""政府工作报告解析与指标抽取的回归测试。

每个用例都对应一个真实踩过的坑。这些 bug 有一个共同特征：单条路径看起来
正常，只有把真实报告喂进去才暴露，所以必须有测试钉住。

  1. 标题段落被长度下限丢掉    -> 一份 10 个字的"一、2025年工作回顾"被丢，
                                 上海 2026 报告只分出 2 节
  2. &emsp; 实体未解码        -> 行首变成 "&emsp;&emsp;一、2024年工作回顾"，
                                 锚定 ^\\s* 的标题正则一个都匹配不到，
                                 上海 2025/2024 两份分出 0 节
  3. 养老床位被当成医疗床位    -> 数值对、指标错，比抽不到更糟
  4. is_target 被同句措辞污染  -> 「一般公共预算收入达到8500亿元…失业率平均为
                                 4.2%」里的失业率被"达到"拖成目标值
  5. 个位数百分比被当噪声丢弃  -> "5%"/"2%" 全丢，pattern 命中但抽取返回空
  6. 人均 GDP 被重复计为 GDP   -> 同一个数既进 gdp 又进 per_capita_gdp
  7. 年份不能从 URL 取        -> 武汉 2016-2018 三份的 URL 都是 /202003/
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from extractors.work_report import (  # noqa: E402
    extract_body,
    normalize,
    parse_report_year,
    split_sections,
)
from extractors.work_report_metrics import (  # noqa: E402
    extract_metrics,
    extract_topic_hits,
)


class ExtractBodyTests(unittest.TestCase):
    def test_short_heading_paragraphs_are_kept(self) -> None:
        """"一、2025年工作回顾"只有 10 字，早先的 >=12 下限把它丢了。"""
        html = (
            "<p>&emsp;&emsp;一、2025年工作回顾</p>"
            "<p>过去一年，全市生产总值达到5.67万亿元、增长5.4%。</p>"
            "<p>三、2026年主要任务</p>"
            "<p>城镇新增就业60万人以上，推动经济高质量发展。</p>"
        )
        text = normalize(extract_body(html))
        self.assertIn("一、2025年工作回顾", text)
        self.assertIn("三、2026年主要任务", text)


class SectionSplitTests(unittest.TestCase):
    def test_four_sections_are_classified(self) -> None:
        body = "\n".join([
            "现在，我代表上海市人民政府，向大会报告工作。",
            "一、2025年工作回顾",
            "过去一年，全市生产总值达到5.67万亿元。",
            "二、关于《五年规划纲要（草案）》的说明",
            "规划纲要提出未来五年的主要目标。",
            "三、2026年主要任务",
            "城镇新增就业60万人以上。",
            "四、深入推进政府治理创新",
            "加强廉政建设。",
        ])
        preface, sections = split_sections(body)
        keys = [s["key"] for s in sections]
        self.assertEqual(keys, ["review", "plan", "target", "reform"])
        self.assertLess(len(preface), 100, "前言只应是套话，不该吞掉整节")

    def test_heading_inline_after_a_single_space(self) -> None:
        """标题与正文之间只有单个空格时也要认出来。

        上海 2007/2010、广州 2015 整份都是这个形态：
        「…向大会作政府工作报告，请予审议。 一、2006年工作回顾 2006年是…」
        早先要求标题在行首，这三份分出 0 节。
        """
        body = (
            "各位代表: 现在,我代表上海市人民政府,向大会作政府工作报告,请予审议。"
            " 一、2006年工作回顾 2006年是“十一五”时期的开局之年。"
            " 二、2007年工作的总体要求 今年是重要的一年。"
            " 三、经济发展 保持经济持续平稳健康发展。"
        )
        _preface, sections = split_sections(body)
        self.assertEqual(len(sections), 3, [s["heading"] for s in sections])
        self.assertEqual([s["key"] for s in sections], ["review", "target", "other"])

    def test_heading_inline_after_multiple_spaces(self) -> None:
        """标题与正文之间多个空格分隔（武汉 2013 整份都是这个形态）。"""
        body = (
            "现在,我代表市人民政府向大会报告工作,请予审议。     一、2012年工作回顾     "
            "2012年,是本届政府的开局之年。     二、2013年工作安排     今年是关键之年。"
        )
        _preface, sections = split_sections(body)
        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0]["key"], "review")
        self.assertIn("本届政府", sections[0]["text"])

    def test_long_heading_with_book_title_marks(self) -> None:
        """标题名可长至 45 字：『二、关于《…第十五个五年规划纲要(草案)》的说明』。"""
        body = (
            "一、2025年工作回顾 回顾内容。 "
            "二、关于《上海市国民经济和社会发展第十五个五年规划纲要(草案)》的说明 说明内容。 "
            "三、2026年主要任务 任务内容。"
        )
        _preface, sections = split_sections(body)
        self.assertEqual(len(sections), 3)
        self.assertEqual(sections[1]["key"], "plan")


class ExtractBodyFooterTests(unittest.TestCase):
    def test_page_footer_is_dropped(self) -> None:
        """广州 2015 那份把版权、网站标识码、ICP 备案号混在正文段落里。"""
        html = (
            "<p>各位代表: 现在,我代表广州市人民政府向大会作工作报告。一、2014年工作回顾 内容。</p>"
            "<p>版权所有:广州市人民政府</p>"
            "<p>网站标识码:4401000004</p>"
            "<p>ICP备案号:粤ICP备2022092331号-1</p>"
        )
        text = extract_body(html)
        for junk in ("版权所有", "网站标识码", "ICP备案号"):
            self.assertNotIn(junk, text)
        self.assertIn("一、2014年工作回顾", text)


class YearParseTests(unittest.TestCase):
    def test_year_comes_from_title_not_url(self) -> None:
        # 武汉 2016-2018 三份的 URL 年份段都是 /202003/
        url = "https://www.wuhan.gov.cn/zwgk/xxgk/ghjh/zfgzbg/202003/t20200316_970155.shtml"
        self.assertEqual(parse_report_year("武汉市2018年政府工作报告", url), 2018)
        self.assertEqual(parse_report_year("重庆市人民政府工作报告（2026年）", ""), 2026)
        self.assertEqual(
            parse_report_year("龚正市长在上海市第十六届人民代表大会第四次会议的政府工作报告（2026年）"),
            2026,
        )


    def test_work_suggestion_section_is_a_target_section(self) -> None:
        """「二、2014年工作建议」是年度目标，不是长期规划。

        早先把"建议"归给 plan，is_target 变 False，本年目标值被当成实际值。
        交叉验证一次报出 12 处同类冲突，全是"一、20XX年工作回顾"的实际值
        对上"二、20XX年工作建议"的目标值。
        """
        body = (
            "一、2013年工作回顾 城镇新增就业17.6万人,转移农村劳动力5.2万人。 "
            "二、2014年工作建议 城镇新增就业15万人,农村劳动力转移就业5.5万人。"
        )
        _preface, sections = split_sections(body)
        self.assertEqual([s["key"] for s in sections], ["review", "target"])
        review = extract_metrics(sections[0]["text"], region_code="x", year=2014,
                                 section_key=sections[0]["key"])
        target = extract_metrics(sections[1]["text"], region_code="x", year=2014,
                                 section_key=sections[1]["key"])
        self.assertFalse(review[0]["is_target"], "回顾段的实际值不是目标")
        self.assertTrue(target[0]["is_target"], "工作建议段的值是本年目标")

    def test_five_year_plan_draft_explanation_is_a_plan_section(self) -> None:
        """「关于《…五年规划纲要(草案)》的说明」仍应归 plan，不能被目标类词抢走。"""
        _preface, sections = split_sections(
            "一、2025年工作回顾 内容。 "
            "二、关于《上海市国民经济和社会发展第十五个五年规划纲要(草案)》的说明 内容。 "
            "三、2026年主要任务 内容。"
        )
        self.assertEqual([s["key"] for s in sections], ["review", "plan", "target"])

    def test_cumulative_and_multi_year_values_are_excluded(self) -> None:
        """累计值与未来目标不是本年值，进年度序列会造出假数据。

        交叉验证抓到的实例：
          武汉 2012「五年城镇新增就业72.4万人」—— 五年累计，被记成 2012 年
          广州 2007「到2011年,全市地区生产总值超过1万亿元」—— 2011 年目标
          广州 2022「地方一般公共预算收入累计8475亿元」—— 多年加总
        """
        for text in (
            "五年城镇新增就业72.4万人,扶持创业10.3万人",
            "到2011年,全市地区生产总值超过1万亿元,人均生产总值达到1.3万美元",
            "地方一般公共预算收入累计8475亿元,年均增长5.2%",
        ):
            with self.subTest(text=text):
                got = extract_metrics(text, region_code="x", year=2012)
                self.assertEqual(got, [], f"{text!r} 不应产出本年指标")

    def test_fiscal_revenue_requires_local_caliber(self) -> None:
        """全地区口径与市本级口径差 3.7 倍，不能当成同一指标。

        广州 2022 那份同时写「来源于广州地区的一般公共预算收入」与
        「地方一般公共预算收入」，宽松匹配会让交叉验证报出 100% 差异。
        """
        wide = extract_metrics("来源于广州地区的一般公共预算收入3000亿元", region_code="x", year=2022)
        self.assertEqual([m for m in wide if m["metric_key"] == "fiscal_revenue"], [])
        local = extract_metrics("地方一般公共预算收入8475亿元", region_code="x", year=2022)
        self.assertEqual([m["value"] for m in local if m["metric_key"] == "fiscal_revenue"], [8475.0])


    def test_target_section_recognised_from_content_not_title(self) -> None:
        """标题一个目标词都没有、但正文写着"主要预期目标"的段落仍是目标段。

        上海 2016 那份的「三、全力以赴做好"十三五"开局工作」：交叉验证报出
        4.1%（上一年实际）与 4.4%（本年目标）同格冲突，因为该段没被认成目标段。
        """
        body = (
            "一、2015年工作回顾 新增就业岗位59.7万个,城镇登记失业率控制在4.1%。 "
            "三、全力以赴做好“十三五”开局工作 "
            "今年经济社会发展主要预期目标:城镇登记失业率控制在4.4%以内。"
        )
        _preface, sections = split_sections(body)
        keys = [s["key"] for s in sections]
        self.assertEqual(keys, ["review", "target"])
        target = extract_metrics(sections[1]["text"], region_code="x", year=2016,
                                 section_key=sections[1]["key"])
        self.assertTrue(target[0]["is_target"])

    def test_review_section_is_never_reclassified_by_content(self) -> None:
        """回顾段即使提到"主要预期目标"也不能被改判——它可能是在回顾上年目标。"""
        body = (
            "一、2015年工作回顾 全年地区生产总值增长6.6%,主要预期目标均已实现。 "
            "三、2026年重点工作 地区生产总值增长5.5%左右。"
        )
        _preface, sections = split_sections(body)
        self.assertEqual(sections[0]["key"], "review")
        self.assertEqual(sections[1]["key"], "target")


class YearAttributionTests(unittest.TestCase):
    """回顾段的数值属于上年，按报告年份记会整体错位一年。"""

    def test_review_section_attributes_to_previous_year(self) -> None:
        got = extract_metrics("全市生产总值达到10297亿元,按可比价格计算比上年增长12%",
                              region_code="x", year=2007, section_key="review")
        gdp = [m for m in got if m["metric_key"] == "gdp"]
        self.assertTrue(gdp)
        self.assertEqual(gdp[0]["year"], 2006, "2007 报告回顾的是 2006 年")

    def test_target_section_keeps_report_year(self) -> None:
        got = extract_metrics("地区生产总值增长5%左右", region_code="x", year=2011,
                              section_key="target")
        self.assertEqual(got[0]["year"], 2011)

    def test_year_named_in_sentence_wins(self) -> None:
        """正文里点了年份就以它为准。

        武汉 2011 那份：「地区生产总值连续跨越3000亿元…2010年达到5515.8亿元」
        句内已经写明 2010 年，比"回顾段减一"更准。
        """
        got = extract_metrics("地区生产总值连续跨越3000亿元、4000亿元、5000亿元3个台阶,"
                              "2010年达到5515.8亿元,是2005年的2.4倍",
                              region_code="x", year=2011, section_key="review")
        gdp = [m for m in got if m["metric_key"] == "gdp"]
        self.assertTrue(gdp)
        self.assertEqual(gdp[0]["year"], 2010)

    def test_review_values_extend_the_series_one_year_back(self) -> None:
        """一份报告应覆盖两个年份：上年实际 + 本年目标。"""
        body = (
            "一、2013年工作回顾 城镇新增就业17.6万人,转移农村劳动力5.2万人。 "
            "二、2014年工作建议 城镇新增就业15万人。"
        )
        _preface, sections = split_sections(body)
        actual = extract_metrics(sections[0]["text"], region_code="x", year=2014,
                                 section_key=sections[0]["key"])
        target = extract_metrics(sections[1]["text"], region_code="x", year=2014,
                                 section_key=sections[1]["key"])
        self.assertEqual(actual[0]["year"], 2013, "回顾段归 2013")
        self.assertEqual(target[0]["year"], 2014, "目标段归 2014")


    def test_comparison_base_year_is_not_taken_as_the_value_year(self) -> None:
        """比较基准年不是数值本身的年份。

        上海 2007：「全市生产总值达到10297亿元,按可比价格计算比上年增长12%,
        连续15年保持两位数增长」——若把句内任意年份当归属，2002 之类的基准年
        会被当成 10298 亿的年份，居民人均可支配收入跨度一度变成 2002-2025。
        """
        got = extract_metrics(
            "全市生产总值达到10297亿元,按可比价格计算比上年增长12%,比2002年增长71.7%",
            region_code="x", year=2007, section_key="review")
        gdp = [m for m in got if m["metric_key"] == "gdp"]
        self.assertTrue(gdp)
        self.assertEqual(gdp[0]["year"], 2006, "10297 亿属于 2006 年，不是 2002")

    def test_first_non_comparison_year_in_a_multi_year_sentence(self) -> None:
        """武汉 2011：「2010年达到5515.8亿元,是2005年的2.4倍」取 2010。"""
        got = extract_metrics(
            "地区生产总值连续跨越3000亿元、4000亿元、5000亿元3个台阶,"
            "2010年达到5515.8亿元,是2005年的2.4倍,五年年均增长14.8%",
            region_code="x", year=2011, section_key="review")
        gdp = [m for m in got if m["metric_key"] == "gdp"]
        self.assertTrue(gdp)
        self.assertEqual(gdp[0]["year"], 2010)


class CommuniqueParseTests(unittest.TestCase):
    """统计公报是补报告缺口的来源，指标覆盖要够。"""

    def _parse(self, text: str) -> dict[str, float]:
        from work_report_verify import parse_communique

        got = parse_communique(text, region_code="310000", year=2025,
                               publisher="上海市统计局", url="https://tjj.sh.gov.cn/x.html")
        return {m["metric_key"]: m["value"] for m in got}

    def test_real_shanghai_communique_sentences(self) -> None:
        """整段取自 tjj.sh.gov.cn/tjgb/ 2025 年上海市统计公报原文。"""
        text = (
            "初步核算,全年实现地区生产总值56708.71亿元,比上年增长5.4%。"
            "全年地方一般公共预算收入8500.91亿元,比上年增长1.5%。"
            "地方一般公共预算支出9975.99亿元,比上年增长1.0%。"
            "至年末,全市常住人口为2485.41万人。"
            "全年城镇调查失业率平均值为4.2%。"
            "据抽样调查,全年全市居民人均可支配收入91987元,比上年增长4.1%。"
            "全年居民消费价格总水平比上年上涨0.2%。"
        )
        got = self._parse(text)
        for key in ("gdp", "fiscal_revenue", "fiscal_expenditure", "population",
                    "urban_surveyed_unemployment", "per_capita_income", "cpi"):
            with self.subTest(metric=key):
                self.assertIn(key, got)
        self.assertEqual(got["gdp"], 56708.71)
        self.assertEqual(got["population"], 2485.41)

    def test_two_token_link_phrases_are_matched(self) -> None:
        """「平均值为」是两个 token，单 token 可选组匹配不上。"""
        self.assertIn("urban_surveyed_unemployment",
                      self._parse("全年城镇调查失业率平均值为4.2%"))

    def test_provenance_is_marked(self) -> None:
        from work_report_verify import PROVENANCE_COMMUNIQUE, parse_communique

        got = parse_communique("全年实现地区生产总值56708.71亿元",
                               region_code="310000", year=2025, publisher="p", url="u")
        self.assertTrue(got)
        self.assertEqual(got[0]["provenance"], PROVENANCE_COMMUNIQUE)
        self.assertEqual(got[0]["source_publisher"], "p")
        self.assertEqual(got[0]["source_url"], "u")
        self.assertFalse(got[0]["is_target"], "公报里的数字都是已实现值")


class MetricExtractTests(unittest.TestCase):
    def test_single_digit_percentages_are_kept(self) -> None:
        """个位数百分比早先被 ^\\d{1,2}$ 当噪声丢掉。"""
        cases = [
            ("地区生产总值增长5%左右、实际工作中力争更好结果", "gdp_growth"),
            ("居民消费价格涨幅2%左右", "cpi"),
            ("城镇调查失业率5%以内", "urban_surveyed_unemployment"),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                got = extract_metrics(text, region_code="x", year=2026, section_key="target")
                self.assertIn(expected, [m["metric_key"] for m in got],
                              f"{text!r} 应抽出 {expected}")

    def test_per_capita_gdp_is_not_counted_as_gdp(self) -> None:
        got = extract_metrics("人均地区生产总值达到3.2万美元", region_code="x", year=2026)
        keys = [m["metric_key"] for m in got]
        self.assertIn("per_capita_gdp", keys)
        self.assertNotIn("gdp", keys, "人均 GDP 不是 GDP 总量，不能重复计入")

    def test_gdp_not_pulled_from_a_later_clause(self) -> None:
        """交叉验证抓到过：上海 2026 的 gdp 出现 5.67万亿元 与 3.2万美元。

        后者来自「全市生产总值达到5.67万亿元…人均地区生产总值达到3.2万美元」
        这类一句话里先总量后人均的写法。人均紧邻匹配点、也可隔着"地区"，
        所以只回看 2 个字挡不住，必须看窗口里有没有"人均"。
        """
        text = "全市生产总值达到5.67万亿元、增长5.4%,人均地区生产总值达到3.2万美元"
        got = extract_metrics(text, region_code="x", year=2026)
        gdp = [m for m in got if m["metric_key"] == "gdp"]
        self.assertEqual(len(gdp), 1, "GDP 总量只应出现一次")
        self.assertEqual(gdp[0]["value"], 5.67)

    def test_elderly_care_beds_are_not_medical_beds(self) -> None:
        """数值对、指标错，比抽不到更糟。"""
        for text in (
            "新增养老床位3852张、认知障碍照护床位3326张",
            "筹措供应“新时代城市建设者管理者之家”床位3.2万张",
        ):
            with self.subTest(text=text):
                got = extract_metrics(text, region_code="x", year=2026)
                self.assertEqual([m["metric_key"] for m in got], [],
                                 "养老/保障房床位不得进入医疗床位指标")

    def test_is_target_follows_the_section_not_the_sentence(self) -> None:
        """同句里另一个指标的"达到"不能污染本指标。"""
        text = "地方一般公共预算收入达到8500亿元、增长1.5%,城镇调查失业率平均为4.2%"
        actual = extract_metrics(text, region_code="x", year=2026, section_key="review")
        unemp = [m for m in actual if m["metric_key"] == "urban_surveyed_unemployment"]
        self.assertTrue(unemp, "应抽出调查失业率")
        self.assertFalse(unemp[0]["is_target"],
                         "该值在「上年工作回顾」段，是已发生事实，不该标成目标")

    def test_target_section_marks_values_as_target(self) -> None:
        got = extract_metrics("城镇新增就业60万人以上", region_code="x", year=2026,
                              section_key="target")
        self.assertTrue(got)
        self.assertTrue(got[0]["is_target"])

    def test_value_and_unit_are_kept_verbatim(self) -> None:
        """不做量级归一：原文 64.1 万人就存 64.1 + 万人。"""
        got = extract_metrics("城镇新增就业64.1万人", region_code="x", year=2026)
        jobs = [m for m in got if m["metric_key"] == "new_urban_jobs"]
        self.assertTrue(jobs)
        self.assertEqual(jobs[0]["value"], 64.1)
        self.assertEqual(jobs[0]["unit"], "万人")

    def test_percentage_keeps_its_unit(self) -> None:
        got = extract_metrics("城镇调查失业率平均为4.2%", region_code="x", year=2026)
        self.assertEqual(got[0]["unit"], "%")

    def test_caliber_is_always_recorded(self) -> None:
        """口径缺失会让跨地区对比变成假的。"""
        got = extract_metrics("全市生产总值达到5.67万亿元", region_code="x", year=2026)
        self.assertTrue(got)
        self.assertTrue(got[0]["caliber"], "每个指标都必须带口径")

    def test_year_numbers_are_not_metrics(self) -> None:
        got = extract_metrics("2025年是“十四五”规划冲刺收官之年", region_code="x", year=2026)
        self.assertEqual(got, [])


class TopicHitTests(unittest.TestCase):
    def test_livelihood_topics_are_detected_without_numbers(self) -> None:
        text = ("深化养老服务改革，改建2000张认知障碍照护床位，"
                "新增公办幼儿园托班托额3630个，城镇新增就业60万人以上。")
        hits = extract_topic_hits(text)
        self.assertIn("养老", hits)
        self.assertIn("教育", hits)
        self.assertIn("就业", hits)

    def test_no_hits_returns_empty(self) -> None:
        self.assertEqual(extract_topic_hits("今天天气晴朗。"), {})


if __name__ == "__main__":
    unittest.main()
