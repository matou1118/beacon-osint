"""分批采集：每个源独立超时，卡住就跳过，不阻塞其他源。

多个源并发采集。限速下限（collectors/base.py 的 min_interval_seconds）是按源
实例计的，也就是按域名计的——不同域名之间并不需要互相等待，所以并发不会
降低对任何单个站点的礼貌程度。

用法: python scripts/batch_collect.py --limit 60 --workers 6
"""
import sys
import time
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, '.')

from main import load_sources, collector_for, enrich_records, load_rules
from rules.corroboration import corroborate_records
from storage.db import Database
from datetime import datetime, timezone

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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=10)
    parser.add_argument('--timeout', type=int, default=90, help='per-source budget seconds')
    parser.add_argument('--sources', type=str, default='config/sources.yaml')
    parser.add_argument('--workers', type=int, default=6, help='sources collected in parallel')
    parser.add_argument('--force', action='store_true', help='ignore next-due time')
    args = parser.parse_args()

    sources = load_sources(args.sources)
    workers = max(1, min(args.workers, len(sources) or 1))
    print(f"总源数: {len(sources)}, limit={args.limit}, budget={args.timeout}s/source, workers={workers}")

    all_records = []
    success = 0
    failed = 0
    skipped = 0
    source_stats = []

    now = datetime.now(timezone.utc)
    database = Database("data/beacon.db")

    # due 检查留在主线程串行做：它是纯读，且要在派发前决定谁参与本轮。
    todo = []
    for source in sources:
        sid = source.get('id', '')
        if not args.force and not database.source_due(sid, now):
            skipped += 1
            continue
        todo.append(source)

    if not todo:
        print("没有到期的源。用 --force 可以忽略调度时间强制采集。")
        database.close()
        return 0

    print(f"本轮到期 {len(todo)} 个源，并发 {workers} 路\n")
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
                print(f"[{done}/{len(todo)}] {source.get('id','')} ({name}) EXC: {exc}")
                continue

            if enriched:
                success += 1
                print(f"[{done}/{len(todo)}] {sid} ({name}) OK {len(enriched)}条 ({elapsed:.0f}s)")
                all_records.extend(enriched)
                source_stats.append((sid, len(enriched), elapsed))
            elif error:
                failed += 1
                print(f"[{done}/{len(todo)}] {sid} ({name}) FAIL ({elapsed:.0f}s): {error[:70]}")
            else:
                skipped += 1
                print(f"[{done}/{len(todo)}] {sid} ({name}) SKIP ({elapsed:.0f}s)")

    wall = time.time() - started
    # 批量入库
    print(f"\n{'='*60}")
    print(f"采集完成: success={success}, failed={failed}, skipped={skipped}")
    print(f"总记录数: {len(all_records)}")
    print(f"墙钟耗时: {wall/60:.1f} 分钟（串行约需 {sum(s[2] for s in source_stats)/60:.0f} 分钟）")
    if source_stats:
        rate = len(all_records) / wall * 60 if wall else 0
        print(f"吞吐: {rate:.1f} 条/分钟")

    if all_records:
        # 印证
        rules = load_rules('config/rules.yaml')
        all_records = corroborate_records(all_records, rules)

        # 入库
        storage_result = database.upsert_announcements(all_records)
        extraction_result = database.save_field_extractions(all_records)
        print(f"入库: {storage_result}")
        print(f"字段抽取: {extraction_result}")

    database.close()
    print(f"\n最终 announcements 数:")
    import sqlite3
    c = sqlite3.connect('data/beacon.db').cursor()
    c.execute('SELECT COUNT(*) FROM announcements')
    print(f"  {c.fetchone()[0]}")


if __name__ == '__main__':
    main()
