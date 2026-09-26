from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from storage.db import Database


class DatabaseTests(unittest.TestCase):
    def test_upsert_is_idempotent_and_updates_in_place(self) -> None:
        record = {
            "source_id": "test_gov",
            "source_url": "https://www.example.gov.cn/article/1",
            "title": "政策公告",
            "publish_date": "2026-01-02",
            "content": "正文",
            "gov_level": "国家部委",
            "category_l1": "行业政策",
            "content_hash": "a" * 64,
        }
        with Database(":memory:") as database:
            self.assertEqual(database.upsert_announcement(record), "inserted")
            self.assertEqual(database.upsert_announcement(record), "unchanged")
            changed = {**record, "title": "政策公告（修订）", "content": "修订正文", "content_hash": "b" * 64}
            self.assertEqual(database.upsert_announcement(changed), "updated")
            self.assertEqual(database.count_announcements(), 1)
            self.assertEqual(database.list_announcements()[0].title, "政策公告（修订）")
    def test_retention_purge_and_source_schedule(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        record = {
            "source_id": "expired_gov",
            "source_url": "fixture://local/expired",
            "title": "短期通知",
            "publish_date": "2020-01-01",
            "content": "正文",
            "gov_level": "省级",
            "region_province": "四川省",
            "category_l1": "活动通知",
            "retention_class": "transient",
            "retention_until": "2020-02-01T00:00:00+00:00",
            "content_hash": "c" * 64,
        }
        with Database(":memory:") as database:
            self.assertEqual(database.upsert_announcement(record), "inserted")
            self.assertEqual(database.purge_expired(now), {"deleted": 1})
            self.assertEqual(database.count_announcements(), 0)
            self.assertTrue(database.source_due("test_source", now))
            database.mark_source_result("test_source", "daily", 86400, True, now=now)
            self.assertFalse(database.source_due("test_source", now + timedelta(hours=1)))
            self.assertTrue(database.source_due("test_source", now + timedelta(days=1)))


if __name__ == "__main__":
    unittest.main()
