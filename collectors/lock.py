"""采集互斥锁：同一时刻只跑一个采集器。

为什么需要
  三个采集入口都写 `data/beacon.db`：公告批量采集（batch_collect.py）、
  政府工作报告（collect_work_reports.py）、统计公报（collect_stat_communiques.py），
  另有两个计划任务直接调 scheduler.py。SQLite 允许一写多读，但 writer 与
  writer 重叠在 WAL 之外的场景会抛 "database is locked"；即便靠 busy_timeout
  扛住，两个采集器同时改同一批来源的状态（source_sync_state）也会互相覆盖，
  导致「采集失败被记成成功」这类静默错误。

为什么不用文件锁 API
  os.mkdir 在所有平台都是原子操作，不需要 fcntl/msvcrt 之类的条件导入。
  锁目录里放 pid 与开始时间，便于事后判断是不是残留的僵尸锁。

超时行为
  拿不到锁就跳过本轮并说明原因，不排队等待——定时任务是周期性触发的，
  排队只会让越积越多。这与"限速下限"是同一个礼貌原则：不和别的采集器抢。
"""
from __future__ import annotations

import errno
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK_DIR = ROOT / "data" / "runtime" / "collect.lock"
# 超过这个时长还持锁的，视为上次进程崩溃留下的僵尸锁，可以接管。
STALE_SECONDS = 3 * 3600


class CollectLockBusy(RuntimeError):
    """锁已被别的采集器持有。"""


def _read_holder(lock_dir: Path | None = None) -> dict:
    try:
        return json.loads(((lock_dir or LOCK_DIR) / "holder.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _pid_alive(pid: int) -> bool:
    """判断进程是否还活着。

    **不要用 os.kill(pid, 0)**：那是 POSIX 的"探测"用法，在 Windows 上语义完全不同。
    CPython 的 os_kill_impl 对非 CTRL_C/CTRL_BREAK 事件一律走 TerminateProcess，
    所以 os.kill(pid, 0) 会**真的把那个进程杀掉**并返回 0。实测踩过：校验脚本和
    pytest 都被自己静默杀掉，表现为"命令没有任何输出、退出码 0"，看不出是哪里出的
    问题——因为确实没有报错，进程只是没了。

    Windows 上用 kernel32.OpenProcess 查，POSIX 上才用 os.kill。
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return False
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True   # 存在但不属于当前用户
    except OSError:
        return True   # 判断不了就保守，当它活着
    return True


def _is_stale(lock_dir: Path | None = None) -> bool:
    """僵尸判定。持锁进程还活着就一律不算僵尸，**不管锁了多久**。

    原来只看 started_at_epoch 距今是否超过 3 小时。通宵排空跑 40 轮，轮间
    暂停就有 80 分钟，整轮很容易超过 3 小时——一旦超过，另一轮进来会把锁
    抢走，两条流水线同时对同一个库做删除和插入。所以先看 PID 活不活，
    3 小时只作为"PID 缺失或已死"时的兜底。
    """
    info = _read_holder(lock_dir)
    pid = info.get("pid")
    if pid and _pid_alive(int(pid)):
        return False
    started = info.get("started_at_epoch")
    if not started:
        # 目录在但没有 holder.json（例如被强杀在 mkdir 与写文件之间）：算残留
        return True
    return (time.time() - float(started)) > STALE_SECONDS


def _take_over(lock_dir: Path | None = None) -> None:
    target = lock_dir or LOCK_DIR
    holder = {
        "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "started_at_epoch": time.time(),
        "taken_over_from": _read_holder(target) or None,
    }
    (target / "holder.json").write_text(
        json.dumps(holder, ensure_ascii=False, indent=2), encoding="utf-8")


def acquire(name: str, wait_seconds: int = 0, lock_dir: Path | None = None) -> Path:
    """取采集锁。取不到抛 CollectLockBusy，取到由 release() 释放。

    lock_dir 让同一套原语能管多把锁：采集锁给 batch_collect 等写库脚本，
    流水线锁给 scripts/pipeline.py。两把锁互不干扰——流水线不能直接复用采集锁，
    因为它自己会调 batch_collect，那样会自己把自己挡在门外。
    """
    target = Path(lock_dir) if lock_dir else LOCK_DIR
    target.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + max(0, wait_seconds)
    while True:
        try:
            os.mkdir(target)
        except FileExistsError:
            if _is_stale(target):
                # 僵尸锁没人接手，删掉继续重试
                try:
                    (target / "holder.json").unlink(missing_ok=True)
                    os.rmdir(target)
                except OSError as exc:  # 另一个进程正在清理
                    if exc.errno not in (errno.ENOENT, errno.ENOTEMPTY):
                        raise
                continue
            if time.time() >= deadline:
                holder = _read_holder(target)
                raise CollectLockBusy(
                    f"另一个采集进程正在运行（pid={holder.get('pid', '?')}，"
                    f"开始于 {holder.get('started_at', '?')}），本次跳过：{name}")
            time.sleep(5)
            continue
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise
            time.sleep(5)
            continue
        _take_over(target)
        return target


def release(lock_dir: Path | None = None) -> None:
    target = Path(lock_dir) if lock_dir else LOCK_DIR
    try:
        (target / "holder.json").unlink(missing_ok=True)
        os.rmdir(target)
    except OSError:
        pass


class collect_lock:
    """采集锁上下文管理器

    用法
        with collect_lock("某某采集") as ok:
            if not ok:
                return 0   # 锁被别人占着，正常退出
            ...
    """

    def __init__(self, name: str, wait_seconds: int = 0,
                 lock_dir: Path | None = None) -> None:
        self.name = name
        self.wait_seconds = wait_seconds
        self.lock_dir = lock_dir
        self.acquired = False

    def __enter__(self) -> bool:
        try:
            acquire(self.name, self.wait_seconds, self.lock_dir)
            self.acquired = True
        except CollectLockBusy as exc:
            print(f"[跳过] {exc}")
        return self.acquired

    def __exit__(self, *exc_info) -> None:
        if self.acquired:
            release(self.lock_dir)
            self.acquired = False
