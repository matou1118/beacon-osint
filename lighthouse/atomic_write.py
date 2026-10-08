"""原子写文件 —— Windows 上必须带重试，否则整轮采集会崩。

为什么单独一个模块
------------------
`Path.replace()` 在 POSIX 上就是 rename(2)，覆盖一个正被打开的文件没问题。
**Windows 不是**：打开的文件带共享锁，而 replace 要「删除 + 重命名」，于是抛
``PermissionError: [WinError 5] 拒绝访问``。

这不是权限配置问题，是文件系统的锁语义。实测 2026-10-02 整夜：
``scripts/status_summary.py`` 和 ``scripts/iterate_loop.py`` 每 20 分钟读一次
``source_backoff.json``，采集器写回时就撞上锁，调度器一起就退，看门狗连续
重启 **62 次**，12 小时只完成 1 轮、零入库。

为什么不在调用点各写一遍重试
----------------------------
这个问题项目里出现过 5 次（run / principles ×2 / principle_applier / overnight），
而 Windows 上最讨厌的恰恰是「大部分时候没事，偶尔炸」——各写各的早晚写法不一。
集中在这里，加测试时也只测一处。
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any

# 重试次数和间隔。**实测定的**：6 次线性退避（累计约 2.7 秒）在持续读取的
# 场景下仍然失败——2026-10-02 用真实 source_backoff.json 加一个每秒读 1000 次
# 的线程压测，60 次写入里有 2 次在 6 次重试后仍抛 PermissionError。
# 原因是读者线程连开文件时会产生一个独占窗口，线性退避容易和它同步撞上。
#
# 所以改成更多次 + 抖动。采集写一次状态最多等 1.4 秒，而守卫脚本的读取
# 窗口在毫秒级，抖动能保证错开。
ATTEMPTS = 10
BACKOFF_STEP = 0.08
# 抖动上限（秒）。线性步长的这部分固定加成。
JITTER = 0.12


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> str:
    """把 ``text`` 原子地写进 ``path``。返回实际用的方式。

    先写 ``.tmp`` 再替换，这样崩了也不会留下半个文件（读方要么读到旧的完整
    内容，要么读到新的完整内容）。

    替换失败时重试；全部失败就退到直接覆盖写——不是原子的，但比整轮崩掉好。
    读取方最坏读到半个文件，下一轮自愈。

    返回 ``"replace"`` / ``"overwrite"``，调用方可据此写日志。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding=encoding)
    last: OSError | None = None
    for attempt in range(ATTEMPTS):
        try:
            tmp.replace(path)
            return "replace"
        except PermissionError as exc:
            last = exc
            # 抖动很重要：固定间隔会和读取方的轮询节奏同步，两边一起撞。
            time.sleep(BACKOFF_STEP * (attempt + 1)
                       + random.uniform(0, JITTER))
    try:
        path.write_text(tmp.read_text(encoding=encoding), encoding=encoding)
        tmp.unlink(missing_ok=True)
        return "overwrite"
    except OSError:
        raise last if last else OSError(f"无法写入 {path}")


def atomic_write_json(path: Path, data: Any) -> str:
    return atomic_write_text(path, json.dumps(data, ensure_ascii=False))


__all__ = ["atomic_write_text", "atomic_write_json", "ATTEMPTS", "BACKOFF_STEP"]