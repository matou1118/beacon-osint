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


def _read_holder() -> dict:
    try:
        return json.loads((LOCK_DIR / "holder.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _is_stale() -> bool:
    info = _read_holder()
    started = info.get("started_at_epoch")
    if not started:
        # 拿不到开始时间就保守处理：目录存在但没有 holder.json 视为僵尸锁。
        return True
    return (time.time() - float(started)) > STALE_SECONDS


def _take_over() -> None:
    holder = {
        "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "started_at_epoch": time.time(),
        "taken_over_from": _read_holder() or None,
    }
    (LOCK_DIR / "holder.json").write_text(
        json.dumps(holder, ensure_ascii=False, indent=2), encoding="utf-8")


def acquire(name: str, wait_seconds: int = 0) -> Path:
    """拿不到锁就抛 CollectLockBusy。返回锁目录以便 release。"""
    LOCK_DIR.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + max(0, wait_seconds)
    while True:
        try:
            os.mkdir(LOCK_DIR)
        except FileExistsError:
            if _is_stale():
                # 僵尸锁：接管。先删再建，避免留下半清理状态。
                try:
                    (LOCK_DIR / "holder.json").unlink(missing_ok=True)
                    os.rmdir(LOCK_DIR)
                except OSError as exc:  # 别的进程正在正常释放
                    if exc.errno not in (errno.ENOENT, errno.ENOTEMPTY):
                        raise
                continue
            if time.time() >= deadline:
                holder = _read_holder()
                raise CollectLockBusy(
                    f"另一个采集器正在运行（pid={holder.get('pid', '?')}，"
                    f"开始于 {holder.get('started_at', '?')}），本轮跳过：{name}")
            time.sleep(5)
            continue
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise
            time.sleep(5)
            continue
        _take_over()
        return LOCK_DIR


def release() -> None:
    try:
        (LOCK_DIR / "holder.json").unlink(missing_ok=True)
        os.rmdir(LOCK_DIR)
    except OSError:
        pass  # 已被接管或已被清理


class collect_lock:
    """上下文管理器。

    用法：
        with collect_lock("报告采集") as ok:
            if not ok:
                return 0   # 拿不到锁，本轮跳过
            ...
    """

    def __init__(self, name: str, wait_seconds: int = 0) -> None:
        self.name = name
        self.wait_seconds = wait_seconds
        self.acquired = False

    def __enter__(self) -> bool:
        try:
            acquire(self.name, self.wait_seconds)
            self.acquired = True
        except CollectLockBusy as exc:
            print(f"[跳过] {exc}")
        return self.acquired

    def __exit__(self, *exc_info) -> None:
        if self.acquired:
            release()
            self.acquired = False
