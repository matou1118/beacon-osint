from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from api.config import Settings
from api.main import create_app
from api.services.user_service import UserService
from graph_service import rebuild_graph


class CapturingSmsProvider:
    def __init__(self) -> None:
        self.codes: list[tuple[str, str]] = []

    def send_code(self, phone: str, code: str) -> None:
        self.codes.append((phone, code))


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(database_path=":memory:", environment="development", jwt_secret="test-secret-012345678901234567890123")
        self.app = create_app(db_path=":memory:", settings=self.settings)
        self.client = TestClient(self.app)
        self.database = self.app.state.database
        self.database.upsert_announcements(
            [
                {
                    "source_id": "sc_hr",
                    "source_url": "https://example.gov.cn/notice/1",
                    "title": "青年人才租房补贴通知",
                    "publish_date": "2026-02-20",
                    "content": "面向应届毕业生提供租房补贴。",
                    "issuing_body": "四川省人社厅",
                    "gov_level": "省级",
                    "region_province": "四川省",
                    "category_l1": "行业补贴",
                    "category_l2": "人才补贴",
                    "target_group": "应届毕业生",
                    "key_points": ["补贴对象明确"],
                },
                {
                    "source_id": "sc_trade",
                    "source_url": "https://example.gov.cn/notice/2",
                    "title": "企业招聘服务通知",
                    "publish_date": "2026-02-19",
                    "content": "面向企业提供招聘服务。",
                    "issuing_body": "四川省商务厅",
                    "gov_level": "省级",
                    "region_province": "四川省",
                    "category_l1": "招聘信息",
                    "target_group": "企业",
                },
            ]
        )

    def tearDown(self) -> None:
        self.client.close()

    def test_announcement_list_detail_and_filters(self) -> None:
        response = self.client.get("/api/announcements?gov_level=省级&category=行业补贴&page=1&page_size=20")
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["total"], 1)
        item = data["items"][0]
        self.assertEqual(item["category_l1"], "行业补贴")
        self.assertIn("match_score", item)
        detail = self.client.get(f"/api/announcements/{item['id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("raw_text", detail.json()["data"])

    def test_keyword_search_filters_and_scores(self) -> None:
        # 关键词必须真正缩小结果集：从前只要求命中评分，搜索页拿到的是全库。
        hit = self.client.get("/api/announcements", params={"keyword": "租房"}).json()["data"]
        self.assertEqual(hit["total"], 1)
        item = hit["items"][0]
        self.assertIn("租房", item["title"])
        self.assertGreaterEqual(item["match_score"], 10)
        self.assertTrue(any("关键词匹配" in reason for reason in item["match_reasons"]))

        miss = self.client.get("/api/announcements", params={"keyword": "zzzz-not-exist"}).json()["data"]
        self.assertEqual(miss["total"], 0)

        # 空白分词：任一词命中即保留
        either = self.client.get("/api/announcements", params={"keyword": "租房 招聘"}).json()["data"]
        self.assertEqual(either["total"], 2)

    def test_target_group_filters_the_result_set(self) -> None:
        # 人群/行业原来只进评分 profile，检索页勾了等于没勾
        group = self.client.get("/api/announcements", params={"target_group": "企业"}).json()["data"]
        self.assertEqual(group["total"], 1)
        self.assertEqual(group["items"][0]["target_group"], "企业")
        miss = self.client.get("/api/announcements", params={"target_group": "不存在的人群"}).json()["data"]
        self.assertEqual(miss["total"], 0)

    def test_category_l2_filters_by_industry(self) -> None:
        # 检索页「行业门类」筛的是 category_l2 —— 全库 target_group/target_industry 都是空的
        hit = self.client.get("/api/announcements", params={"category_l2": "人才补贴"}).json()["data"]
        self.assertEqual(hit["total"], 1)
        self.assertEqual(hit["items"][0]["category_l2"], "人才补贴")
        miss = self.client.get("/api/announcements", params={"category_l2": "不存在的门类"}).json()["data"]
        self.assertEqual(miss["total"], 0)

    def test_stats_aggregate_follows_the_same_filters(self) -> None:
        # 检索页图表要的是全量汇总 —— 只翻一页画不出分布，且必须和列表同一套筛选
        data = self.client.get("/api/announcements/stats").json()["data"]
        self.assertEqual(data["total"], 2)
        self.assertEqual({row["name"] for row in data["category_l1"]}, {"行业补贴", "招聘信息"})
        self.assertEqual({row["name"] for row in data["province"]}, {"四川省"})
        self.assertEqual(data["month"], [{"name": "2026-02", "value": 2}])

        filtered = self.client.get("/api/announcements/stats", params={"category": "行业补贴"}).json()["data"]
        self.assertEqual(filtered["total"], 1)

        keyword = self.client.get("/api/announcements/stats", params={"keyword": "租房"}).json()["data"]
        self.assertEqual(keyword["total"], 1)
        self.assertEqual(keyword["gov_level"], [{"name": "省级", "value": 1}])

    def test_list_paginates_in_sql(self) -> None:
        # 无关键词路径走 COUNT+LIMIT/OFFSET —— 翻页不能全库物化
        first = self.client.get("/api/announcements", params={"page": 1, "page_size": 1}).json()["data"]
        second = self.client.get("/api/announcements", params={"page": 2, "page_size": 1}).json()["data"]
        self.assertEqual(first["total"], 2)
        self.assertEqual(second["total"], 2)
        self.assertNotEqual(first["items"][0]["id"], second["items"][0]["id"])
        self.assertGreaterEqual(first["items"][0]["publish_date"], second["items"][0]["publish_date"])
        beyond = self.client.get("/api/announcements", params={"page": 99, "page_size": 1}).json()["data"]
        self.assertEqual(beyond["items"], [])
        self.assertEqual(beyond["total"], 2)

    def test_export_returns_csv_for_the_current_filters(self) -> None:
        # 导出必须和列表同一套筛选、全量不分页；无 BOM 的 UTF-8 CSV 在 Excel 里是乱码
        response = self.client.get("/api/announcements/export")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/csv"))
        self.assertIn("attachment", response.headers["content-disposition"])
        lines = response.text.splitlines()
        self.assertTrue(lines[0].startswith("\ufeff"), "UTF-8 CSV 要带 BOM")
        self.assertIn("标题", lines[0])
        self.assertEqual(len(lines), 3)  # 表头 + 2 条

        # /export 声明在 /{announcement_id} 之前：筛选口径与列表一致
        filtered = self.client.get("/api/announcements/export", params={"category": "行业补贴"})
        self.assertEqual(len(filtered.text.splitlines()), 2)

    def test_stats_cache_serves_the_same_window(self) -> None:
        from api.services import announcement_service as svc

        svc._stats_cache.clear()
        original_ttl = svc._STATS_TTL
        svc._STATS_TTL = 3600.0
        try:
            first = self.client.get("/api/announcements/stats").json()["data"]
            self.assertEqual(first["total"], 2)

            self.database.upsert_announcements(
                [
                    {
                        "source_id": "sc_cache",
                        "source_url": "https://example.gov.cn/notice/3",
                        "title": "缓存窗口内新增的公告",
                        "publish_date": "2026-02-21",
                        "content": "用于验证 TTL 窗口内不重算",
                        "issuing_body": "某省厅",
                        "gov_level": "省级",
                        "region_province": "四川省",
                        "category_l1": "政策发布",
                    }
                ]
            )

            cached = self.client.get("/api/announcements/stats").json()["data"]
            self.assertEqual(cached["total"], 2, "TTL 窗口内应复用缓存，而不是重算")

            svc._STATS_TTL = 0.0
            fresh = self.client.get("/api/announcements/stats").json()["data"]
            self.assertEqual(fresh["total"], 3, "关闭缓存后应看到新数据")
        finally:
            svc._STATS_TTL = original_ttl
            svc._stats_cache.clear()

    def test_categories_and_regions(self) -> None:
        categories = self.client.get("/api/categories").json()["data"]
        self.assertIn("行业补贴", categories["l1"])
        self.assertIn("高职单招", categories["admission_sub"])
        regions = self.client.get("/api/regions").json()["data"]
        self.assertIn("四川省", regions["provinces"])

    def test_graph_summary_and_neighbors(self) -> None:
        rebuild_graph(self.database)
        summary = self.client.get("/api/graph/summary")
        self.assertEqual(summary.status_code, 200)
        self.assertGreater(summary.json()["data"]["nodes"], 0)
        detail = self.client.get("/api/graph/nodes/announcement:1")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("neighbors", detail.json()["data"])

    def test_subscription_lifecycle_requires_local_user_header(self) -> None:
        headers = {"X-User-Id": "u_test"}
        created = self.client.post("/api/subscriptions", headers=headers, json={"gov_levels": ["省级"], "region": "四川省", "categories": ["行业补贴"], "push_frequency": "daily"})
        self.assertEqual(created.status_code, 200)
        subscription_id = created.json()["data"]["id"]
        listed = self.client.get("/api/subscriptions", headers=headers)
        self.assertEqual(len(listed.json()["data"]), 1)
        updated = self.client.put(f"/api/subscriptions/{subscription_id}/push", headers=headers, json={"push_frequency": "weekly", "enabled": True})
        self.assertEqual(updated.json()["data"]["push_frequency"], "weekly")
        deleted = self.client.delete(f"/api/subscriptions/{subscription_id}", headers=headers)
        self.assertTrue(deleted.json()["data"]["deleted"])

    def test_feedback_is_upserted(self) -> None:
        payload = {"announcement_id": 1, "useful": True, "comment": "有帮助"}
        headers = {"X-User-Id": "u_test"}
        first = self.client.post("/api/feedback", headers=headers, json=payload)
        second = self.client.post("/api/feedback", headers=headers, json={**payload, "useful": False})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.json()["data"]["useful"])

    def test_disabled_sms_and_wechat_provider_are_explicit(self) -> None:
        self.assertEqual(self.client.post("/api/auth/send-code", json={"phone": "13800138000"}).status_code, 503)
        self.assertEqual(self.client.post("/api/auth/wechat", json={"code": "temporary"}).status_code, 501)

    def test_sms_login_issues_jwt_when_provider_is_injected(self) -> None:
        provider = CapturingSmsProvider()
        self.app.state.user_service.sms_provider = provider
        sent = self.client.post("/api/auth/send-code", json={"phone": "13800138000"})
        self.assertEqual(sent.status_code, 200)
        phone, code = provider.codes[-1]
        logged_in = self.client.post("/api/auth/login", json={"phone": phone, "code": code})
        self.assertEqual(logged_in.status_code, 200)
        token = logged_in.json()["data"]["token"]
        subscriptions = self.client.get("/api/subscriptions", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(subscriptions.status_code, 200)

    def test_user_service_never_stores_raw_phone_in_user_id(self) -> None:
        service = UserService(self.settings, CapturingSmsProvider())
        user_id = service.user_id_for_phone("13800138000")
        self.assertTrue(user_id.startswith("u_"))
        self.assertNotIn("13800138000", user_id)


if __name__ == "__main__":
    unittest.main()
