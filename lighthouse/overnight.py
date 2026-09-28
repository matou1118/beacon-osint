"""通宵采集调度器：采集 → 自迭代 → 采集 → …，无人值守。

为什么用 Python 而不是 .bat / .ps1
-----------------------------------
踩过两次：``.bat`` 里的 UTF-8 中文注释会被 cmd.exe 按 GBK 读、当成命令执行，
脚本连日志都建不出来；无 BOM 的 ``.ps1`` 同样被 PowerShell 5.1 按 ANSI 读，
把 ``[int]$Rounds = 12`` 报成「值不是有效数值」。逻辑放在 Python 里就没这些问题，
而且能测。

自迭代做什么
------------
一轮跑完不直接下一轮，而是先看这一轮谁没采到、为什么，然后：

    no_links 的源 → repair.py 从真实链接反推 detail_pattern（只改有证据的）
    反推成功且能采到的 → promote.py 把它恢复成 enabled
    然后再跑下一轮

三道闸，防止自迭代把配置改坏：

    * repair 只在**推导出的形态与列表页不同形、>=5 条链接、至少两段路径**时才写
    * 显式配了 detail_pattern 的源**优先于**通用启发式
    * promote 只根据**实测采到数据**的影子报告，不猜
    * 每一步都先备份 sources.yaml，且只做行级手术（保住 145 个人工登记字段）

    python lighthouse/overnight.py --rounds 14 --minutes 45
    python lighthouse/overnight.py --daemon          # 前台，看着它跑
    python lighthouse/overnight.py --stop            # 停
    python lighthouse/status.py                      # 随时查（出差用这个）
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

RUNTIME = _ROOT / "data" / "runtime" / "lighthouse"
LOG = RUNTIME / "overnight.log"
STATUS = RUNTIME / "status.json"
STOP_FLAG = RUNTIME / "STOP"
PY = sys.executable

_stop = False


def _handle(signum: int, _frame: Any) -> None:
    global _stop
    _stop = True
    log(f"收到信号 {signum}，本轮结束后停止")


def log(text: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {text}"
    print(line, flush=True)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def write_status(state: dict[str, Any]) -> None:
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    tmp = STATUS.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATUS)          # 原子替换，status.py 读它时不会读到半个文件


def db_count() -> int:
    import sqlite3

    path = _ROOT / "data" / "beacon.db"
    if not path.exists():
        return 0
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        n = con.execute("select count(*) from announcements").fetchone()[0]
        con.close()
        return int(n)
    except Exception:                                          # noqa: BLE001
        return 0


def run_collect(args: argparse.Namespace) -> int:
    cmd = [PY, "-u", "lighthouse/run.py", "--all",
           "--limit", str(args.limit), "--max-minutes", str(args.minutes),
           "--max-stale", str(args.stale), "--workers", str(args.workers),
           "--archive-pages", str(args.depth), "--ingest"]
    if args.fresh:
        cmd.append("--fresh")
    before = db_count()
    with open(LOG, "a", encoding="utf-8") as handle:
        proc = subprocess.Popen(cmd, cwd=str(_ROOT), stdout=handle,
                                stderr=subprocess.STDOUT)
        # 有限地等。实测一轮跑完（用了渲染）后 run.py 卡在 atexit 关浏览器，
        # 20 多分钟不退出；proc.wait() 无超时地等下去，这一轮在调度器眼里
        # 就永远没结束，后面所有轮次都不会开始，而 status.json 一直显示
        # 「采集中」——通宵任务就这么静默地死掉了。
        grace = args.minutes * 60 + 180
        try:
            return proc.wait(timeout=grace), before
        except subprocess.TimeoutExpired:
            log(f"  采集进程 {proc.pid} 超时 {grace // 60} 分未退出，强杀后继续下一轮"
                f"（已采到的数据在它写完旁路文件时就入库了，不会丢）")
            proc.kill()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass
            return -9, before


def self_iterate(round_no: int) -> dict[str, Any]:
    """一轮之后：把没采到的源试着修好，把采到数据的源恢复启用。

    只做有证据的动作。repair 推不出来的源保持原样，promote 只认实测。
    """
    summary: dict[str, Any] = {"repaired": [], "promoted": [], "skipped": []}

    reports = sorted(glob.glob(str(RUNTIME / "shadow-*.json")), key=os.path.getmtime)
    if not reports:
        return summary
    latest = reports[-1]
    data = json.loads(Path(latest).read_text(encoding="utf-8"))
    dead = [s["source_id"] for s in data.get("sources", [])
            if not s.get("records") and s.get("notes")
            and any("no links" in str(n) or "list_selector" in str(n)
                    for n in s["notes"])]
    if not dead:
        log("自迭代：没有 no_links 的源，跳过")
        return summary

    log(f"自迭代：{len(dead)} 个源取不到链接，尝试反推 detail_pattern")
    ids = ",".join(dead[:args_repair_batch[0]])
    repair = subprocess.run([PY, "lighthouse/repair.py", "--source", ids, "--apply"],
                            cwd=str(_ROOT), capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    for line in (repair.stdout or "").splitlines():
        if "已改" in line:
            summary["repaired"].append(line.strip())
            log("  " + line.strip())
    if repair.returncode != 0 and repair.stderr:
        log("  repair 失败：" + (repair.stderr.strip().splitlines() or [""])[-1][:160])

    # 一个 pattern 都没改出来就别复采了。
    # 实测：repair 对 9 个源推出 0 个（正确地拒绝了猜测），但代码还是去复采了一遍，
    # 白花 4 分钟，结论还是「仍然失败」。16 轮就是 1 小时。
    # 复采只在「配置真的改了」时才有意义——那是用来验证改动有没有用的。
    if not summary["repaired"]:
        log("  没推出任何新 pattern，跳过复采验证（改了才需要验）")
        return summary

    # 改完选择器立刻复采一次，用实测结果决定要不要恢复 enabled
    probe = subprocess.run(
        [PY, "lighthouse/run.py", "--source", ids, "--limit", "3",
         "--dry-run", "--fresh", "--max-minutes", "8", "--max-stale", "40",
         "--tag", "probe"],
        cwd=str(_ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace")
    # 找探针报告要认 tag：自迭代的探针只覆盖几个源，别把整轮的当最新一轮
    probes = sorted(glob.glob(str(RUNTIME / "shadow-probe-*.json")), key=os.path.getmtime)
    if not probes:
        return summary
    probed = json.loads(Path(probes[-1]).read_text(encoding="utf-8"))
    winners = [s["source_id"] for s in probed.get("sources", []) if s.get("records")]
    losers = [s["source_id"] for s in probed.get("sources", [])
              if not s.get("records") and s.get("records") == 0]
    if winners:
        promote = subprocess.run([PY, "lighthouse/promote.py",
                                  "--ids", ",".join(winners), "--apply"],
                                 cwd=str(_ROOT), capture_output=True, text=True,
                                 encoding="utf-8", errors="replace")
        for line in (promote.stdout or "").splitlines():
            if "status=" in line or "已写入" in line:
                summary["promoted"].append(line.strip())
                log("  " + line.strip())
    summary["skipped"] = losers
    log(f"自迭代：修好 {len(summary['repaired'])} 个，"
        f"恢复启用 {len(summary['promoted'])} 个，"
        f"仍然失败 {len(losers)} 个")
    return summary


args_repair_batch = [6]         # 每轮最多试修 6 个源，防止一轮吃掉整轮时间


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="通宵采集调度器（含自迭代）")
    parser.add_argument("--rounds", type=int, default=14)
    parser.add_argument("--minutes", type=int, default=45, help="每轮硬时限")
    parser.add_argument("--limit", type=int, default=25, help="每源上限")
    parser.add_argument("--stale", type=int, default=40, help="连续多少个已知 URL 才早停")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--repair-batch", type=int, default=6,
                        help="每轮最多试修几个 no_links 的源")
    parser.add_argument("--deadline", default="none",
                        help="到点停止，HH:MM。默认 none = 一直跑到被显式停掉")
    parser.add_argument("--depth", type=int, default=12,
                        help="起始翻页深度。产出衰减时自动加大——源被采浅了"
                             "就往更深的年份挖，而不是停下来")
    parser.add_argument("--depth-max", type=int, default=60,
                        help="翻页深度上限。再深下去多数站没有更多历史页")
    parser.add_argument("--no-self-iterate", action="store_true")
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--daemon", action="store_true",
                        help="留在前台（默认也是前台，daemon 只是显式声明）")
    parser.add_argument("--stop", action="store_true", help="写停止标记，让正在跑的实例退出")
    args = parser.parse_args(argv)
    args_repair_batch[0] = args.repair_batch

    RUNTIME.mkdir(parents=True, exist_ok=True)
    if args.stop:
        STOP_FLAG.write_text(datetime.now().isoformat(), encoding="utf-8")
        log("已写停止标记，正在跑的实例会在本轮结束后退出")
        return 0
    if STOP_FLAG.exists():
        STOP_FLAG.unlink()
        log("清除旧的停止标记")

    signal.signal(signal.SIGINT, _handle)
    try:
        signal.signal(signal.SIGTERM, _handle)
    except (AttributeError, ValueError):
        pass

    # 截止时刻算成绝对时间。直接比小时是错的：19:39 配 07:00 时
    # 「19 >= 7」为真，任务会立刻「停止，0 轮」。
    # 默认 none = 不设截止，一直跑到被显式停掉。理由：政府网站的历史公告
    # 每天都在增加，停在某个钟点就等于白扔已经建好的采集能力；衰减的问题
    # 该用「往深里挖」解决，不是用「到点躺下」解决。
    now = datetime.now()
    no_deadline = args.deadline.strip().lower() in {"none", "", "-", "0"}
    if no_deadline:
        deadline = now + timedelta(days=365)
    else:
        hour, _, minute = args.deadline.partition(":")
        deadline = now.replace(hour=int(hour), minute=int(minute or 0),
                               second=0, microsecond=0)
        if deadline <= now:
            deadline += timedelta(days=1)

    state: dict[str, Any] = {
        "pid": os.getpid(), "started_at": now.isoformat(timespec="seconds"),
        "deadline": deadline.isoformat(timespec="seconds"),
        "rounds_planned": args.rounds, "rounds_done": 0,
        "db_start": db_count(), "db_now": db_count(),
        "round_log": [], "last_error": "", "self_iteration": [],
        "depth": args.depth, "depth_changes": [],
    }
    write_status(state)

    log("=" * 74)
    log(f"通宵调度启动 pid={os.getpid()} 轮数={args.rounds} 每轮={args.minutes}分 "
        f"stale={args.stale} limit={args.limit} 并发={args.workers}")
    log(f"截止 {deadline.strftime('%m-%d %H:%M')}"
        f"{'（无截止，跑到被停掉）' if no_deadline else f'（{_hours(deadline)} 小时后）'}"
        f"  起始翻页深度 {args.depth}（上限 {args.depth_max}）"
        f"  起始库 {state['db_start']} 条")
    log(f"自迭代：{'关' if args.no_self_iterate else '开，每轮最多修 ' + str(args.repair_batch) + ' 个源'}")

    for index in range(1, args.rounds + 1):
        if _stop or STOP_FLAG.exists():
            log("收到停止请求")
            break
        if datetime.now() >= deadline:
            log(f"到达截止时间 {deadline.strftime('%H:%M')}，停止")
            break

        log(f"--- 第 {index}/{args.rounds} 轮 开始 ---")
        started = datetime.now()
        write_status({**state, "phase": f"collect round {index}"})
        try:
            code, before = run_collect(args)
        except Exception as exc:                              # noqa: BLE001
            state["last_error"] = f"{type(exc).__name__}: {exc}"
            log(f"  采集进程起不来：{exc}")
            code = -1
            before = db_count()
        after = db_count()
        gained = after - before
        minutes = round((datetime.now() - started).total_seconds() / 60, 1)
        log(f"--- 第 {index}/{args.rounds} 轮 结束  用时 {minutes} 分  "
            f"退出码 {code}  入库 +{gained}（{before}→{after}）---")

        state["rounds_done"] = index
        state["db_now"] = after
        state["round_log"].append({"round": index, "minutes": minutes,
                                   "exit": code, "db_before": before,
                                   "db_after": after, "gained": gained,
                                   "depth": args.depth,
                                   "at": datetime.now().isoformat(timespec="seconds")})
        write_status({**state, "phase": "self-iterate"})

        if not args.no_self_iterate and not _stop:
            try:
                state["self_iteration"].append(
                    {"round": index, **self_iterate(index)})
            except Exception as exc:                          # noqa: BLE001
                state["last_error"] = f"self_iterate: {type(exc).__name__}: {exc}"
                log(f"  自迭代异常：{exc}")
        write_status({**state, "phase": "idle"})

        # 产出衰减时的正确反应是**往深里挖**，不是停下来。
        # 实测依据：archive_pages 6 -> 12，4 个源的未采候选从 88 涨到 249。
        # 源被采浅了，深度还有的是；躺下等下次启动才是浪费。
        # 连续两轮明显衰减就加深一级；已经到上限就保持深度继续跑
        #（源全采空是正常状态，不是故障，没���有可挖的就不挖）。
        recent = state["round_log"][-3:]
        if len(recent) >= 2 and args.depth < args.depth_max:
            baseline = max((r["gained"] for r in recent[:-1]), default=0)
            latest = recent[-1]["gained"]
            if baseline > 0 and latest < baseline * 0.4:
                args.depth = min(args.depth + 6, args.depth_max)
                state["depth_changes"].append(
                    {"round": index, "from": args.depth - 6, "to": args.depth,
                     "reason": f"上一轮 {latest} 条，低于前几轮最好成绩 {baseline} 的 40%"})
                log(f"  产出衰减（{latest} < {baseline} 的 40%），翻页深度 "
                    f"{args.depth - 6} -> {args.depth}，继续挖")
                state["depth"] = args.depth
            elif recent[-1]["gained"] == 0 and latest == 0 and baseline == 0:
                if args.depth < args.depth_max:
                    args.depth = min(args.depth + 6, args.depth_max)
                    state["depth_changes"].append(
                        {"round": index, "from": args.depth - 6, "to": args.depth,
                         "reason": "连续两轮零入库，加深翻页再试"})
                    log(f"  连续两轮零入库，翻页深度 {args.depth - 6} -> {args.depth}")
                    state["depth"] = args.depth
        write_status({**state, "phase": "idle", "depth": args.depth})

    state["finished_at"] = datetime.now().isoformat(timespec="seconds")
    state["db_end"] = db_count()
    state["phase"] = "done"
    state["total_gain"] = state["db_end"] - state["db_start"]
    write_status(state)
    log("=" * 74)
    log(f"全部结束：{state['rounds_done']} 轮，净增 {state['total_gain']} 条 "
        f"（{state['db_start']} → {state['db_end']}）")
    log(f"统计：python lighthouse/report.py    状态：python lighthouse/status.py")
    return 0


def _hours(when: datetime) -> str:
    delta = when - datetime.now()
    return f"{delta.total_seconds() / 3600:.1f}"


if __name__ == "__main__":
    raise SystemExit(main())
