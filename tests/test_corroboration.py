from __future__ import annotations

import unittest

from rules.corroboration import corroborate_records, corroboration_summary


class CorroborationTests(unittest.TestCase):
    def test_distinct_domains_with_same_topic_are_corroborated(self) -> None:
        records = [
            {"id": 1, "source_id": "a", "source_url": "https://gov.example.cn/1", "title": "B300芯片产业政策", "region_province": "四川省", "category_l1": "行业政策", "publish_date": "2026-03-01"},
            {"id": 2, "source_id": "b", "source_url": "https://news.example.cn/2", "title": "B300芯片产业动态", "region_province": "四川省", "category_l1": "行业政策", "publish_date": "2026-03-02"},
        ]
        config = {"corroboration": {"enabled": True, "min_distinct_sources": 2, "topic_terms": ["B300", "芯片"]}}
        result = corroborate_records(records, config)
        self.assertTrue(all(item["corroboration_status"] == "corroborated" for item in result))
        self.assertEqual(corroboration_summary(result)["corroborated"], 2)

    def test_single_source_is_not_promoted(self) -> None:
        result = corroborate_records([{"id": 1, "source_id": "a", "source_url": "https://gov.example.cn/1", "title": "人工智能政策", "category_l1": "行业政策", "publish_date": "2026-03-01"}], {"corroboration": {"enabled": True, "topic_terms": ["人工智能"]}})
        self.assertEqual(result[0]["corroboration_status"], "single_source")
        self.assertIn("等待", result[0]["corroboration_reason"])


if __name__ == "__main__":
    unittest.main()
