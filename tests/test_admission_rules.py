from __future__ import annotations

import json
import time
import unittest
from pathlib import Path

from rules.engine import match_announcements


class AdmissionRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.records = json.loads(Path("tests/fixtures/admission_announcements.json").read_text(encoding="utf-8"))
        self.config = {
            "rules": [
                {
                    "name": "招生院校类型",
                    "conditions": [
                        {"field": "category_l1", "operator": "eq", "value": "招生信息"},
                        {"field": "institution_type", "operator": "contains", "value": "${user.institution_preference}"},
                    ],
                    "score": 35,
                    "reason": "招生信息匹配：{institution_name}（{institution_type}）",
                }
            ]
        }

    def test_filters_high_vocational_public_college(self) -> None:
        user = {
            "levels": ["省级"],
            "categories": ["招生信息"],
            "institution_types": ["公办专科"],
            "institution_preference": ["公办专科"],
            "admission_types": ["高职单招"],
        }
        result = match_announcements(self.records, user, self.config)
        self.assertEqual([item["source_id"] for item in result], ["fixture_vocational"])
        self.assertTrue(result[0]["reasons"])

    def test_filters_military_and_directed_categories(self) -> None:
        user = {
            "levels": ["国家部委", "省级"],
            "categories": ["招生信息"],
            "institution_types": ["军队院校"],
            "institution_preference": ["军队院校"],
        }
        result = match_announcements(self.records, user, self.config)
        self.assertEqual([item["source_id"] for item in result], ["fixture_military"])

        user["institution_types"] = []
        user["admission_sub_categories"] = ["公费定向培养"]
        result = match_announcements(self.records, user, self.config)
        self.assertEqual([item["source_id"] for item in result], ["fixture_directed_medical"])

    def test_employment_only_is_a_hard_filter(self) -> None:
        user = {
            "categories": ["招生信息"],
            "employment_only": True,
        }
        result = match_announcements(self.records, user, self.config)
        self.assertEqual([item["source_id"] for item in result], ["fixture_military"])

    def test_matching_is_under_three_seconds(self) -> None:
        started = time.perf_counter()
        match_announcements(self.records, {"categories": ["招生信息"]}, self.config)
        self.assertLess(time.perf_counter() - started, 3.0)


if __name__ == "__main__":
    unittest.main()
