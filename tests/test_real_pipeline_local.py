from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import yaml
from fastapi.testclient import TestClient

from api.config import Settings
from api.main import create_app
from graph_service import graph_summary
from storage.db import Database

ROOT = Path(__file__).resolve().parents[1]
# 通宵采集常驻时生产采集锁会被真实持有，这个集成测试就会因为一个与它无关的
# 原因变红。让 main.py 取自己的临时锁，测试在采集运行时也能验证真实行为。
_TEST_LOCK = Path(tempfile.gettempdir()) / "beacon_test_collect_local.lock"


class FixtureHandler(BaseHTTPRequestHandler):
    detail_pages: dict[str, str] = {}

    def do_GET(self) -> None:
        if self.path == "/robots.txt":
            body = b"User-agent: *\nAllow: /\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/list":
            body = "<html><body><main class='notice-list'><a href='/sc/notice/1'>四川省市场监督管理局通知</a><a href='/sc/notice/2'>四川省发展和改革委员会公告</a></main></body></html>".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path in self.detail_pages:
            body = self.detail_pages[self.path].encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, _format: str, *_args: Any) -> None:
        return


class LocalPipelineTests(unittest.TestCase):
    def test_real_cli_collects_extracts_stores_matches_and_serves_api(self) -> None:
        FixtureHandler.detail_pages = {
            "/sc/notice/1": "<html><head><title>四川省市场监督管理局通知</title><meta name='publish_date' content='2026-02-20'></head><body><main><h1>四川省市场监督管理局通知</h1><p>面向企业提供行业政策支持，金额为100万元，申报截止日期为2026-04-30。</p><p>四川省市场监督管理局负责本通知的发布和解释。</p></main></body></html>",
            "/sc/notice/2": "<html><head><title>四川省发展和改革委员会公告</title><meta name='publish_date' content='2026-02-19'></head><body><main><h1>四川省发展和改革委员会公告</h1><p>面向企业发布项目申报信息，金额为200万元，申报截止日期为2026-05-01。</p><p>四川省发展和改革委员会负责本公告的发布。</p></main></body></html>",
        }
        server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config_path = root / "sources.yaml"
                schemas_path = root / "schemas.yaml"
                db_path = root / "beacon.db"
                output_path = root / "records.json"
                markdown_path = root / "markdown"
                list_url = f"http://127.0.0.1:{server.server_port}/list"
                config = {
                    "version": 1,
                    "default_request_interval_seconds": 0,
                    "collector_engine": {"default_render_mode": "static"},
                    "cadence_tiers": {"daily": {"interval_seconds": 86400}},
                    "retention_policies": {"active": {"days": 365}},
                    "sources": [{"id": "local_fixture", "name": "四川省市场监督管理局", "domain": "127.0.0.1", "url": list_url, "allowed_domains": ["127.0.0.1"], "level": "national", "gov_level": "省级", "jurisdiction_code": "510000", "region_province": "四川省", "region_city": "成都市", "category_l1": "行业政策", "category_l2": "政策通知", "target_group": "企业", "cadence_tier": "daily", "retention_class": "active", "status": "enabled", "enabled": True, "access_method": "static", "render_mode": "static", "check_robots": True, "list_selector": ".notice-list", "link_selector": "a[href]", "detail_pattern": r"/sc/notice/\d+", "minimum_content_chars": 40, "request_interval_seconds": 0}],
                }
                config_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
                schemas_path.write_text("schemas: {}\n", encoding="utf-8")
                command = [sys.executable, str(ROOT / "main.py"), "--config", str(config_path), "--crawl4ai-schemas", str(schemas_path), "--db", str(db_path), "--source", "local_fixture", "--force", "--limit", "2", "--user-region", "四川省", "--user-level", "省级", "--user-category", "行业政策", "--target-group", "企业", "--output", str(output_path), "--markdown-output", str(markdown_path)]
                completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=90, check=False,
                   env=dict(os.environ, BEACON_COLLECT_LOCK=str(_TEST_LOCK)))
                self.assertEqual(completed.returncode, 0, completed.stderr)
                result = json.loads(completed.stdout)
                self.assertEqual(len(result["records"]), 2)
                self.assertEqual(result["storage"]["inserted"], 2)
                self.assertGreater(result["graph"]["nodes"], 0)
                self.assertTrue(result["matches"])
                self.assertEqual(result["markdown"]["files"], 2)
                self.assertTrue(output_path.exists())
                self.assertTrue((markdown_path / "index.md").exists())
                with Database(str(db_path)) as database:
                    self.assertEqual(len(database.list_announcements(limit=10)), 2)
                    self.assertGreater(graph_summary(database)["edges"], 0)
                settings = Settings(database_path=str(db_path), environment="development", jwt_secret="local-test-secret-012345678901234567890123")
                app = create_app(db_path=str(db_path), settings=settings)
                client = TestClient(app)
                try:
                    self.assertEqual(client.get("/api/announcements?gov_level=省级&category=行业政策").status_code, 200)
                    self.assertEqual(client.get("/api/graph/summary").json()["data"]["nodes"] > 0, True)
                finally:
                    client.close()
                    app.state.database.close()
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
