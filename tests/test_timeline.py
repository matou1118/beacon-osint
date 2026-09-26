from __future__ import annotations

import unittest
from datetime import date, timedelta

from extractors.timeline import (
    TIER_ARCHIVE,
    TIER_CURRENT,
    TIER_RECENT,
    build_timeline,
    coverage_gaps,
    freshness_tier,
    parse_date,
    tier_summary,
    timeline_by_program,
)


class ParseDateTests(unittest.TestCase):
    def test_accepts_common_formats(self) -> None:
        for raw in ("2026-09-25", "2026/09/25", "2026-09-25 10:30:00", "2026-09-25T10:30:00Z"):
            with self.subTest(raw=raw):
                self.assertEqual(parse_date(raw), date(2026, 9, 25))

    def test_chinese_date(self) -> None:
        self.assertEqual(parse_date("2026年9月25日"), date(2026, 9, 25))

    def test_junk_is_none(self) -> None:
        for raw in (None, "", "无", "待定"):
            self.assertIsNone(parse_date(raw))


class TierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.today = date(2026, 9, 26)

    def test_last_year_is_current(self) -> None:
        recent = (self.today - timedelta(days=100)).isoformat()
        self.assertEqual(freshness_tier(recent, self.today), TIER_CURRENT)

    def test_one_to_three_years_is_recent(self) -> None:
        old = (self.today - timedelta(days=800)).isoformat()
        self.assertEqual(freshness_tier(old, self.today), TIER_RECENT)

    def test_beyond_three_years_is_archive(self) -> None:
        older = (self.today - timedelta(days=1500)).isoformat()
        self.assertEqual(freshness_tier(older, self.today), TIER_ARCHIVE)

    def test_undated_falls_into_archive(self) -> None:
        self.assertEqual(freshness_tier(None, self.today), TIER_ARCHIVE)

    def test_summary_counts_each_tier(self) -> None:
        today = date(2026, 9, 26)
        rows = [
            {"publish_date": (today - timedelta(days=30)).isoformat()},
            {"publish_date": (today - timedelta(days=800)).isoformat()},
            {"publish_date": (today - timedelta(days=2000)).isoformat()},
            {"publish_date": None},
        ]
        summary = tier_summary(rows, today)
        self.assertEqual(summary["counts"][TIER_CURRENT], 1)
        self.assertEqual(summary["counts"][TIER_RECENT], 1)
        self.assertEqual(summary["counts"][TIER_ARCHIVE], 2)


class TimelineTests(unittest.TestCase):
    def _record(self, rid, title, published, level="省级"):
        return {
            "id": rid,
            "title": title,
            "content": f"关于{title}的通知正文内容说明。",
            "publish_date": published,
            "gov_level": level,
            "region_province": "四川省",
            "source_url": f"https://example.gov.cn/{rid}.html",
        }

    def test_sorted_oldest_first(self) -> None:
        rows = [
            self._record(1, "2026年实施方案", "2026-05-01"),
            self._record(2, "2023年实施方案", "2023-05-01"),
            self._record(3, "2025年实施方案", "2025-05-01"),
        ]
        line = build_timeline(rows)
        self.assertEqual([item["publish_date"] for item in line],
                         ["2023-05-01", "2025-05-01", "2026-05-01"])

    def test_undated_kept_at_the_end(self) -> None:
        rows = [
            self._record(1, "有日期", "2025-01-01"),
            self._record(2, "无日期", None),
        ]
        line = build_timeline(rows)
        self.assertEqual(len(line), 2)
        self.assertIsNone(line[-1]["publish_date"])

    def test_each_line_carries_tier_and_link(self) -> None:
        line = build_timeline([self._record(1, "实施方案", "2026-01-01")])
        self.assertEqual(line[0]["tier"], TIER_CURRENT)
        self.assertTrue(line[0]["url"])

    def test_timeline_by_program_needs_more_than_one_document(self) -> None:
        rows = [
            self._record(1, "国务院关于印发《农村人居环境整治行动方案》的通知", "2023-01-01", "国家部委"),
            self._record(2, "四川省关于印发农村人居环境整治行动方案的通知", "2024-01-01"),
        ]
        groups = timeline_by_program(rows)
        self.assertTrue(groups)
        self.assertEqual(len(list(groups.values())[0]), 2)


class GapTests(unittest.TestCase):
    def test_reports_missing_years(self) -> None:
        rows = [{"publish_date": "2026-01-01"}, {"publish_date": "2025-06-01"}]
        gaps = coverage_gaps(rows, span_years=10)
        self.assertEqual(gaps["coverage_rate"], 0.2)
        self.assertIn("2024", gaps["missing_years"])
        self.assertNotIn("2025", gaps["missing_years"])

    def test_empty_corpus_is_all_missing(self) -> None:
        gaps = coverage_gaps([], span_years=5)
        self.assertEqual(gaps["coverage_rate"], 0.0)
        self.assertEqual(len(gaps["missing_years"]), 5)


if __name__ == "__main__":
    unittest.main()
