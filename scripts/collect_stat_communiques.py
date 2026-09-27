"""用统计公报补齐政府工作报告里缺失的指标，并做交叉验证。

三件事，顺序不能换：
  1. 抓各地区统计局年度《国民经济和社会发展统计公报》，抽指标
  2. 与报告原文抽出的同名指标放在一起（靠 provenance 区分证据强度），
     补上报告里没有的年份
  3. 同一 (地区, 年份, 指标) 出现两个不同值时**不自动取舍**，报出差异

为什么不自动挑一个：统计公报发布后，次年政府工作报告的"上年回顾"有时会给出
修订后的数字。这条修订痕迹本身是有价值的信息，自动覆盖就看不出修订发生过。

用法:
    python scripts/collect_stat_communiques.py              # 采集 + 补齐 + 交叉验证
    python scripts/collect_stat_communiques.py --dry-run    # 只报不写
    python scripts/collect_stat_communiques.py --verify-only  # 只做交叉验证
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, ".")

from collectors.work_report_collector import WorkReportCollector
from extractors.work_report import extract_body, normalize, parse_report_year
from stat_sources import STAT_SOURCES
from storage.db import Database
from storage.models import WorkReportMetric
from work_report_verify import (
    PROVENANCE_COMMUNIQUE,
    coverage_by_provenance,
    parse_communique,
    resolve_conflicts,
)

_LINK_RE = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def enumerate_communiques(source: dict, limit: int | None = None) -> list[tuple[int, str, str]]:
    """枚举归档页上的年度统计公报。返回 [(year, url, title)]。"""
    from urllib.parse import urljoin

    collector = WorkReportCollector(dict(source, deadline_seconds=900))
    try:
        html = collector._fetch_html(source["url"])
    finally:
        collector.close()
    needle = source.get("title_must_contain") or "国民经济和社会发展统计公报"
    out: list[tuple[int, str, str]] = []
    seen: set[str] = set()
    for href, inner in _LINK_RE.findall(html or ""):
        title = re.sub(r"\s+", " ", _TAG_RE.sub("", inner)).strip()
        if needle not in title:
            continue
        url = urljoin(source["url"], href.strip())
        if url in seen:
            continue
        seen.add(url)
        year = parse_report_year(title, url)
        if not year:
            continue
        out.append((year, url, title))
    out.sort(key=lambda item: item[0], reverse=True)
    return out[:limit] if limit else out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--regions", default="", help="逗号分隔地区名")
    parser.add_argument("--limit", type=int, default=None, help="每地区最多取几份公报")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify-only", action="store_true", help="跳过采集，只做交叉验证")
    args = parser.parse_args()

    db = None if (args.dry_run or args.verify_only) else Database("data/beacon.db")

    if not args.verify_only:
        wanted = {r.strip() for r in args.regions.split(",") if r.strip()}
        sources = [s for s in STAT_SOURCES
                   if (not wanted or s["region_name"] in wanted or s["id"] in wanted)
                   and not s.get("blocked_reason")]
        for s in STAT_SOURCES:
            if s.get("blocked_reason"):
                print(f"[待发现] {s['region_name']}：{s['blocked_reason']}", flush=True)

        for src in sources:
            print(f"\n=== {src['region_name']}  {src['url']} ===", flush=True)
            try:
                items = enumerate_communiques(src, limit=args.limit)
            except Exception as exc:  # noqa: BLE001
                print(f"  枚举失败 {type(exc).__name__}: {exc}")
                continue
            if not items:
                print("  归档页没有可识别的年度统计公报")
                continue
            years = [y for y, _u, _t in items]
            print(f"  枚举到 {len(items)} 份，年份 {min(years)}-{max(years)}", flush=True)

            for year, url, title in items:
                collector = WorkReportCollector(dict(src, deadline_seconds=120))
                try:
                    html = collector._fetch_html(url)
                except Exception as exc:  # noqa: BLE001
                    print(f"    {year} 抓取失败 {type(exc).__name__}: {str(exc)[:50]}")
                    collector.close()
                    continue
                collector.close()
                text = normalize(extract_body(html))
                metrics = parse_communique(
                    text, region_code=src["region_code"], year=year,
                    publisher=src["publisher"], url=url,
                )
                if args.dry_run or db is None:
                    print(f"    {year}  {len(text):>6}字  指标 {len(metrics):>2}  {title[:34]}")
                    continue
                # 指标挂在同地区同年份的报告行上（外键要求），没有对应报告就跳过
                with db.session() as session:
                    from sqlalchemy import select

                    from storage.models import WorkReport

                    report = session.execute(
                        select(WorkReport).where(
                            WorkReport.region_code == src["region_code"],
                            WorkReport.year == year,
                        )
                    ).scalar_one_or_none()
                    report_id = report.id if report is not None else None
                    if report_id is not None:
                        # 公报是补数，不覆盖报告原文抽出的值：只在该 (地区,年份,指标)
                        # 还没有任何记录时写入，冲突交给交叉验证去报
                        for m in metrics:
                            exists = session.execute(
                                select(WorkReportMetric).where(
                                    WorkReportMetric.report_id == report_id,
                                    WorkReportMetric.metric_key == m["metric_key"],
                                    WorkReportMetric.is_target == False,  # noqa: E712
                                )
                            ).scalars().first()
                            if exists is None:
                                session.add(WorkReportMetric(report_id=report_id, **m))
                print(f"    {year}  {len(text):>6}字  指标 {len(metrics):>2}  "
                      + ("" if report_id else "(无对应报告,未写入)") + f"  {title[:28]}")

    if db is not None:
        db.close()

    # ── 交叉验证 ──
    print("\n" + "=" * 64)
    print("交叉验证（同一地区/年份/指标出现两个不同值）")
    conflicts = resolve_conflicts()
    if not conflicts:
        print("  无冲突")
    for c in conflicts:
        print(f"  {c['region']} {c['year']} {c['metric']}  差异 {c['spread_pct']}%")
        for v in c["values"]:
            print(f"      {v['value']}{v['unit'] or '':<6} 来自 {v['provenance_label']}"
                  f"  口径 {v['caliber'] or '-'}")
    cov = coverage_by_provenance()
    print("\n指标来源分布")
    for label, count in (cov.get("by_provenance") or {}).items():
        print(f"  {label}: {count}")
    print("\n按指标统计（cells = 覆盖的地区-年份组合）")
    for row in (cov.get("by_metric") or []):
        print(f"  {row['metric_name']:<20} cells {row['cells']:>3}  其中来自公报 {row['from_communique']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
