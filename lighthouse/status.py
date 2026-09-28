"""一句话看清通宵任务状态。出差用这个。

    python lighthouse/status.py            # 一行摘要
    python lighthouse/status.py --full     # 逐轮明细
    python lighthouse/status.py --stop     # 优雅停止（当前轮跑完才退出）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
STATUS = _ROOT / "data" / "runtime" / "lighthouse" / "status.json"
STOP_FLAG = _ROOT / "data" / "runtime" / "lighthouse" / "STOP"


def alive(pid: int) -> bool:
    if os.name == "nt":
        out = os.popen(f'tasklist /FI "PID eq {pid}" 2>NUL').read()
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def load() -> dict | None:
    if not STATUS.exists():
        return None
    try:
        return json.loads(STATUS.read_text(encoding="utf-8"))
    except ValueError:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="通宵任务状态")
    parser.add_argument("--full", action="store_true", help="逐轮明细")
    parser.add_argument("--stop", action="store_true", help="写停止标记")
    args = parser.parse_args(argv)

    if args.stop:
        STOP_FLAG.parent.mkdir(parents=True, exist_ok=True)
        STOP_FLAG.write_text(datetime.now().isoformat(), encoding="utf-8")
        print("已写停止标记。正在跑的那一轮会跑完再退出（不会丢已采到的数据）。")
        return 0

    state = load()
    if state is None:
        print("没有 status.json —— 还没跑过 overnight.py")
        return 1

    pid = int(state.get("pid") or 0)
    running = alive(pid) if pid else False
    phase = state.get("phase", "?")
    done = int(state.get("rounds_done") or 0)
    planned = int(state.get("rounds_planned") or 0)

    print("=" * 62)
    if running:
        print(f"  运行中   pid={pid}   阶段={phase}   {done}/{planned} 轮")
    elif state.get("finished_at"):
        reason = state.get("stopped_reason") or ("到达截止时间" if phase == "done" else phase)
        print(f"  已结束   {state['finished_at'][:16]}   {done}/{planned} 轮   原因：{reason}")
    else:
        print(f"  进程不在了（pid={pid}）但没写 finished_at —— 可能是被强杀")
        print(f"  阶段={phase}  {done}/{planned} 轮")

    gain = int(state.get("total_gain") or (int(state.get("db_now") or 0)
                                           - int(state.get("db_start") or 0)))
    print(f"  入库     {state.get('db_start')} -> {state.get('db_now') or state.get('db_end')}"
          f"   净增 {gain}")
    if state.get("deadline"):
        print(f"  截止     {state['deadline'][:16]}")
    if state.get("updated_at"):
        print(f"  最后更新 {state['updated_at'][:16]}")

    repaired = sum(len(x.get("repaired") or []) for x in state.get("self_iteration") or [])
    promoted = sum(len(x.get("promoted") or []) for x in state.get("self_iteration") or [])
    print(f"  自迭代   修好 pattern {repaired} 次，恢复启用 {promoted} 次")
    depth = state.get("depth")
    changes = state.get("depth_changes") or []
    if depth:
        note = f"（已自动加深 {len(changes)} 次）" if changes else ""
        print(f"  翻页深度 {depth}{note}")
    if state.get("last_error"):
        print(f"  最后错误 {state['last_error'][:70]}")
    if state.get("finished_at") and not running:
        print("  自动流程已结束 —— 重启用：python lighthouse/watchdog.py")

    if args.full:
        print("-" * 62)
        print(f"  {'轮':>3}{'用时':>8}{'退出码':>8}{'入库前':>9}{'入库后':>9}{'净增':>7}{'深度':>7}")
        for row in state.get("round_log") or []:
            print(f"  {row['round']:>3}{row['minutes']:>8}{row['exit']:>8}"
                  f"{row['db_before']:>9}{row['db_after']:>9}{row['gained']:>7}"
                  f"{str(row.get('depth') or '-'):>7}")
        for change in state.get("depth_changes") or []:
            print(f"    深度 {change['from']} -> {change['to']}：{change['reason']}")
        for item in state.get("self_iteration") or []:
            for line in item.get("repaired") or []:
                print("    修 " + line)
            for line in item.get("promoted") or []:
                print("    启 " + line)
    print("=" * 62)
    print("  详细统计：python lighthouse/report.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
