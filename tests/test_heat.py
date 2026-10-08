from __future__ import annotations

import unittest
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from api.config import Settings
from api.main import create_app
from output.heat_report import render_heat_report
from rules.media_cluster import cluster_media_records
from storage.db import Database


class HeatTests(unittest.TestCase):
    def test_heat_formula_and_clustering(self) -> None:
        now = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)
        records = [
            {"id": 1, "source_id": "xinhua", "issuing_body": "新华网", "media_type": "official", "source_url": "https://news.cn/1", "title": "B300芯片政策发布", "content": "人工智能算力芯片政策", "publish_date": "2026-09-25T08:00:00+00:00", "category_l1": "行业政策"},
            {"id": 2, "source_id": "people", "issuing_body": "人民网", "media_type": "official", "source_url": "https://people.cn/2", "title": "B300芯片人工智能政策发布", "content": "B300芯片和算力", "publish_date": "2026-09-25T09:00:00+00:00", "category_l1": "行业政策"},
            {"id": 3, "source_id": "energy", "issuing_body": "中国能源报", "media_type": "industry", "source_url": "https://energy.cn/3", "title": "新能源与芯片产业政策", "content": "新能源政策", "publish_date": "2026-09-25T10:00:00+00:00", "category_l1": "行业政策"},
        ]
        config = {"media_heat": {"weights": {"official": 3, "mainstream": 2, "industry": 1}, "category_weights": {"policy": 2, "livelihood": 1.5, "industry": 1}, "time_decay": {"hours_24": 1, "hours_48": .5, "older": .2}, "policy_keywords": ["政策"], "livelihood_keywords": ["补贴"], "industry_keywords": ["B300", "芯片", "人工智能", "算力", "新能源"], "exclude_keywords": ["娱乐"], "cluster_window_hours": 72}}
        topics = cluster_media_records(records, config, now=now)
        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0]["heat_score"], 42.0)
        self.assertEqual(topics[0]["source_count"], 3)
        self.assertIn("官方", render_heat_report(topics, "2026-09-25"))

    def test_heat_topics_persist(self) -> None:
        database = Database(":memory:")
        topic = {"cluster_id": "abc", "title": "政策话题", "category_l1": "行业政策", "category_l2": "芯片", "heat_score": 12.5, "source_count": 2, "source_names": ["媒体A", "媒体B"], "latest_publish": "2026-09-25 10:00", "article_urls": ["https://a", "https://b"], "heat_reason": "2家媒体报道", "summary": "摘要", "terms": ["芯片"]}
        database.upsert_heat_topics([topic], "2026-09-25")
        self.assertEqual(database.list_heat_topics("2026-09-25")[0]["heat_score"], 12.5)
        self.assertEqual(database.get_heat_topic("abc")["source_count"], 2)
        database.close()

    def test_heat_api_returns_topic_list_and_detail(self) -> None:
        settings = Settings(database_path=":memory:", environment="development", jwt_secret="heat-test-secret-012345678901234567890123")
        app = create_app(db_path=":memory:", settings=settings)
        client = TestClient(app)
        try:
            app.state.database.upsert_heat_topics([{"cluster_id": "api-topic", "title": "API热频话题", "category_l1": "行业政策", "heat_score": 20, "source_count": 2, "source_names": ["媒体A", "媒体B"], "latest_publish": "2026-09-25 10:00", "article_urls": [], "heat_reason": "2家媒体报道"}], "2026-09-25")
            response = client.get("/api/heat/topics?date=2026-09-25")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["data"]["topics"][0]["cluster_id"], "api-topic")
            # terms 是前端标签行的数据源，别在接口层丢掉
            self.assertIn("terms", response.json()["data"]["topics"][0])
            dates = client.get("/api/heat/dates")
            self.assertEqual(dates.status_code, 200)
            self.assertEqual(dates.json()["data"]["dates"], ["2026-09-25"])
            detail = client.get("/api/heat/topics/api-topic")
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(detail.json()["data"]["title"], "API热频话题")
        finally:
            client.close()
            app.state.database.close()


    def test_government_sources_pass_the_gate_and_score_as_official(self) -> None:
        """政务源必须能聚簇，且按一手官方计权。

        这条是 heat_topics 长期 0 行的根因：闸门原本是
        「没有 media_type 且 source_type != media 就跳过」，而本项目 145/145 个源
        都是 government，于是整库被关在门外——链条接上了也跑不出东西。
        """
        now = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)
        config = {"media_heat": {"weights": {"official": 3, "mainstream": 2, "industry": 1},
                                 "category_weights": {"policy": 2, "livelihood": 1.5, "industry": 1},
                                 "time_decay": {"hours_24": 1, "hours_48": .5, "older": .2},
                                 "policy_keywords": ["政策", "通知"],
                                 "livelihood_keywords": ["补贴"],
                                 "industry_keywords": ["芯片", "新能源"],
                                 "exclude_keywords": ["娱乐"],
                                 "cluster_window_hours": 72}}
        records = [
            {"id": 1, "source_id": "zj_gov", "source_type": "government", "issuing_body": "浙江省人民政府",
             "source_url": "https://zj.gov.cn/1", "title": "关于新能源产业扶持政策的通知",
             "publish_date": "2026-09-25T08:00:00+00:00", "category_l1": "行业政策"},
            {"id": 2, "source_id": "cq_gov", "source_type": "government", "issuing_body": "重庆市能源局",
             "source_url": "https://cq.gov.cn/2", "title": "新能源产业扶持政策通知",
             "publish_date": "2026-09-25T09:00:00+00:00", "category_l1": "行业政策"},
        ]
        topics = cluster_media_records(records, config, now=now)
        self.assertEqual(len(topics), 1, "政务源被闸门挡掉了，heat_topics 就永远是空的")
        self.assertEqual(topics[0]["source_count"], 2)
        self.assertTrue(all(s["media_type"] == "official" for s in topics[0]["sources"]),
                        "政务源应判为 official，否则拿不到一手权重 3.0")
        # calculate_heat = Σ(源权重) × 独立来源数 × 时间衰减 × 分类权重
        # = (3+3) × 2 × 1.0(24h 内) × 2.0(policy) = 24
        self.assertEqual(topics[0]["heat_score"], 24.0)
        self.assertIn("2个官方一手", topics[0]["heat_reason"])

    def test_records_without_any_source_type_are_still_skipped(self) -> None:
        """闸门只放开政务源。既无 media_type 又不是媒体/政务来源的记录仍要跳过。"""
        now = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)
        config = {"media_heat": {"weights": {"official": 3, "industry": 1},
                                 "policy_keywords": ["政策"], "livelihood_keywords": [],
                                 "industry_keywords": ["芯片"], "exclude_keywords": [],
                                 "cluster_window_hours": 72}}
        records = [{"id": 1, "source_id": "unknown", "source_url": "https://x/1",
                    "title": "芯片政策", "publish_date": "2026-09-25T08:00:00+00:00",
                    "category_l1": "行业政策"}]
        self.assertEqual(cluster_media_records(records, config, now=now), [])


    def test_heat_trend_aggregates_by_report_date(self) -> None:
        settings = Settings(database_path=":memory:", environment="development", jwt_secret="heat-test-secret-012345678901234567890123")
        app = create_app(db_path=":memory:", settings=settings)
        client = TestClient(app)
        try:
            database = app.state.database
            database.upsert_heat_topics([{"cluster_id": "t1", "title": "话题一", "heat_score": 10.0, "source_count": 1}], "2026-09-24")
            database.upsert_heat_topics(
                [
                    {"cluster_id": "t2", "title": "话题二", "heat_score": 15.5, "source_count": 1},
                    {"cluster_id": "t3", "title": "话题三", "heat_score": 4.5, "source_count": 1},
                ],
                "2026-09-25",
            )
            response = client.get("/api/heat/trend")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.json()["data"]["trend"],
                [
                    {"date": "2026-09-24", "count": 1, "heat": 10.0},
                    {"date": "2026-09-25", "count": 2, "heat": 20.0},
                ],
            )
        finally:
            client.close()
            app.state.database.close()


if __name__ == "__main__":
    unittest.main()
