from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]


class ModeHandler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, content_type: str = "text/html; charset=utf-8") -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        host = self.headers.get("Host", "127.0.0.1")
        base = f"http://{host}"
        if self.path == "/robots.txt":
            self._send(b"User-agent: *\nAllow: /\n", "text/plain; charset=utf-8")
        elif self.path == "/api/items":
            self._send(json.dumps({"items": [{"title": "学院招生政策", "date": "2026-03-01", "url": f"{base}/api/item/1", "content": "学院面向考生发布招生政策与录取安排。" }]}, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
        elif self.path == "/rss":
            self._send(f"<rss><channel><item><title>民生补贴通知</title><link>{base}/rss/article/1</link><pubDate>2026-03-02</pubDate></item></channel></rss>".encode("utf-8"), "application/rss+xml; charset=utf-8")
        elif self.path == "/rss/article/1":
            self._send("<html><head><title>民生补贴通知</title><meta name='publish_date' content='2026-03-02'></head><body><main><h1>民生补贴通知</h1><p>面向企业职工发布养老和社区补贴政策，金额为10万元，截止日期为2026-06-01。</p><p>本通知用于测试RSS采集和规则抽取链路。</p></main></body></html>".encode("utf-8"))
        elif self.path == "/web/list":
            self._send("<html><body><main class='notice-list'><a href='/web/article/1'>部门政策通知</a></main></body></html>".encode("utf-8"))
        elif self.path == "/web/article/1":
            self._send("<html><head><title>部门政策通知</title><meta name='publish_date' content='2026-03-03'></head><body><main><h1>部门政策通知</h1><p>面向企业提供行业政策支持，金额为50万元，截止日期为2026-07-01。</p><p>本通知用于测试静态网页采集和规则抽取链路。</p></main></body></html>".encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, _format: str, *_args: Any) -> None:
        return


class RealPipelineModeTests(unittest.TestCase):
    def test_api_rss_and_web_run_through_one_main_process(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), ModeHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config_path = root / "sources.yaml"
                schemas_path = root / "schemas.yaml"
                db_path = root / "beacon.db"
                markdown_path = root / "markdown"
                port = server.server_port
                base = f"http://127.0.0.1:{port}"
                sources = [
                    {"id": "api_fixture", "name": "学院 API", "domain": "127.0.0.1", "url": f"{base}/api/items", "list_endpoint": f"{base}/api/items", "access_method": "api", "api_kind": "education", "level": "national", "gov_level": "国家级", "category_l1": "招生信息", "target_group": "考生", "cadence_tier": "daily", "retention_class": "active", "status": "enabled", "enabled": True, "check_robots": True, "request_interval_seconds": 0},
                    {"id": "rss_fixture", "name": "民生 RSS", "domain": "127.0.0.1", "url": f"{base}/rss", "rss_url": f"{base}/rss", "access_method": "rss", "level": "national", "gov_level": "国家级", "category_l1": "养老与社区", "target_group": "企业", "cadence_tier": "daily", "retention_class": "active", "status": "enabled", "enabled": True, "check_robots": True, "request_interval_seconds": 0},
                    {"id": "web_fixture", "name": "部门网页", "domain": "127.0.0.1", "url": f"{base}/web/list", "access_method": "static_html", "level": "national", "gov_level": "国家级", "category_l1": "行业政策", "target_group": "企业", "cadence_tier": "daily", "retention_class": "active", "status": "enabled", "enabled": True, "check_robots": True, "list_selector": ".notice-list", "link_selector": "a[href]", "detail_pattern": r"/web/article/\d+", "minimum_content_chars": 40, "request_interval_seconds": 0},
                ]
                config = {"version": 1, "default_request_interval_seconds": 0, "collector_engine": {"default_render_mode": "static"}, "cadence_tiers": {"daily": {"interval_seconds": 86400}}, "retention_policies": {"active": {"days": 365}}, "sources": sources}
                config_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
                schemas_path.write_text("schemas: {}\n", encoding="utf-8")
                command = [sys.executable, str(ROOT / "main.py"), "--config", str(config_path), "--crawl4ai-schemas", str(schemas_path), "--db", str(db_path), "--force", "--limit", "1", "--markdown-output", str(markdown_path)]
                for source_id in ("api_fixture", "rss_fixture", "web_fixture"):
                    command.extend(["--source", source_id])
                completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=90, check=False)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                result = json.loads(completed.stdout)
                self.assertEqual(len(result["records"]), 3)
                self.assertEqual(result["storage"]["inserted"], 3)
                self.assertEqual(result["markdown"]["files"], 3)
                self.assertTrue(all(not item["errors"] for item in result["sources"]), result)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
