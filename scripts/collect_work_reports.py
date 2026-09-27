"""采集政府工作报告并入库。

分三步，顺序不能换：
  1. 枚举归档栏目的历年报告链接（复用 BaseCollector 的 robots + 3s 限速）
  2. 抓正文、分节、抽指标与专题命中
  3. 入库 work_reports / work_report_metrics / work_report_topics

去重靠 (region_code, year)：政府工作报告一年一份，重跑只会覆盖，不会重复。

用法:
    python scripts/collect_work_reports.py                     # 全部地区
    python scripts/collect_work_reports.py --regions 上海,广州   # 指定地区
    python scripts/collect_work_reports.py --limit 3            # 每地区先试 3 篇
    python scripts/collect_work_reports.py --dry-run           # 只报不写
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, ".")

from collectors.work_report_collector import WorkReportCollector, build_report_records
from storage.db import Database
from work_report_sources import WORK_REPORT_SOURCES


def upsert_report(db: Database, record: dict) -> tuple[int, str]:
    """按 (region_code, year) 落库。返回 (report_id, inserted|updated)。"""
    from sqlalchemy import select

    from storage.models import WorkReport, WorkReportMetric, WorkReportTopic

    now = datetime.now(timezone.utc)
    with db.session() as session:
        existing = session.execute(
            select(WorkReport).where(
                WorkReport.region_code == record["region_code"],
                WorkReport.year == record["year"],
            )
        ).scalar_one_or_none()
        if existing is None:
            report = WorkReport(**record, fetched_at=now)
            session.add(report)
            session.flush()
            action = "inserted"
        else:
            # 正文或分节变化时覆盖，指标随后重建
            if existing.content_hash != record["content_hash"]:
                for key, value in record.items():
                    setattr(existing, key, value)
                existing.fetched_at = now
                action = "updated"
            else:
                return existing.id, "unchanged"
            report = existing
        session.flush()
        report_id = report.id
        # 指标与专题命中整体重建：抽取规则升级后旧值必须跟着变
        session.query(WorkReportMetric).filter(
            WorkReportMetric.report_id == report_id).delete()
        session.query(WorkReportTopic).filter(
            WorkReportTopic.report_id == report_id).delete()
        return report_id, action


def save_metrics(db: Database, report_id: int, metrics: list[dict], topics: list[dict]) -> None:
    from storage.models import WorkReportMetric, WorkReportTopic

    with db.session() as session:
        for m in metrics:
            session.add(WorkReportMetric(report_id=report_id, **m))
        for t in topics:
            session.add(WorkReportTopic(report_id=report_id, **t))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--regions", default="", help="逗号分隔的地区名，默认全部")
    parser.add_argument("--limit", type=int, default=None, help="每地区最多抓几份")
    parser.add_argument("--timeout", type=int, default=360, help="每地区预算秒")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    wanted = {r.strip() for r in args.regions.split(",") if r.strip()}
    sources = [s for s in WORK_REPORT_SOURCES
               if not wanted or s["region_name"] in wanted or s["id"] in wanted]

    blocked = [s for s in sources if s.get("blocked_reason")]
    sources = [s for s in sources if not s.get("blocked_reason")]
    for s in blocked:
        print(f"[受阻] {s['region_name']}：{s['blocked_reason']}", flush=True)

    db = None if args.dry_run else Database("data/beacon.db")
    totals = {"inserted": 0, "updated": 0, "unchanged": 0, "metrics": 0, "topics": 0}
    per_region: dict[str, dict] = {}

    for src in sources:
        source = dict(src)
        if args.timeout:
            source["deadline_seconds"] = args.timeout
        collector = WorkReportCollector(source)
        print(f"\n=== {src['region_name']}  {src['url']} ===", flush=True)
        try:
            raws = collector.collect(limit=args.limit)
        except Exception as exc:  # noqa: BLE001
            print(f"  采集失败 {type(exc).__name__}: {exc}")
            collector.close()
            continue
        print(f"  取到 {len(raws)} 份报告", flush=True)

        stats = {"reports": 0, "metrics": 0, "topics": 0, "sections": 0, "chars": 0,
                 "years": [], "errors": list(collector.errors)}
        for raw in raws:
            report, metrics, topics = build_report_records(
                raw, region_code=src["region_code"],
                region_name=src["region_name"], level=src.get("level", "市级"),
            )
            stats["reports"] += 1
            stats["metrics"] += len(metrics)
            stats["topics"] += len(topics)
            stats["sections"] += len(report["sections"] or [])
            stats["chars"] += report["char_count"]
            stats["years"].append(report["year"])
            if args.dry_run:
                print(f"    {report['year']}  {report['char_count']:>6}字  "
                      f"{len(report['sections'] or [])}节  指标{len(metrics)}  "
                      f"专题{len(topics)}  {report['title'][:26]}", flush=True)
                continue
            report_id, action = upsert_report(db, report)
            totals[action] = totals.get(action, 0) + 1
            if action != "unchanged":
                save_metrics(db, report_id, metrics, topics)
                totals["metrics"] += len(metrics)
                totals["topics"] += len(topics)
            print(f"    {report['year']}  {report['char_count']:>6}字  "
                  f"{len(report['sections'] or [])}节  指标{len(metrics)}  "
                  f"专题{len(topics)}  [{action}]", flush=True)
        collector.close()
        per_region[src["region_name"]] = stats
        years = sorted(stats["years"])
        if years:
            missing = [y for y in range(min(years), max(years) + 1) if y not in years]
            print(f"  年份 {min(years)}-{max(years)} 共{len(years)}年"
                  + (f"，缺 {missing}" if missing else "，连续"), flush=True)
        for err in stats["errors"][:3]:
            print(f"  ! {err['url'][-40:]} {err['error'][:60]}")

    print("\n" + "=" * 60)
    for name, s in per_region.items():
        print(f"  {name:<6} 报告 {s['reports']:>3}  平均 {s['chars']//max(1,s['reports']):>6} 字  "
              f"节 {s['sections']:>3}  指标 {s['metrics']:>4}  专题 {s['topics']:>3}")
    if not args.dry_run:
        print(f"\n  入库: {totals}")
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
