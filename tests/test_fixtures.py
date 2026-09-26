from __future__ import annotations

import json
import unittest
from pathlib import Path

from collectors.municipal import MunicipalCollector
from collectors.provincial import ProvincialCollector
from main import load_config, resolve_sources


FIXTURES = Path(__file__).parent / "fixtures"


class FixtureTests(unittest.TestCase):
    def test_local_records_cover_all_scopes(self) -> None:
        records = json.loads((FIXTURES / "sample_announcements.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(records), 8)
        self.assertEqual(
            {record["gov_level"] for record in records},
            {"国家部委", "省级", "市级", "县级", None, "其他官方公共信息"},
        )
        self.assertIn("fixture_school", {record["source_id"] for record in records})
        self.assertIn("official_media", {record["source_type"] for record in records})
        self.assertTrue(all(record["category_l1"] for record in records))

    def test_provincial_css_selector(self) -> None:
        html = (FIXTURES / "provincial_list.html").read_text(encoding="utf-8")
        source = {
            "id": "fixture_province",
            "domain": "example.invalid",
            "url": "https://example.invalid/list",
            "list_selector": "main.notice-list",
            "detail_pattern": "/notice/",
        }
        collector = ProvincialCollector(source, client=object())
        self.assertEqual(
            collector._extract_links(html, "https://example.invalid/list"),
            [
                "https://example.invalid/sc/notice/1",
                "https://example.invalid/sc/notice/2",
            ],
        )
        collector.close()

    def test_municipal_css_selector_and_config_resolution(self) -> None:
        html = (FIXTURES / "municipal_list.html").read_text(encoding="utf-8")
        source = {
            "id": "fixture_city",
            "domain": "example.invalid",
            "url": "https://example.invalid/list",
            "list_selector": "section.list",
        }
        collector = MunicipalCollector(source, client=object())
        self.assertEqual(len(collector._extract_links(html, "https://example.invalid/list")), 2)
        collector.close()
        config = load_config("config/sources.yaml")
        sources = {source["id"]: source for source in resolve_sources(config)}
        self.assertEqual(sources["ndrc_gov"]["focus_level"], "critical")
        self.assertEqual(sources["nea_gov"]["focus_level"], "critical")
        self.assertEqual(sources["sc_gov"]["region_province"], "四川省")
        self.assertEqual(sources["cd_gov"]["gov_level"], "市级")


if __name__ == "__main__":
    unittest.main()
