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
