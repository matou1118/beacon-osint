"""分批采集：每个源独立超时，卡住就跳过，不阻塞其他源。

用法: python scripts/batch_collect.py --limit 10
"""
import sys
import time
import argparse
import threading
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=10)
    parser.add_argument('--timeout', type=int, default=90, help='per-source timeout seconds')
    parser.add_argument('--sources', type=str, default='config/sources.yaml')
    args = parser.parse_args()

    sources = load_sources(args.sources)
    print(f"总源数: {len(sources)}, limit={args.limit}, timeout={args.timeout}s/source")

    all_records = []
    success = 0
    failed = 0
    skipped = 0
    source_stats = []

    now = datetime.now(timezone.utc)
    database = Database("data/beacon.db")

    for i, source in enumerate(sources):
        sid = source.get('id', '')
        name = source.get('name', '')[:15]
        start = time.time()

        # 检查是否 due
        if not database.source_due(sid, now):
            skipped += 1
            continue

        print(f"[{i+1}/{len(sources)}] {sid} ({name})...", end=' ', flush=True)

        result = collect_source_with_timeout(source, args.limit, args.timeout)
        elapsed = time.time() - start

        records = result["records"]
        error = result["error"]

        if error and not records:
            failed += 1
            print(f"FAIL ({elapsed:.0f}s): {error[:60]}")
            database.mark_source_result(
                source_id=sid, cadence_tier=source.get('cadence_tier', 'daily'),
                interval_seconds=int(source.get('cadence_seconds', 86400)),
                success=False, error=error[:200], now=now
            )
        elif records:
            success += 1
            print(f"OK {len(records)}条 ({elapsed:.0f}s)")
            # enrich
            source_map = {sid: source}
            enriched = enrich_records(records, source_map, use_gliner=False)
            all_records.extend(enriched)
            database.mark_source_result(
                source_id=sid, cadence_tier=source.get('cadence_tier', 'daily'),
                interval_seconds=int(source.get('cadence_seconds', 86400)),
                success=True, error=None, now=now
            )
        else:
            skipped += 1
            print(f"SKIP ({elapsed:.0f}s)")

    # 批量入库
    print(f"\n{'='*60}")
    print(f"采集完成: success={success}, failed={failed}, skipped={skipped}")
    print(f"总记录数: {len(all_records)}")

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
