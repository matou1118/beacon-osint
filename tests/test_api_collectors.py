from __future__ import annotations

import unittest

from collectors.api.base import ApiCollector


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.status_code = 200

    def json(self) -> dict:
        return self.payload


class FakeClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.requests: list[tuple[str, str]] = []

    def request(self, method: str, url: str, **kwargs: object) -> FakeResponse:
        self.requests.append((method, url))
        return FakeResponse(self.payload)

    def close(self) -> None:
        return None


class ApiCollectorTests(unittest.TestCase):
    def test_api_collector_normalizes_items(self) -> None:
        client = FakeClient(
            {
                "items": [
                    {
                        "title": "公共数据公告",
                        "date": "2026-01-02",
                        "url": "https://data.example.gov.cn/item/1",
                        "content": "公告正文",
                    }
                ]
            }
        )
        source = {
            "id": "fixture_api",
            "name": "测试公共数据平台",
            "domain": "data.example.gov.cn",
            "url": "https://data.example.gov.cn/",
            "list_endpoint": "https://data.example.gov.cn/api/items",
            "check_robots": False,
            "items_key": "items",
            "gov_level": "省级",
        }
        records = ApiCollector(source, client=client).fetch_list()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["title"], "公共数据公告")
        self.assertRegex(records[0]["content_hash"], r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
