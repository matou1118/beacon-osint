from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from main import enrich_records
from output.pusher import EmailPusher
from rules.engine import match_announcements
from rules.tracker import apply_focus_tracking
from storage.db import Database


class EndToEndTests(unittest.TestCase):
    def test_fixture_collect_extract_store_match_and_deduplicated_email(self) -> None:
        records = json.loads(Path("tests/fixtures/sample_announcements.json").read_text(encoding="utf-8"))
        source_map = {
            record["source_id"]: {
                "name": record.get("issuing_body") or record["source_id"],
                "gov_level": record.get("gov_level"),
                "region_province": record.get("region_province"),
                "region_city": record.get("region_city"),
                "region_county": record.get("region_county"),
            }
            for record in records
        }
        enriched = [apply_focus_tracking(record, {}) for record in enrich_records(records, source_map)]
        rules = {
            "rules": [
                {"name": "省级", "conditions": [{"field": "gov_level", "operator": "eq", "value": "省级"}], "score": 30, "reason": "层级匹配：{gov_level}"},
                {"name": "分类", "conditions": [{"field": "category_l1", "operator": "in", "value": "${user.categories}"}], "score": 30, "reason": "分类匹配：{category_l1}"},
                {"name": "人群", "conditions": [{"field": "target_group", "operator": "contains", "value": "${user.target_group}"}], "score": 20, "reason": "人群匹配：{target_group}"},
            ]
        }
        user = {"region": "四川省", "levels": ["省级"], "categories": ["行业补贴", "行业政策"], "target_group": "企业"}
        with Database(":memory:") as database:
            database.upsert_announcements(enriched)
            matches = match_announcements(enriched, user, rules)
            self.assertTrue(matches)
            self.assertTrue(all(item["reasons"] for item in matches))
            sent_messages = []
            pusher = EmailPusher(
                database,
                frequency="daily",
                config={"enabled": True, "from_address": "test@example.com", "to_addresses": ["receiver@example.com"]},
                smtp_send=sent_messages.append,
            )
            now = datetime(2026, 9, 25, 14, 30, tzinfo=timezone.utc)
            self.assertTrue(pusher.send(matches[0], now=now))
            self.assertFalse(pusher.send(matches[0], now=now))
            self.assertEqual(len(sent_messages), 1)

    def test_scheduler_scripts_exist(self) -> None:
        self.assertTrue(Path("scripts/run_daily.bat").exists())
        self.assertTrue(Path("scripts/run_weekly.bat").exists())
        self.assertTrue(Path("scripts/run_scheduler.bat").exists())
        self.assertTrue(Path("scheduler.py").exists())


if __name__ == "__main__":
    unittest.main()
