"""查看采集进度：正在跑哪个源、已完成多少、最近有没有卡住。

为什么需要
  采集一轮要 1-2 小时，之前用 main.py 跑，它只在**结束时**打印一个 JSON，
  中间毫无输出；而且重定向到文件时 stdout 是块缓冲的，进程一被杀掉日志就是
  0 字节——我因此以为"采集跑完了但没数据"，实际是被僵尸锁挡掉直接退出了。
  这类黑箱必须能看见：跑多久了、卡在哪、是不是真的活着。

判断依据三个，不只看日志
  1. 进程在不在（PID）
  2. 锁的 holder.json 是否指向那个 PID（锁在但进程死了 = 僵尸锁）
  3. 日志最后一行的时间和内容（多久没更新 = 卡住）

用法
  python scripts/collect_progress.py            # 看一次
  python scripts/collect_progress.py --watch    # 每 10 秒刷新
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "data" / "runtime" / "collect.lock" / "holder.json"
# batch_collect.py 逐源输出 "[3/52] sid (name) OK 12条 (37s)"，据此算进度
LINE_RE = re.compile(r"\[(\d+)/(\d+)\]\s+(\S+)\s+\((.*?)\)\s+(OK|FAIL|SKIP)\s*(.*)")

# 单源超过这个秒数没出结果就提示可能卡住
STALE_SECONDS = 300


def running_pids() -> dict[int, str]:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }"],
            capture_output=True, text=True, timeout=30).stdout
    except Exception:  # noqa: BLE001
        return {}
    result: dict[int, str] = {}
    for line in out.splitlines():
        if "|" not in line:
            continue
        pid_text, _, cmd = line.partition("|")
        try:
            result[int(pid_text.strip())] = cmd.strip()
        except ValueError:
            continue
    return result


def newest_log() -> Path | None:
    candidates = [p for p in (ROOT / "data").glob("*.log") if p.stat().st_size > 0]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def report() -> dict:
    pids = running_pids()
    collectors = {pid: cmd for pid, cmd in pids.items()
                  if "batch_collect" in cmd or "main.py" in cmd}
    holder = None
    if LOCK.exists():
        try:
            holder = json.loads(LOCK.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            holder = None

    log = newest_log()
    done = total = 0
    last_line = ""
    last_age = 0.0
    counts: dict[str, int] = {}
    # done/total 取整份日志里最大的编号，而不是最后一行——batch_collect 是并发
    # 6 路，完成顺序不单调，末行可能是 "[41/145]"，按它算会显示进度倒退。
    best = 0
    if log and log.stat().st_size:
        last_age = time.time() - log.stat().st_mtime
        lines = log.read_text(encoding="utf-8", errors="ignore").splitlines()
        for line in lines:
            m = LINE_RE.search(line)
            if m:
                best = max(best, int(m.group(1)))
                total = max(total, int(m.group(2)))
                counts[m.group(5)] = counts.get(m.group(5), 0) + 1
                last_line = line.strip()
        done = best
        if not last_line and lines:
            last_line = lines[-1].strip()

    # 锁在但进程不在 = 僵尸锁。这是刚才踩过的坑：进程被 Stop-Force 杀掉，
    # 锁目录留下，后续每一轮采集都正确地拒绝写入，表现为"日志 0 字节、一片安静"。
    zombie = bool(holder) and holder.get("pid") not in pids
    return {
        "collectors": collectors,
        "holder": holder,
        "zombie_lock": zombie,
        "log": str(log) if log else None,
        "log_age_seconds": round(last_age, 1),
        "done": done,
        "total": total,
        "counts": counts,
        "last_line": last_line,
    }


def render(info: dict) -> str:
    out: list[str] = []
    if info["collectors"]:
        for pid, cmd in info["collectors"].items():
            short = cmd if len(cmd) < 90 else cmd[:90] + "..."
            out.append(f"[RUNNING] PID {pid}: {short}")
    else:
        out.append("[IDLE] 没有采集进程在跑")

    holder = info["holder"]
    if info["zombie_lock"]:
        out.append(f"[ZOMBIE LOCK] 锁的 holder 是 PID {holder.get('pid')}，"
                   f"但该进程不存在。这会挡住所有采集，需手动清理：")
        out.append(f'    cmd /c rd /s /q "{LOCK.parent}"')
    elif holder:
        out.append(f"[LOCK] PID {holder.get('pid')} since {holder.get('started_at')}")

    log = info["log"]
    if log:
        out.append(f"[LOG] {log}")
        if info["total"]:
            pct = info["done"] / info["total"] * 100
            # 标签用 ASCII：控制台在 GBK 代码页下会把中文标签和数字混排显示成
            # 乱码（我一度把 "done=81" 读成 0，以为进度没更新）。数字单独成行最保险。
            out.append(f"done={info['done']} / total={info['total']}  "
                       f"({pct:.0f}%)  {info['counts']}")
        age = info["log_age_seconds"]
        if age > STALE_SECONDS:
            out.append(f"[STALE] 日志 {age:.0f}s 没更新，可能卡住了")
        else:
            out.append(f"[OK] 最后更新 {age:.0f}s 前")
        if info["last_line"]:
            out.append(f"[LAST] {info['last_line'][:110]}")
    else:
        out.append("[NO LOG] 没有任何非空日志 —— 要么没跑过，要么被僵尸锁挡掉退出了")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--watch", action="store_true", help="每 10 秒刷新")
    args = parser.parse_args()
    if not args.watch:
        print(render(report()))
        return 0
    try:
        while True:
            print("\033[2J\033[H", end="")
            print(f"采集进度  {time.strftime('%H:%M:%S')}")
            print(render(report()))
            time.sleep(10)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
