"""分批采集：每个源独立超时，卡住就跳过，不阻塞其他源。

多个源并发采集。限速下限（collectors/base.py 的 min_interval_seconds）是按源
实例计的，也就是按域名计的——不同域名之间并不需要互相等待，所以并发不会
降低对任何单个站点的礼貌程度。

用法: python scripts/batch_collect.py --limit 60 --workers 6
"""
import sys
import time
import argparse
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, '.')

from main import load_sources, collector_for, enrich_records, load_rules
from rules.corroboration import corroborate_records
from collectors.lock import collect_lock
from storage.db import Database

ROUNDS_LOG = Path('data/rounds.jsonl')


def say(message: str) -> None:
    """Print and flush immediately.

    A multi-hour run writes to a redirected log; block buffering hid every
    progress line until the buffer filled, so a stalled run looked like silence.
    """
    print(message, flush=True)


def record_round(summary: dict) -> None:
    """Append one line per round so throughput can be reported later."""
    summary["at"] = datetime.now(timezone.utc).isoformat()
    ROUNDS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with ROUNDS_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(summary, ensure_ascii=False) + "\n")

def collect_source_with_timeout(source, limit, timeout_sec=90):
    """采集单个源，超时返回空。"""
    result = {"records": [], "error": None, "time": 0}
    sid = source.get('id', '')

    def worker():
        try:
            collector = collector_for(source)
            if not collector:
                result["error"] = "no collector"
                return
            try:
                records = collector.collect(limit=limit)
                result["records"] = records
            finally:
                collector.close()
        except Exception as e:
            result["error"] = str(e)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout=timeout_sec)

    if t.is_alive():
        result["error"] = f"timeout after {timeout_sec}s"
        result["records"] = []

    return result


def collect_one(source, limit, timeout_sec, now, database):
    """采集单个源并写回调度状态。返回 (source_id, records, error, elapsed)。"""
    sid = source.get('id', '')
    start = time.time()
    # 采集器自己看表：到点就返回已采到的部分，而不是被外层丢掉整份结果。
    # 外层的线程超时只作看门狗用，正常情况下不会先触发。
    source = dict(source)
    source['deadline_seconds'] = timeout_sec
    result = collect_source_with_timeout(source, limit, timeout_sec + 60)
    elapsed = time.time() - start
    records, error = result["records"], result["error"]

    try:
        database.mark_source_result(
            source_id=sid, cadence_tier=source.get('cadence_tier', 'daily'),
            interval_seconds=int(source.get('cadence_seconds', 86400)),
            success=bool(records) and not error, error=(error or None) and error[:200], now=now
        )
    except Exception as exc:
        error = error or f"mark_source_result failed: {exc}"
    return sid, records, error, elapsed


def run_round(args, sources, round_no, total_rounds):
    """One full pass over the due sources. Returns a summary dict."""
    workers = max(1, min(args.workers, len(sources) or 1))
    prefix = f"[轮 {round_no}/{total_rounds}] " if total_rounds > 1 else ""

    all_records = []
    success = 0
    failed = 0
    skipped = 0
    source_stats = []

    now = datetime.now(timezone.utc)
    database = Database("data/beacon.db")

    # due 检查留在主线程串行做：它是纯读，且要在派发前决定谁参与本轮。
    todo = []
    not_enabled = 0
    for source in sources:
        sid = source.get('id', '')
        # --force only overrides the next-due time, never the registry status.
        # Without this the pass walked all 145 registered sources: the 57 marked
        # blocked and 36 pending_reprobe were attempted every round, so most of
        # the concurrency budget went to sources that cannot succeed.
        if not args.all_statuses and not (source.get('status') == 'enabled' and source.get('enabled', True)):
            not_enabled += 1
            continue
        if not args.force and not database.source_due(sid, now):
            skipped += 1
            continue
        todo.append(source)

    if not_enabled:
        say(prefix + f"按登记表跳过 {not_enabled} 个非 enabled 源"
                     f"（加 --all-statuses 可一并尝试）")
    if not todo:
        say(prefix + "没有到期的源。用 --force 可以忽略调度时间强制采集。")
        database.close()
        return {"round": round_no, "todo": 0, "success": 0, "failed": 0,
                "skipped": skipped, "records": 0, "wall_seconds": 0.0,
                "inserted": 0, "updated": 0, "unchanged": 0}

    say(prefix + f"总源数 {len(sources)}，本轮到期 {len(todo)}，并发 {workers} 路，"
                 f"每源预算 {args.timeout}s，limit={args.limit}")
    started = time.time()

    def run(source):
        sid, records, error, elapsed = collect_one(
            source, args.limit, args.timeout, now, database
        )
        if records:
            enriched = enrich_records(records, {sid: source}, use_gliner=False)
        else:
            enriched = []
        return sid, source, enriched, error, elapsed

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run, source): source for source in todo}
        for future in as_completed(futures):
            source = futures[future]
            done += 1
            name = source.get('name', '')[:15]
            try:
                sid, _src, enriched, error, elapsed = future.result()
            except Exception as exc:
                failed += 1
                say(prefix + f"[{done}/{len(todo)}] {source.get('id','')} ({name}) EXC: {exc}")
                continue

            if enriched:
                success += 1
                say(prefix + f"[{done}/{len(todo)}] {sid} ({name}) OK {len(enriched)}条 ({elapsed:.0f}s)")
                all_records.extend(enriched)
                source_stats.append((sid, len(enriched), elapsed))
            elif error:
                failed += 1
                say(prefix + f"[{done}/{len(todo)}] {sid} ({name}) FAIL ({elapsed:.0f}s): {error[:70]}")
            else:
                skipped += 1
                say(prefix + f"[{done}/{len(todo)}] {sid} ({name}) SKIP ({elapsed:.0f}s)")

    wall = time.time() - started
    serial_minutes = sum(s[2] for s in source_stats) / 60
    say(prefix + "-" * 52)
    say(prefix + f"本轮 success={success} failed={failed} skipped={skipped}，"
                 f"取回 {len(all_records)} 条")
    say(prefix + f"墙钟 {wall/60:.1f} 分钟（串行需 {serial_minutes:.0f} 分钟，"
                 f"加速 {serial_minutes/max(wall/60, 0.01):.1f}x）")
    if wall:
        say(prefix + f"吞吐 {len(all_records)/wall*60:.1f} 条/分钟")

    storage = {"inserted": 0, "updated": 0, "unchanged": 0}
    if all_records:
        rules = load_rules('config/rules.yaml')
        all_records = corroborate_records(all_records, rules)
        storage = database.upsert_announcements(all_records)
        extraction_result = database.save_field_extractions(all_records)
        say(prefix + f"入库 {storage}")
        say(prefix + f"字段抽取 {extraction_result}")

    database.close()
    summary = {
        "round": round_no,
        "todo": len(todo),
        "success": success,
        "failed": failed,
        "skipped": skipped,
        "records": len(all_records),
        "wall_seconds": round(wall, 1),
        "records_per_minute": round(len(all_records) / wall * 60, 1) if wall else 0.0,
        "inserted": storage.get("inserted", 0),
        "updated": storage.get("updated", 0),
        "unchanged": storage.get("unchanged", 0),
    }
    record_round(summary)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=10)
    parser.add_argument('--timeout', type=int, default=90, help='per-source budget seconds')
    parser.add_argument('--sources', type=str, default='config/sources.yaml')
    parser.add_argument('--workers', type=int, default=6, help='sources collected in parallel')
    parser.add_argument('--force', action='store_true', help='ignore next-due time')
    parser.add_argument('--all-statuses', action='store_true',
                        help='also attempt blocked / pending_reprobe sources')
    parser.add_argument('--rounds', type=int, default=1, help='repeat the pass N times')
    parser.add_argument('--pause', type=int, default=0, help='seconds to wait between rounds')
    parser.add_argument('--stop-when-dry', type=int, default=0,
                        help='stop after N consecutive rounds that insert no new records '
                             '(0 = never stop). Enabled sources yield a fixed set at the '
                             'current max_items/archive_pages, so repeating the pass adds '
                             'nothing while re-hitting the same government sites all night.')
    args = parser.parse_args()

    # 三个采集入口共用一个 SQLite，写入会互相覆盖 source_sync_state。
    # 定时任务周期性触发，拿不到锁就跳过本轮，不排队。
    with collect_lock("公告批量采集") as locked:
        if not locked:
            return 0
        return _run(args)


def _run(args) -> int:
    sources = load_sources(args.sources)
    say(f"源总数 {len(sources)} | limit={args.limit} | 每源预算 {args.timeout}s | "
        f"并发 {args.workers} | 轮数 {args.rounds}")

    total_rounds = max(1, args.rounds)
    say(f"源总数 {len(sources)} | limit={args.limit} | 每源预算 {args.timeout}s | "
        f"并发 {args.workers} | 轮数 {total_rounds}"
        + (f" | 连续 {args.stop_when_dry} 轮无新增即停" if args.stop_when_dry else ""))

    dry_streak = 0
    for round_no in range(1, total_rounds + 1):
        say("=" * 66)
        summary = {"round": round_no, "inserted": -1}
        try:
            summary = run_round(args, sources, round_no, total_rounds)
        except Exception as exc:  # noqa: BLE001 - an overnight run must survive a bad round
            say(f"轮 {round_no} 异常中止: {type(exc).__name__}: {exc}")
            import traceback
            say(traceback.format_exc())
        inserted = int(summary.get("inserted", -1))
        if inserted > 0:
            dry_streak = 0
        elif inserted == 0:
            dry_streak += 1
        if args.stop_when_dry and dry_streak >= args.stop_when_dry:
            say(f"连续 {dry_streak} 轮没有新增记录，停止。"
                f"要继续拿量需提高 max_items/archive_pages 或解冻待审源。")
            break
        if round_no < total_rounds and args.pause:
            say(f"轮间等待 {args.pause}s ...")
            time.sleep(args.pause)

    say("=" * 66)
    say(f"全部轮次结束，{datetime.now(timezone.utc).isoformat()}")
    import sqlite3
    c = sqlite3.connect('data/beacon.db').cursor()
    c.execute('SELECT COUNT(*) FROM announcements')
    say(f"announcements 总数: {c.fetchone()[0]}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
