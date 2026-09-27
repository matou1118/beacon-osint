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
