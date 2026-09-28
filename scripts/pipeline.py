"""一键全流程：采集 → 清洗 → 摘要 → 分类 → 去重 → 体检，一条命令跑完。

为什么需要这个
  交付不了的原因是流程本身没有编排：采集 batch_collect、清洗 prune_section_pages、
  摘要 resummarize、分类 reclassify、去重 dedup、体检 audit_* 全是独立脚本，
  每次都要人按顺序手动跑一遍。漏一步数据就不一致，而且没人能确认"现在库里的
  数据是不是每一步都跑过了"。本脚本把六步固定成一条流水线。

每一步都必须能单独看状态，也要能整体重跑
  - 每步的统计写进 data/pipeline_state.json，带时间戳和数量；
  - 中途失败不丢已完成的部分：--from 可以指定从第几步开始；
  - 互斥锁只覆盖采集那一步，摘要/分类/去重各自开短事务，不长时间占锁。

失败策略：采集失败不阻塞后续
  采集是唯一会因网络失败的步骤，27 个源超时是常态。所以采集按"能采多少算多少"
  继续往下走，最后统一报告哪几步没成功，由人决定要不要重跑那一步。

用法
  python scripts/pipeline.py                 # 全流程
  python scripts/pipeline.py --from clean    # 从清洗开始（采集已跑过）
  python scripts/pipeline.py --only summary  # 只跑某一步
  python scripts/pipeline.py --dry-run       # 只打印将执行什么
  python scripts/pipeline.py --limit 30 --workers 6 --timeout 300
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collectors.lock import CollectLockBusy, acquire, release  # noqa: E402
from collectors.net import proxy_status  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
STATE = ROOT / "data" / "pipeline_state.json"
LOCK_DIR = ROOT / "data" / "runtime" / "collect.lock"
# 流水线自己的锁目录。必须是另一把锁：它会调 batch_collect，而 batch_collect 要取
# 采集锁，同一把会让流水线把自己挡在门外。
#
# BEACON_PIPELINE_LOCK 可以改路径，是为了测试。通宵采集常驻时，生产锁会被真实
# 持有，测试若也用生产路径就必然失败——之前 5 个流水线测试全是因为这个红的，
# 而且失败的恰恰是"锁被占时必须拒绝启动"那条最该跑的断言。让测试指向自己的
# 临时路径，测试才能在采集运行时照常验证真实行为。
PIPELINE_LOCK_DIR = Path(os.environ.get(
    "BEACON_PIPELINE_LOCK", str(ROOT / "data" / "runtime" / "pipeline.lock")))


def log(message: str) -> None:
    """每行都 flush。长流程不刷盘的话，重定向到文件后进程一被杀掉日志就是空的。"""
    print(message, flush=True)


def run_step(name: str, argv: list[str], *, timeout: int = 7200) -> dict:
    """跑一步，子进程输出**实时透传**到本进程的标准输出。

    原来用 subprocess.run(capture_output=True)，结果整步的输出都攒在内存里
    直到进程结束才打印——采集那一步要 1 小时，于是日志停在"[collect] 开始"
    整整一小时，正是你说的"长时间卡死都不知道"。而 batch_collect 明明每源都在
    刷进度，是编排器把它吞了。

    改成 Popen 逐行读。stderr 合并进 stdout（两者都刷到同一个文件），
    子进程已经用 -u 关掉缓冲，所以这里能实时看到。
    """
    started = time.time()
    log(f"\n{'=' * 66}\n[{name}] 开始 {' '.join(argv[1:])}\n{'=' * 66}")
    env = dict(os.environ)
    # -u 关掉块缓冲，否则被 kill 后日志为 0 字节（踩过：force_run.log 0 字节，
    # 我一度读成"跑完了没数据"，实际是进程被僵尸锁挡掉直接退出了）
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    proc = subprocess.Popen(argv, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", bufsize=1)
    tail: list[str] = []
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                log(f"  | {line[:150]}")
                tail.append(line)
                del tail[:-8]  # 只留最后 8 行做摘要
        proc.wait(timeout=max(1, timeout - int(time.time() - started)))
    except subprocess.TimeoutExpired:
        proc.kill()
        elapsed = time.time() - started
        log(f"[{name}] ✗ 超时 {elapsed:.0f}s（>{timeout}s）")
        return {"step": name, "ok": False, "error": f"timeout after {timeout}s",
                "seconds": round(elapsed)}
    elapsed = time.time() - started
    if proc.returncode != 0:
        log(f"[{name}] ✗ 退出码 {proc.returncode}，耗时 {elapsed:.0f}s")
        return {"step": name, "ok": False, "returncode": proc.returncode,
                "error": " | ".join(tail[-2:])[:200], "seconds": round(elapsed)}
    log(f"[{name}] ✓ 完成，耗时 {elapsed:.0f}s")
    return {"step": name, "ok": True, "seconds": round(elapsed), "tail": tail}


def clear_stale_lock() -> str | None:
    """清掉僵尸锁。

    锁目录留着而持锁进程已死时，batch_collect 会**正确地**拒绝写入，表现为
    "日志 0 字节、一片安静"——看起来像卡死，实际是被自己的锁挡在门外。
    自动判断 PID 是否还活着，比让人手动 rd 快，也不容易忘。
    """
    if not LOCK_DIR.exists():
        return None
    holder_path = LOCK_DIR / "holder.json"
    try:
        holder = json.loads(holder_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        holder = {}
    pid = holder.get("pid")
    alive = False
    if pid:
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"if (Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue) "
                 f"{{ 'ALIVE' }} else {{ 'DEAD' }}"],
                capture_output=True, text=True, timeout=20).stdout.strip()
            alive = out.endswith("ALIVE")
        except Exception:  # noqa: BLE001
            alive = True  # 判断不了就保守，当它活着
    if alive:
        return f"持锁进程 PID {pid} 还活着，不清（另一轮采集正在跑）"
    subprocess.run(["cmd", "/c", "rd", "/s", "/q", str(LOCK_DIR)],
                   capture_output=True, timeout=30)
    return f"已清理僵尸锁（原持锁 PID {pid} 已死）"


def count(table: str = "announcements") -> int | None:
    import sqlite3

    try:
        con = sqlite3.connect(f"file:{ROOT / 'data' / 'beacon.db'}?mode=ro", uri=True)
        value = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        con.close()
        return value
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="start", default="collect",
                        choices=["collect", "clean", "refill", "amounts", "summary", "classify", "dedup", "audit"],
                        help="从哪一步开始")
    parser.add_argument("--only", default=None,
                        choices=["collect", "clean", "refill", "amounts", "summary", "classify", "dedup", "audit"])
    parser.add_argument("--limit", type=int, default=30, help="每源最多取多少条")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=300, help="每源秒数预算")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--all-statuses", action="store_true",
                        help="连 blocked/pending_reprobe 一起试（默认只跑启用源）")
    parser.add_argument("--drain", action="store_true",
                        help="排空模式：反复采集直到各源取空（通宵用）")
    parser.add_argument("--rounds", type=int, default=40, help="--drain 的最大轮数")
    parser.add_argument("--pause", type=int, default=120, help="--drain 轮间暂停秒数")
    parser.add_argument("--stop-when-dry", type=int, default=3,
                        help="--drain 连续几轮无新增就收工")
    args = parser.parse_args()

    log(f"灯塔采集流水线  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"库：{ROOT / 'data' / 'beacon.db'}  当前 announcements = {count()}")

    lock_note = clear_stale_lock()
    if lock_note:
        log(f"锁：{lock_note}")

    # 流水线必须自己持一把锁跑完整轮，而不是只清僵尸锁就继续。
    # 原来只调 clear_stale_lock()，它检测到"另一轮正在跑"之后照样往下走，于是
    # 两个 pipeline 并行：采集那步有 batch_collect 自己的锁挡着，可后面
    # prune_section_pages / dedup 这些会 DELETE 的步骤完全裸奔——实测就撞上了，
    # 第二个实例已经跑到 [dedup]，而第一个还在 collect。
    #
    # 也不能直接复用采集锁：流水线自己会调 batch_collect，那样会把自己挡在门外。
    # 所以另开一把，只管"有没有别的流水线在跑"。
    try:
        acquire("流水线", wait_seconds=0, lock_dir=PIPELINE_LOCK_DIR)
    except CollectLockBusy:
        log("已有一轮流水线在跑，本次不启动（避免两个进程同时删改同一个库）")
        return 0

    try:
        return _run(args)
    finally:
        release(PIPELINE_LOCK_DIR)


def _run(args) -> int:
    # 开跑前先探代理。代理配在 Windows 注册表里，requests 自动使用，采集代码
    # 自己完全不知道它挂了——通宵那晚 03:30 那轮就是这么白跑的：101 个源全部
    # ENOTFOUND，7.5 分钟跑完，退出码 0，体检报"无异常"，实际一个字节没采到。
    # 探测放在流水线这里（而不是 batch_collect 内部），因为流水线是计划任务的
    # 入口，它需要能以非零码退出、并把原因写进 overnight_*.log。
    proxy_ok, proxy_note = proxy_status()
    log(f"代理自检：{proxy_note}")
    if not proxy_ok:
        log("代理不通，本轮不采集。留着时间给下一个 2 小时切片，别拿假进度糊弄一夜。")
        return 2

    collect = [PY, "-u", "-B", "scripts/batch_collect.py", "--force",
               "--limit", str(args.limit), "--workers", str(args.workers),
               "--timeout", str(args.timeout)]
    if args.all_statuses:
        collect.append("--all-statuses")
    if args.drain:
        # 通宵采集要的是"取空为止"，不是"每源取 N 条就走"。一轮 limit 打满后
        # 源里还有新公告，所以要反复排空，连续几轮没新增才收工。
        collect += ["--rounds", str(args.rounds), "--pause", str(args.pause),
                    "--stop-when-dry", str(args.stop_when_dry)]

    steps = [
        ("collect", collect),
        ("clean", [PY, "-u", "-B", "scripts/prune_section_pages.py", "--apply"]),
        # 补抽必须在重算摘要之前：摘要的头部事实行（金额/截止/责任机构）读的是
        # 这些列，不先补上就是拿旧值去拼。
        ("refill", [PY, "-u", "-B", "scripts/refill_fields.py", "--apply"]),
        # 金额要在 summary 之前跑：摘要头部会带上「金额：…」，靠 amounts 表取值
        ("amounts", [PY, "-u", "-B", "scripts/extract_amounts.py", "--apply"]),
        ("summary", [PY, "-u", "-B", "scripts/resummarize.py", "--apply"]),
        ("classify", [PY, "-u", "-B", "scripts/reclassify.py", "--apply"]),
        ("dedup", [PY, "-u", "-B", "scripts/dedup.py"]),
        ("audit", [PY, "-u", "-B", "scripts/audit_summary_quality.py", "--show", "0"]),
    ]
    order = [s[0] for s in steps]
    start = order.index(args.only) if args.only else order.index(args.start)
    todo = steps[start:]

    if args.dry_run:
        log("\n将执行：")
        for name, argv in todo:
            log(f"  [{name}] {' '.join(argv[1:])}")
        return 0

    log(f"\n将执行 {len(todo)} 步：{' -> '.join(n for n, _ in todo)}")

    results = []
    total = count()
    for name, argv in todo:
        before = count()
        info = run_step(name, argv)
        after = count()
        info["records_before"] = before
        info["records_after"] = after
        if before is not None and after is not None:
            info["delta"] = after - before
            log(f"[{name}] 记录数 {before} -> {after}（{after - before:+d}）")
        results.append(info)
        total = after if after is not None else total

    failed = [r["step"] for r in results if not r["ok"]]
    state = {
        "ran_at": datetime.now().isoformat(timespec="seconds"),
        "results": results,
        "failed": failed,
        "announcements": total,
    }
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    log(f"\n{'=' * 66}")
    log(f"流水线结束  {datetime.now().strftime('%H:%M:%S')}  "
        f"announcements = {total}")
    for r in results:
        mark = "✓" if r["ok"] else "✗"
        delta = r.get("delta")
        extra = f"  记录{r['delta']:+d}" if isinstance(delta, int) and delta else ""
        log(f"  {mark} {r['step']:<9} {r['seconds']:>5}s{extra}"
            + (f"  {r.get('error', '')}" if r.get("error") else ""))
    if failed:
        log(f"\n失败步骤：{failed}")
        log(f"只重跑这些：python scripts/pipeline.py --only {failed[0]}")
    log(f"状态写入 {STATE}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
