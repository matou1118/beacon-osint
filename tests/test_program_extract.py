from __future__ import annotations

import unittest

from extractors.program_extract import (
    analyse_programs,
    extract_program_candidates,
    is_program_name,
    normalize_name,
)

# A program shows up at several levels and over several years, each level issuing
# its own rules. AGENTS.md 4.9 requires both conditions.


class NormalizeTests(unittest.TestCase):
    def test_strips_issuing_wrapper(self) -> None:
        self.assertEqual(normalize_name("关于印发农村人居环境整治行动方案的通知"), "农村人居环境整治行动方案")

    def test_strips_inline_html_from_titles(self) -> None:
        # Gov CMS titles carry markup: "文化产业发展<div>十五五规划".
        self.assertEqual(normalize_name("文化产业发展<div>十五五规划"), "文化产业发展十五五规划")

    def test_keeps_the_subject(self) -> None:
        self.assertIn("碳达峰", normalize_name("关于印发《“十五五”碳达峰行动方案》的通知"))


class IsProgramTests(unittest.TestCase):
    def test_program_names_pass(self) -> None:
        for name in ("农村人居环境整治行动方案", "十五五碳达峰行动方案", "三北防护林体系建设工程"):
            with self.subTest(name=name):
                self.assertTrue(is_program_name(name))

    def test_statutes_are_not_programs(self) -> None:
        # A cited statute is the authority for a notice, not a program.
        for name in ("中华人民共和国大气污染防治法", "农药管理条例", "企业会计准则解释第22号征求意见稿"):
            with self.subTest(name=name):
                self.assertFalse(is_program_name(name))

    def test_mastheads_and_serials_are_not_programs(self) -> None:
        for name in ("中国能源报", "中国建设报", "豫政办豫政函"):
            with self.subTest(name=name):
                self.assertFalse(is_program_name(name))

    def test_generic_words_are_not_programs(self) -> None:
        for name in ("通知", "公告", "公示"):
            with self.subTest(name=name):
                self.assertFalse(is_program_name(name))

    def test_furniture_is_not_a_program(self) -> None:
        self.assertFalse(is_program_name("机构非物质文化遗产司发布日期2026-09-21分类规划"))


class ExtractTests(unittest.TestCase):
    def test_reads_a_quoted_instrument_from_the_title(self) -> None:
        names = extract_program_candidates(
            {"title": "关于印发《农村人居环境整治行动方案》的通知", "content": ""}
        )
        self.assertIn("农村人居环境整治行动方案", names)

    def test_falls_back_to_the_body(self) -> None:
        names = extract_program_candidates(
            {"title": "关于有关事项的通知", "content": "现将《重点河流及涝区治理实施方案》印发给你们。"}
        )
        self.assertTrue(any("重点河流" in n for n in names))

    def test_no_candidates_for_plain_news(self) -> None:
        names = extract_program_candidates(
            {"title": "八月全社会用电量再破万亿", "content": "2026年8月全社会用电量达到10332亿千瓦时。"}
        )
        self.assertEqual(names, [])


class AnalyseTests(unittest.TestCase):
    def _record(self, rid, title, year, level, province="四川省"):
        return {
            "id": rid,
            "title": title,
            "content": f"现将有关事项通知如下：{title}",
            "publish_date": f"{year}-05-01",
            "gov_level": level,
            "region_province": province,
        }

    def test_same_program_across_years_and_levels_is_a_program(self) -> None:
        rows = [
            self._record(1, "国务院关于印发《农村人居环境整治行动方案》的通知", "2023", "国家部委"),
            self._record(2, "四川省关于印发农村人居环境整治行动方案的通知", "2024", "省级"),
            self._record(3, "成都市关于印发农村人居环境整治行动方案实施细则的通知", "2025", "市级"),
        ]
        report = analyse_programs(rows)
        programs = [d for d in report["detail"] if d["is_program"]]
        self.assertTrue(programs, "same program across years and levels must be detected")
        self.assertGreaterEqual(len(programs[0]["levels"]), 2)
        self.assertGreaterEqual(len(programs[0]["years"]), 2)

    def test_single_year_is_not_a_program(self) -> None:
        rows = [
            self._record(1, "国务院关于印发《农村人居环境整治行动方案》的通知", "2024", "国家部委"),
            self._record(2, "四川省关于印发农村人居环境整治行动方案的通知", "2024", "省级"),
        ]
        report = analyse_programs(rows)
        self.assertEqual(report["programs"], 0)
        self.assertEqual(report["near_miss_multi_level"], 1)

    def test_single_level_is_not_a_program(self) -> None:
        rows = [
            self._record(1, "四川省关于印发《重点河流及涝区治理实施方案》的通知", "2023", "省级"),
            self._record(2, "四川省关于印发重点河流及涝区治理实施方案的通知", "2024", "省级"),
        ]
        report = analyse_programs(rows)
        self.assertEqual(report["programs"], 0)
        self.assertEqual(report["near_miss_multi_year"], 1)


if __name__ == "__main__":
    unittest.main()
