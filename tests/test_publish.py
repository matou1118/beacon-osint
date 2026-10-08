"""成刊引擎测试。

重点是三件容易悄悄坏掉的事：
1. 刊期边界——跨期重复是成刊最容易出的错
2. 事件必须真有多个来源（1 个来源标成「多源事件」是误导）
3. 导语里的数字必须来自统计，不能编
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from lighthouse.publish import (build_edition, build_lead, period, render_markdown,
                                sort_items, write_edition)

SCHEMA = """
CREATE TABLE announcements (
    id INTEGER PRIMARY KEY, source_id TEXT, source_url TEXT, title TEXT,
    publish_date TEXT, category_l1 TEXT, category_l2 TEXT, region_province TEXT,
    region_city TEXT, gov_level TEXT, amount TEXT, deadline TEXT,
    target_group TEXT, media_cluster_id TEXT, media_heat REAL, media_terms TEXT,
    corroboration_status TEXT, corroboration_count INTEGER,
    corroborating_sources TEXT, needs_review INTEGER, extra TEXT
);
"""


def _row(rid, sid, title, day, *, cluster="", heat=None, region="浙江省",
         body="国家能源局发布绿证数据。绿证核发3.54亿个。", category="行业政策",
         corrob=0, summary="国家能源局发布绿证数据。绿证核发3.54亿个。"):
    extra = json.dumps({"summary": summary, "issuing_body": sid}, ensure_ascii=False)
    return (rid, sid, f"https://{sid}/{rid}", title, day, category, None, region,
            None, "省级", None, None, None, cluster, heat, None,
            "corroborated" if corrob >= 2 else "single_source", corrob, None, 0,
            extra)


class PeriodTests(unittest.TestCase):
    def test_daily_covers_one_day(self) -> None:
        start, end = period("daily", date(2026, 9, 30))
        self.assertEqual((start.isoformat(), end.isoformat()),
                         ("2026-09-30", "2026-10-01"))

    def test_weekly_is_seven_days_ending_at_ref(self) -> None:
        start, end = period("weekly", date(2026, 9, 30))
        self.assertEqual((start.isoformat(), end.isoformat()),
                         ("2026-09-23", "2026-09-30"))
        self.assertEqual((end - start).days, 7)

    def test_monthly_is_the_whole_previous_month(self) -> None:
        start, end = period("monthly", date(2026, 10, 1))
        self.assertEqual((start.isoformat(), end.isoformat()),
                         ("2026-09-01", "2026-10-01"))

    def test_monthly_handles_january_rolling_into_previous_year(self) -> None:
        start, end = period("monthly", date(2026, 1, 15))
        self.assertEqual((start.isoformat(), end.isoformat()),
                         ("2025-12-01", "2026-01-01"))

    def test_unknown_edition_rejected(self) -> None:
        with self.assertRaises(ValueError):
            period("yearly", date(2026, 1, 1))


class EditionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        con = sqlite3.connect(self.db)
        con.executescript(SCHEMA)
        con.executemany("INSERT INTO announcements VALUES (" + ",".join("?" * 21) + ")", [
            # 同一个 cluster 的两个来源 → 真事件
            _row(1, "zj_gov", "绿证数据发布", "2026-09-30", cluster="c1", heat=24.0, corrob=2),
            _row(2, "cq_gov", "绿证数据发布", "2026-09-30", cluster="c1", heat=24.0,
                 corrob=2, region="重庆市", category="行业政策"),
            # 只有一个来源的 cluster → 不能算事件（回填按整月判过 min_sources，
            # 成刊按日开窗后可能只剩一个成员）
            _row(3, "sc_gov", "独苗通知", "2026-09-30", cluster="c2", heat=12.0),
            _row(4, "sd_gov", "普通公告", "2026-09-30"),
            _row(5, "hn_gov", "上期公告", "2026-09-28"),
        ])
        con.commit()
        con.close()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _daily(self) -> dict:
        return build_edition("daily", date(2026, 9, 30), self.db)

    def test_daily_excludes_previous_day(self) -> None:
        """跨期重复是最容易出的错：9-28 那条不能进 9-30 的日报。"""
        payload = self._daily()
        self.assertEqual(payload["stats"]["total"], 4)
        self.assertNotIn("上期公告",
                         json.dumps(payload, ensure_ascii=False))

    def test_multi_source_cluster_becomes_one_event(self) -> None:
        payload = self._daily()
        events = [i for s in payload["sections"] for i in s["items"] if i.get("is_event")]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["source_count"], 2)
        self.assertEqual(sorted(s["source_id"] for s in events[0]["sources"]),
                         ["cq_gov", "zj_gov"])

    def test_single_source_cluster_is_not_an_event(self) -> None:
        """1 个来源标成「多源事件」是在对外宣称交叉印证，而实际上没有。"""
        payload = self._daily()
        titles = [i["title"] for s in payload["sections"] for i in s["items"]]
        self.assertIn("独苗通知", titles)
        items = [i for s in payload["sections"] for i in s["items"]
                 if i["title"] == "独苗通知"]
        self.assertFalse(items[0].get("is_event"))
        self.assertEqual(payload["stats"]["events"], 1)

    def test_same_source_deduped_inside_event(self) -> None:
        """同一 source_id 在一个事件里只算一次（AIHOT 的抗刷规则）。"""
        con = sqlite3.connect(self.db)
        con.execute("INSERT INTO announcements VALUES (" + ",".join("?" * 21) + ")",
                    _row(6, "zj_gov", "绿证数据发布转载", "2026-09-30", cluster="c1",
                         heat=24.0, corrob=2))
        con.commit()
        con.close()
        payload = self._daily()
        events = [i for s in payload["sections"] for i in s["items"] if i.get("is_event")]
        self.assertEqual(events[0]["source_count"], 2, "zj_gov 出现两次只应算一个来源")

    def test_events_sort_before_plain_records(self) -> None:
        payload = self._daily()
        first = payload["sections"][0]["items"][0]
        self.assertTrue(first.get("is_event"), "事件应排在各节首位")

    def test_stats_are_not_invented(self) -> None:
        payload = self._daily()
        self.assertEqual(payload["stats"]["total"], 4)
        self.assertEqual(payload["stats"]["regions"], 2)
        self.assertEqual(payload["stats"]["corroborated"], 2)
        self.assertEqual(payload["item_count"], 3, "4 条记录合成 3 个条目")

    def test_empty_period_does_not_blow_up(self) -> None:
        payload = build_edition("daily", date(2020, 1, 1), self.db)
        self.assertEqual(payload["stats"]["total"], 0)
        self.assertEqual(payload["sections"], [])
        self.assertEqual(payload["lead"], "本期无收录。")


class LeadTests(unittest.TestCase):
    def test_lead_uses_only_real_numbers(self) -> None:
        stats = {"total": 45, "events": 2, "corroborated": 3, "categories": 11,
                 "regions": 4}
        lead = build_lead(stats, [{"title": "绿证数据", "corroboration": {"count": 3}}])
        self.assertIn("45 条", lead)
        self.assertIn("4 个地区", lead)
        self.assertIn("3 条经多源交叉印证", lead)
        self.assertIn("绿证数据", lead)
        self.assertIn("3 个独立来源", lead)

    def test_lead_omits_absent_stats(self) -> None:
        lead = build_lead({"total": 5}, [])
        self.assertEqual(lead, "本期收录 5 条。")


class SortTests(unittest.TestCase):
    def test_newer_date_wins_on_ties(self) -> None:
        items = [
            {"title": "old", "publish_date": "2026-09-01", "is_event": False},
            {"title": "new", "publish_date": "2026-09-28", "is_event": False},
        ]
        self.assertEqual([i["title"] for i in sort_items(items)], ["new", "old"])

    def test_event_beats_record_then_heat_then_date(self) -> None:
        items = [
            {"title": "plain", "publish_date": "2026-09-30", "is_event": False},
            {"title": "cold", "publish_date": "2026-09-30", "is_event": True, "heat_score": 1},
            {"title": "hot", "publish_date": "2026-09-30", "is_event": True, "heat_score": 99},
        ]
        self.assertEqual([i["title"] for i in sort_items(items)],
                         ["hot", "cold", "plain"])


class OutputTests(unittest.TestCase):
    def test_markdown_truncates_but_json_stays_complete(self) -> None:
        payload = {"edition": "monthly", "edition_label": "月报", "ref_date": "2026-10-01",
                   "period": {"from": "2026-09-01", "to": "2026-10-01", "label": "2026-09"},
                   "generated_at": "2026-10-01T00:00:00+00:00",
                   "stats": {"total": 50, "events": 0, "corroborated": 0,
                             "categories": 1, "regions": 0},
                   "lead": "本期收录 50 条。", "item_count": 50,
                   "sections": [{"category_l1": "其他", "items": [
                       {"title": f"t{i}", "summary": "s", "publish_date": "2026-09-01",
                        "source": {"url": "https://x"}}
                       for i in range(50)]}]}
        md = render_markdown(payload, per_section=5)
        self.assertIn("本节共 50 条", md)
        self.assertIn("只列前 5 条", md)
        self.assertNotIn("### t49", md)

        with tempfile.TemporaryDirectory() as tmp:
            paths = write_edition(payload, Path(tmp), per_section=5)
            data = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
            self.assertEqual(len(data["sections"][0]["items"]), 50,
                             "JSON 是机器契约，不该被 markdown 的条数限制截断")


if __name__ == "__main__":
    unittest.main()
