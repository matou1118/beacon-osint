from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from api.config import Settings
from api.main import create_app


class StatsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        settings = Settings(database_path=":memory:", environment="development", jwt_secret="test-secret-012345678901234567890123")
        self.app = create_app(db_path=":memory:", settings=settings)
        self.client = TestClient(self.app)
        self.app.state.database.upsert_announcements(
            [
                {
                    "source_id": "sc_stats_1",
                    "source_url": "https://example.gov.cn/stats/1",
                    "title": "光伏储能产业扶持通知",
                    "publish_date": "2026-09-01",
                    "content": "支持新能源光伏储能项目",
                    "issuing_body": "省发改委",
                    "gov_level": "省级",
                    "region_province": "四川省",
                    "category_l1": "行业政策",
                    "category_l2": "产业规划",
                    "industry_focus": True,
                    "industry_tags": ["新能源"],
                },
                {
                    "source_id": "sc_stats_2",
                    "source_url": "https://example.gov.cn/stats/2",
                    "title": "建设工程中标公示",
                    "publish_date": "2026-09-02",
                    "content": "公示内容",
                    "issuing_body": "市住建局",
                    "gov_level": "市级",
                    "region_province": "广东省",
                    "category_l1": "公示公告",
                    "category_l2": "公示",
                },
            ]
        )

    def tearDown(self) -> None:
        self.client.close()

    def test_stats_overview_shape_and_industry_scope(self) -> None:
        response = self.client.get("/api/stats")
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        for key in ("total", "corroborated", "provinces", "categories", "cat_data", "ind_data", "prov_ind_data", "prov_data", "year_data", "gov_level", "day_data"):
            self.assertIn(key, data)
        self.assertEqual(data["total"], 2)
        # 行业口径只收 行业政策/行业补贴 的子类：「公示」是体裁，不能混进行业榜
        self.assertEqual([row["name"] for row in data["ind_data"]], ["产业规划"])
        self.assertTrue(any(row["ind"] == "产业规划" and row["prov"] == "四川省" for row in data["prov_ind_data"]))
        self.assertEqual({row["name"] for row in data["gov_level"]}, {"省级", "市级"})
        self.assertEqual(data["year_data"], [{"year": "2026", "count": 2}])

    def test_list_exposes_industry_tags(self) -> None:
        response = self.client.get("/api/announcements?page_size=10")
        self.assertEqual(response.status_code, 200)
        items = response.json()["data"]["items"]
        tagged = next(item for item in items if "光伏" in item["title"])
        self.assertEqual(tagged["industry_tags"], ["新能源"])
        self.assertTrue(tagged["industry_focus"])


if __name__ == "__main__":
    unittest.main()
