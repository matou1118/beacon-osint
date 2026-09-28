"""看门狗：调度器自己崩了就重新拉起，直到截止时间。

为什么需要
----------
overnight.py 内部已经能扛住「一轮采集失败」（子进程返回码非 0 也继续下一轮），
但扛不住**它自己**被杀——OOM、机器休眠唤醒失败、被人误关窗口。
出差期间没人盯着，得有个进程专门负责把它拉回来。

看门狗自己也可能崩，所以它做的事情必须极简：只 spawn、只探活、只记日志。

    python lighthouse/watchdog.py --rounds 20 --minutes 30 --deadline 07:30
    python lighthouse/watchdog.py --stop
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

RUNTIME = _ROOT / "data" / "runtime" / "lighthouse"
LOG = RUNTIME / "watchdog.log"
STOP_FLAG = RUNTIME / "WATCHDOG_STOP"
PY = sys.executable

# 调度器正常跑完一轮要 30 分钟左右。间隔这么久还没动静就当它死了。
STALL_SECONDS = 3600
_stop = False


def log(text: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {text}"
    print(line, flush=True)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _handle(_signum: int, _frame: object) -> None:
    global _stop
    _stop = True


def progress_stamp() -> float:
    """调度器最后一次动 status.json 的时间。"""
    path = RUNTIME / "status.json"
    if not path.exists():
        return 0.0
    return path.stat().st_mtime


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="通宵调度看门狗")
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--minutes", type=int, default=30)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--stale", type=int, default=40)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--repair-batch", type=int, default=6)
    parser.add_argument("--depth", type=int, default=12)
    parser.add_argument("--depth-max", type=int, default=60)
    parser.add_argument("--deadline", default="none",
                        help="到点停止，HH:MM。默认 none = 一直守到被显式停掉")
    parser.add_argument("--max-restarts", type=int, default=200)
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args(argv)

    RUNTIME.mkdir(parents=True, exist_ok=True)
    if args.stop:
        STOP_FLAG.write_text(datetime.now().isoformat(), encoding="utf-8")
        log("已写看门狗停止标记")
        return 0
    if STOP_FLAG.exists():
        STOP_FLAG.unlink()

    signal.signal(signal.SIGINT, _handle)
    try:
        signal.signal(signal.SIGTERM, _handle)
    except (AttributeError, ValueError):
        pass

    now = datetime.now()
    # 默认 none：不设截止。钟点不是停采集的理由，衰减了该往深里挖。
    no_deadline = args.deadline.strip().lower() in {"none", "", "-", "0"}
    if no_deadline:
        deadline = now + timedelta(days=3650)
    else:
        hour, _, minute = args.deadline.partition(":")
        deadline = now.replace(hour=int(hour), minute=int(minute or 0),
                               second=0, microsecond=0)
        if deadline <= now:
            deadline += timedelta(days=1)

    log("=" * 66)
    log(f"看门狗启动 pid={os.getpid()} "
        f"{'不设截止，一直守到被停掉' if no_deadline else '截止 ' + deadline.strftime('%m-%d %H:%M')}"
        f" 最多重启 {args.max_restarts} 次 起始深度 {args.depth}")

    restarts = 0
    child: subprocess.Popen | None = None
    while not _stop and datetime.now() < deadline:
        if STOP_FLAG.exists():
            log("看到停止标记，退出")
            break
        if child is not None and child.poll() is None:
            time.sleep(20)
            continue

        if child is not None:
            code = child.returncode
            if restarts >= args.max_restarts:
                log(f"重启次数用尽（{restarts}），退出")
                break
            restarts += 1
            log(f"调度器退出（退出码 {code}），第 {restarts} 次重启")
        else:
            log("拉起调度器")

        cmd = [PY, "-u", "lighthouse/overnight.py",
               "--rounds", str(args.rounds), "--minutes", str(args.minutes),
               "--limit", str(args.limit), "--stale", str(args.stale),
               "--workers", str(args.workers),
               "--depth", str(args.depth), "--depth-max", str(args.depth_max),
               "--repair-batch", str(args.repair_batch),
               "--deadline", args.deadline]
        child = subprocess.Popen(cmd, cwd=str(_ROOT))
        time.sleep(20)

    if child is not None and child.poll() is None:
        log("让调度器自己跑完（不杀它）")
    log(f"看门狗结束，共重启 {restarts} 次")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
