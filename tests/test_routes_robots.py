"""路由阶梯的 robots 行为测试。

**规则已在 2026-09-30 变更**（AGENTS.md §1，项目负责人决策）：

  原文：robots 拒绝是终态，不绕过。
  现文：静态优先 —— robots 拒绝且静态拿不到时，升级到 render / markdown
        再试一次（渲染浏览器能拿到静态请求拿不到的 412/WAF/JS 内容）。
        每次覆盖留痕，robots 不再是终态。

  业务原因：重庆不能没有数据。cq_ny 历史上入库 55 条，按原规则归零，
        cq_ny/cq_kjt/cq_gov/cq_fgw 四个源全部采不到。

**仍然必须拦住的是本地白名单**（`_is_allowed_url`）：那是我们的配置边界，
绕它等于越权采集，与站方 robots 无关。

另外两类行为与 robots 无关，保持原样：非 robots 的错误要升级（换更重的
路由是正当提质手段），门禁判死也要升级。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lighthouse.routes import Ladder


def _cfg():
    return {
        "routes": {
            "levels": [
                {"name": "static", "enabled": True},
                {"name": "render", "enabled": True},
                {"name": "markdown", "enabled": True},
            ]
        },
        "quality": {},
    }


class RobotsEscalationTests(unittest.TestCase):
    """robots 拒绝 → 升级到渲染（2026-09-30 新规则）。"""

    def test_robots_denial_escalates_to_render(self):
        cfg = _cfg()
        ladder = Ladder(cfg, None)
        called = []

        def fake_run_level(name, level, url, detail):
            called.append(name)
            if name == "static":
                raise PermissionError(
                    "robots.txt access denied: https://kjj.cq.gov.cn")
            return ("正文" * 400, "标题", "", [])

        ladder._run_level = fake_run_level
        detail = ladder.fetch("https://kjj.cq.gov.cn/ztzl_176/")

        self.assertEqual(called, ["static", "render"],
                         "robots 拒绝后应升级到 render，实际 %s" % called)
        self.assertTrue(detail.ok, "渲染兜底应当拿到内容")
        self.assertEqual(detail.route_used, "render")

    def test_robots_escalation_is_recorded(self):
        """覆盖 robots 必须留痕，否则事后无法统计/回溯。"""
        cfg = _cfg()
        ladder = Ladder(cfg, None)

        def fake_run_level(name, level, url, detail):
            if name == "static":
                raise PermissionError(
                    "robots.txt access denied: https://nyncw.cq.gov.cn")
            return ("正文" * 400, "标题", "", [])

        ladder._run_level = fake_run_level
        detail = ladder.fetch("https://nyncw.cq.gov.cn/wsdw/")

        escalated = [a for a in detail.attempts if a.get("robots_escalated")]
        self.assertTrue(escalated, "attempts 里必须有 robots_escalated 标记：%s"
                        % detail.attempts)
        self.assertIn("robots_denied", escalated[0]["reason"])

    def test_robots_denial_with_no_fallback_is_dropped(self):
        """升级也拿不到 → 正常丢弃，理由里要能看到 robots 痕迹。"""
        cfg = _cfg()
        ladder = Ladder(cfg, None)

        def fake_run_level(name, level, url, detail):
            if name == "static":
                raise PermissionError(
                    "robots.txt access denied: https://ndrc.gov.cn")
            return None

        ladder._run_level = fake_run_level
        detail = ladder.fetch("https://ndrc.gov.cn/x.html")

        self.assertFalse(detail.ok)
        self.assertIn("robots_denied", detail.dropped_reason,
                      "dropped_reason=%r" % detail.dropped_reason)


class AllowlistIsTerminalTests(unittest.TestCase):
    """本地白名单拒绝是终态 —— 与站方 robots 无关，绕它等于越权采集。"""

    def test_allowlist_denial_stops_the_ladder(self):
        cfg = _cfg()
        ladder = Ladder(cfg, None)
        called = []

        def fake_run_level(name, level, url, detail):
            called.append(name)
            if name == "static":
                raise PermissionError("URL outside source allowlist: https://x.gov.cn/a")
            return ("正文" * 400, "标题", "", [])

        ladder._run_level = fake_run_level
        detail = ladder.fetch("https://x.gov.cn/a")

        self.assertEqual(called, ["static"],
                         "白名单拒绝后还在升级：%s" % called)
        self.assertFalse(detail.ok)
        self.assertIn("allowlist_denied", detail.dropped_reason,
                      "dropped_reason=%r" % detail.dropped_reason)


class UnrelatedEscalationTests(unittest.TestCase):
    """与 robots 无关的升级行为，保持原样。"""

    def test_other_errors_still_escalate(self):
        cfg = _cfg()
        ladder = Ladder(cfg, None)
        called = []

        def fake_run_level(name, level, url, detail):
            called.append(name)
            if name == "static":
                return None                      # 静态没拿到
            if name == "render":
                raise RuntimeError("browser crashed")
            return ("正文" * 400, "标题", "", [])

        ladder._run_level = fake_run_level
        detail = ladder.fetch("https://example.gov.cn/a.html")
        self.assertEqual(called, ["static", "render", "markdown"])
        self.assertTrue(detail.ok)
        self.assertEqual(detail.route_used, "markdown")

    def test_gate_rejection_still_escalates(self):
        """门禁判死（too_short 等）也要继续升级，不能当终止。"""
        cfg = _cfg()
        ladder = Ladder(cfg, None)
        called = []

        def fake_run_level(name, level, url, detail):
            called.append(name)
            if name == "static":
                return ("短", "标题", "", [])
            return ("正文" * 400, "标题", "", [])

        ladder._run_level = fake_run_level
        detail = ladder.fetch("https://example.gov.cn/a.html")
        self.assertIn("render", called)
        self.assertTrue(detail.ok)


if __name__ == "__main__":
    unittest.main()
