"""采集锁的回归测试。

通宵采集那晚真的撞上过：两个 pipeline.py 并行，一个在 collect、另一个已经跑到
dedup，原因是 VBS 不等待导致调度器认为任务已完成，IgnoreNew 形同虚设；而
pipeline.py 自己从不取锁，只清僵尸锁就继续。这几条就是防那件事再来的。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collectors.lock import (  # noqa: E402
    CollectLockBusy,
    _is_stale,
    acquire,
    collect_lock,
    release,
)


class LockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="beaconlock"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_second_acquire_is_refused(self):
        acquire("A", 0, self.tmp)
        try:
            with self.assertRaises(CollectLockBusy):
                acquire("B", 0, self.tmp)
        finally:
            release(self.tmp)
        self.assertFalse(self.tmp.exists())

    def test_lock_dir_is_usable_when_another_path_given(self):
        """lock_dir 参数必须真的生效。

        踩过：编辑时留下了重复的 __enter__，Python 取最后一个，也就是旧的那份
        acquire(name, wait_seconds)——lock_dir 被静默忽略，所有调用都去抢
        collect.lock。测试时表现为"传了 test.lock 却报 collect.lock 已被占用"。
        """
        acquire("A", 0, self.tmp)
        try:
            with self.assertRaises(CollectLockBusy):
                acquire("B", 0, self.tmp)
        finally:
            release(self.tmp)

    def test_release_removes_dir(self):
        with collect_lock("A", lock_dir=self.tmp) as ok:
            self.assertTrue(ok)
            self.assertTrue(self.tmp.exists())
        self.assertFalse(self.tmp.exists())


class StaleTests(unittest.TestCase):
    """持锁进程还活着就一律不算僵尸，不管锁了多久。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="beaconstale"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, pid: int, age: float) -> None:
        (self.tmp / "holder.json").write_text(
            json.dumps({"pid": pid, "started_at_epoch": time.time() - age}),
            encoding="utf-8")

    def test_live_holder_is_never_stale_however_old(self):
        """通宵排空跑 40 轮，整轮很容易超过 3 小时。

        原来只看 started_at_epoch 距今是否超 3 小时，于是长跑会被后来的进程
        把锁抢走，两条流水线同时删改同一个库。99999 秒 ≈ 27.8 小时。
        """
        self._write(os.getpid(), 99999)
        self.assertFalse(_is_stale(self.tmp))

    def test_dead_holder_is_stale(self):
        self._write(999_999, 99999)
        self.assertTrue(_is_stale(self.tmp))

    def test_missing_holder_file_is_stale(self):
        """目录在但没有 holder.json：强杀在 mkdir 与写文件之间。"""
        self.assertTrue(_is_stale(self.tmp))

    def test_pid_probe_does_not_kill_the_process(self):
        """_pid_alive 绝不能用 os.kill(pid, 0)。

        那是 POSIX 的探测用法。CPython 在 Windows 上把非 CTRL_C/CTRL_BREAK 的
        os.kill 一律映射成 TerminateProcess(pid, sig)，于是 os.kill(pid, 0)
        会真的杀掉目标进程并返回 0。实测后果：校验脚本和 pytest 都被自己静默
        杀掉，表现为"没有任何输出、退出码 0"——因为确实没报错，进程只是没了，
        极难定位。

        这条测试用「探测自己的 PID 之后进程还活着」来固定住行为：换成
        os.kill 实现的瞬间，这个测试进程会当场消失，pytest 报的是进程崩溃
        而不是断言失败。
        """
        from collectors.lock import _pid_alive

        self.assertTrue(_pid_alive(os.getpid()))
        self.assertFalse(_pid_alive(0))
        self.assertFalse(_pid_alive(-1))
        # 探测完自己必须还活着——这是本条测试真正的断言
        self.assertTrue(_pid_alive(os.getpid()))


if __name__ == "__main__":
    unittest.main()
