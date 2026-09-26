from __future__ import annotations

import unittest

from collectors.education import EducationCollector
from collectors.source_router import SourceRouter


class EducationCollectorTests(unittest.TestCase):
    def test_router_and_defaults_mark_admissions(self) -> None:
        source = {
            "id": "fixture_education",
            "name": "测试教育来源",
            "domain": "example.gov.cn",
            "url": "https://example.gov.cn/education/",
            "collector": "education",
            "check_robots": False,
        }
        collector = SourceRouter().collector_for(source)
        self.assertIsInstance(collector, EducationCollector)
        self.assertEqual(collector.source["category_l1"], "招生信息")
        collector.close()


if __name__ == "__main__":
    unittest.main()
