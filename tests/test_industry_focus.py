from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from rules.industry_focus import apply_industry_focus


class IndustryFocusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = yaml.safe_load(Path("config/industry_focus.yaml").read_text(encoding="utf-8"))

    def test_ndrc_guidance_tracks_ai_and_new_energy(self) -> None:
        record = {
            "source_id": "ndrc_gov",
            "title": "国家发展改革委关于人工智能和新能源产业发展的指导",
            "content": "推动人工智能、新能源装备和新型储能产业发展。",
            "publish_date": "2026-02-01",
        }
        result = apply_industry_focus(record, self.config)
        self.assertTrue(result["industry_focus"])
        self.assertTrue(result["guidance_source"])
        self.assertEqual(result["guidance_year"], "2026")
        self.assertIn("人工智能", result["industry_tags"])
        self.assertIn("新能源", result["industry_tags"])

    def test_non_guidance_source_is_not_marked_as_annual_guidance(self) -> None:
        record = {
            "source_id": "local_company_notice",
            "title": "企业人工智能项目招聘",
            "content": "招聘人工智能工程师。",
            "publish_date": "2026-03-01",
        }
        result = apply_industry_focus(record, self.config)
        self.assertTrue(result["industry_focus"])
        self.assertFalse(result["guidance_source"])


if __name__ == "__main__":
    unittest.main()
