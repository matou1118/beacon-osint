"""流水线必须自己独占跑完一整轮。

通宵那晚真的撞上过：两个 pipeline.py 并行，一个在 collect、另一个已经跑到
dedup——prune_section_pages 和 dedup 都会 DELETE，一边插入一边删除同一个库。

两个成因，都在这里固定住：
  1. pipeline.py 原来只调 clear_stale_lock()，检测到"另一轮在跑"之后照样继续。
  2. 锁加错了形式：collect_lock 是上下文管理器类，没有 .acquire 方法，写成
     PIPELINE_LOCK.acquire(...) 会 AttributeError 直接崩。这个 bug 是靠通宵任务
     的日志才发现的（Traceback 打进了 data/overnight_*.log），所以必须有测试。

测试用 BEACON_PIPELINE_LOCK 指向临时路径。原因：通宵采集常驻时生产锁会被真实
持有，测试若也用生产路径就必然失败——之前这 5 个测试在采集运行时全红，而且红的
恰恰是"锁被占时必须拒绝启动"那条最该跑的断言。指向自己的路径后，测试在采集
运行时仍能验证真实行为。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline.py"
LOCK_DIR = Path(tempfile.gettempdir()) / "beacon_test_pipeline.lock"


def _run_pipeline(*extra: str) -> subprocess.CompletedProcess:
    # 子进程输出编码必须**显式**钉死，不能靠继承环境。
    #
    # 2026-10-05 实测踩过：这里 decoding 已经写了 encoding="utf-8"，但子进程
    # 实际按 GBK（系统 ANSI 代码页）输出，于是每个中文字符都变成 U+FFFD，
    # 断言「将执行」「已有一轮流水线在跑」全部找不到 —— 报错信息里满屏
    # \ufffd，看着像脚本坏了，其实是编码对不上。
    #
    # 为什么之前时好时坏：子进程用哪个编码取决于它继承到的
    # PYTHONUTF8 / PYTHONIOENCODING / 系统代码页，凡是不由本测试控制。
    # 所以两个方向都要钉：子进程 PYTHONIOENCODING=utf-8，本进程按 utf-8 解。
    env = dict(os.environ,
               BEACON_PIPELINE_LOCK=str(LOCK_DIR),
               PYTHONUTF8="1",
               PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, "-B", str(PIPELINE), *extra],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=180, env=env,
    )


class PipelineLockTests(unittest.TestCase):
    def setUp(self) -> None:
        shutil.rmtree(LOCK_DIR, ignore_errors=True)

    def tearDown(self) -> None:
        shutil.rmtree(LOCK_DIR, ignore_errors=True)

    def test_dry_run_works_and_leaves_no_lock(self):
        """--dry-run 也走加锁路径，必须能正常返回且不留下锁。"""
        result = _run_pipeline("--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("将执行", result.stdout)
        self.assertFalse(LOCK_DIR.exists(), "dry-run 之后应释放锁")

    def test_dry_run_includes_amounts_step(self):
        """金额抽取必须在 summary 之前——摘要头部的事实行要读 amounts 表。"""
        result = _run_pipeline("--dry-run")
        self.assertIn("[amounts]", result.stdout)
        self.assertLess(result.stdout.index("[amounts]"), result.stdout.index("[summary]"))

    def test_drain_flag_is_passed_to_collect(self):
        result = _run_pipeline("--dry-run", "--drain", "--rounds", "7", "--stop-when-dry", "2")
        self.assertIn("--rounds 7", result.stdout)
        self.assertIn("--stop-when-dry 2", result.stdout)

    def test_refuses_to_start_while_another_pipeline_holds_the_lock(self):
        """核心回归：锁被占时必须干净退出，而不是照跑。"""
        probe = (
            "import sys; sys.path.insert(0, r'%s');"
            "from collectors.lock import acquire;"
            "acquire('test-holder', 0, r'%s');"
            "print('held')" % (ROOT, LOCK_DIR)
        )
        held = subprocess.run([sys.executable, "-B", "-c", probe],
                              cwd=str(ROOT), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60)
        self.assertIn("held", held.stdout, held.stdout + held.stderr)
        self.assertTrue(LOCK_DIR.exists())

        result = _run_pipeline("--dry-run")
        combined = result.stdout + result.stderr
        self.assertIn("已有一轮流水线在跑", combined)
        self.assertNotIn("将执行", combined,
                         "锁被占时绝不能继续往下跑")


if __name__ == "__main__":
    unittest.main()
