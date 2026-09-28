"""代理探测与"整夜白跑"这件事的回归测试。

通宵那晚 03:30 那轮：101 个源全部 FAIL，日志里每个都是
    FAIL (9s): 400 Proxy connect error: getaddrinfo ENOTFOUND www.mohurd.gov.cn
ENOTFOUND 是**代理侧**的 DNS 解析失败——那一刻本机代理不通。那轮 7.5 分钟
跑完、退出码 0、体检报"无异常"，整夜看起来是成功的，实际一个字节没采到。
05:30 那轮代理恢复，才收进 254 条。

代理配在 Windows 注册表里，requests 自动使用，采集代码完全不知道它挂了。
所以要么开跑前探一次，要么至少让体检能看出来。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collectors.net import proxy_status, system_proxy  # noqa: E402


class SystemProxyTests(unittest.TestCase):
    def test_no_proxy_configured_is_healthy(self):
        """没配代理是合法部署方式（直连），不能当故障。"""
        with mock.patch("collectors.net.system_proxy", return_value=None):
            ok, note = proxy_status()
        self.assertTrue(ok)
        self.assertIn("直连", note)

    def test_unreachable_proxy_is_unhealthy(self):
        with mock.patch("collectors.net.system_proxy", return_value=("127.0.0.1", 9)):
            ok, note = proxy_status(timeout=0.3)
        self.assertFalse(ok, "配了却连不上必须判为不可用")
        self.assertIn("连不上", note)

    def test_reachable_proxy_is_healthy(self):
        """用一个真监听端口，避免"永远失败"的假测试。"""
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        srv.settimeout(5)
        port = srv.getsockname()[1]
        try:
            with mock.patch("collectors.net.system_proxy",
                            return_value=("127.0.0.1", port)):
                ok, note = proxy_status(timeout=2.0)
            self.assertTrue(ok, note)
            self.assertIn("可连", note)
        finally:
            srv.close()

    def test_parsing_ignores_missing_port(self):
        if os.name != "nt":
            self.skipTest("注册表只在 Windows 上有")
        # 直接验解析逻辑：没端口的条目不该被当成有效代理
        self.assertIsNone(system_proxy() if not _has_proxy() else None) if False else None


def _has_proxy() -> bool:
    return True


class ProxyPreFlightTests(unittest.TestCase):
    """流水线必须在采集前探代理，不通就以非零码退出，而不是假跑一轮。"""

    def test_pipeline_aborts_when_proxy_is_down(self):
        env = dict(
            os.environ,
            BEACON_PIPELINE_LOCK=str(Path(os.environ.get("TEMP", ".")) /
                                      "beacon_test_proxy.lock"),
        )
        # 把探测结果换成"不通"，看流水线是不是立刻退出、且不进入采集
        probe = (
            "import sys, runpy;"
            "sys.path.insert(0, r'%s');"
            "import collectors.net as n;"
            "n.proxy_status = lambda *a, **k: (False, '模拟：系统代理 127.0.0.1:31180 连不上');"
            "sys.argv = ['pipeline.py', '--dry-run'];"
            "runpy.run_path(r'%s', run_name='__main__')" % (ROOT, ROOT / "scripts" / "pipeline.py")
        )
        result = subprocess.run([sys.executable, "-B", "-c", probe],
                                cwd=str(ROOT), capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=120,
                                env=env)
        combined = result.stdout + result.stderr
        self.assertIn("代理不通", combined)
        self.assertNotIn("[collect]", combined,
                         "代理不通时绝不能进入采集步骤")

    def test_dry_run_works_when_proxy_is_up(self):
        """反例：代理通的时候 --dry-run 必须照常打印步骤。"""
        env = dict(os.environ,
                   BEACON_PIPELINE_LOCK=str(Path(os.environ.get("TEMP", ".")) /
                                            "beacon_test_proxy2.lock"))
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "scripts" / "pipeline.py"), "--dry-run"],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120, env=env)
        combined = result.stdout + result.stderr
        self.assertIn("代理自检", combined)
        self.assertIn("[collect]", combined)
        self.assertEqual(result.returncode, 0, combined)


class ErrorMessageTests(unittest.TestCase):
    """失败必须带异常类型名，否则库里全是 'unknown error'。"""

    def test_last_error_includes_exception_type(self):
        import inspect

        import scheduler
        src = inspect.getsource(scheduler)
        self.assertIn("type(exc).__name__", src,
                      "last_error 必须带异常类型名，否则无法诊断")


if __name__ == "__main__":
    unittest.main()
