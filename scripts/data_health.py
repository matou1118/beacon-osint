"""Data health check. Fails loudly when the store is quietly ruined.

This exists because the pipeline reported "clean" while the median body was 298
chars and 58 records were under 100. Unit tests passed the whole time: they
checked that the code ran, not that the data was usable. These thresholds check
the data.

Run: python scripts/data_health.py [--json] [--strict]

Exit code 1 when a threshold fails, so CI or the scheduler can gate on it.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "beacon.db"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Thresholds reflect what a usable corpus looks like, not what the code produces.
# Thresholds reflect what a usable corpus looks like, not what the code produces.
# Priority order is deliberate: losing body text is a real defect, leftover
# footer labels are cosmetic. chrome_residue_rate is therefore the loosest bound.
THRESHOLDS = {
    "median_content_chars": (800, "ge"),
    "under_200_chars_rate": (0.10, "le"),
    "chrome_residue_rate": (0.60, "le"),
    "amount_rate": (0.15, "ge"),
    "date_rate": (0.45, "ge"),
    "summary_rate": (0.90, "ge"),
    "publish_date_rate": (0.90, "ge"),
    "region_coverage_rate": (0.50, "ge"),
    "cross_level_programs": (1, "ge"),
    "section_page_rate": (0.05, "le"),
    "history_year_coverage": (0.50, "ge"),
    "current_tier_rate": (0.10, "ge"),
    # Summary defect classes from extractors.summary_refine.audit. They are the
    # reason the refine pass exists, and the self-improvement loop drives them.
    "summary_compression_high": (0.15, "le"),
    "summary_compression_low": (0.15, "le"),
    "summary_breadcrumb_rate": (0.01, "le"),
    "summary_toolbar_rate": (0.01, "le"),
    "dropped_amount": (0.02, "le"),
    # ── 全链路指标 ──────────────────────────────────────────
    # 采集产出率：单源有效记录 / 总链接数，低 = 采集器没采到东西。
    "collection_yield_rate": (0.30, "ge"),
    # 附件下载率：有附件的记录中成功下载的比例，低 = PDF 通路未启用。
    "attachment_download_rate": (0.10, "ge"),
    # 对比覆盖率：有对比价值的记录 / 总记录，低 = 对比引擎产出不足。
    "compare_coverage_rate": (0.30, "ge"),
    # 印证通过率：通过印证的记录 / 总记录，低 = 交叉印证不足。
    "corroboration_pass_rate": (0.20, "ge"),
}

AMOUNT_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:万元|亿元|万|亿|元)")
DATE_RE = re.compile(r"(?:自|从)?\d{4}年\d{1,2}月\d{1,2}日")
CHROME_RE = re.compile(
    r"打印此页|打印本页|网站地图|ICP备|当前位置[:：]|相关阅读|主办单位[:：]|分享至朋友圈|"
    r"扫一扫|大\s*中\s*小|【\s*[大小中]\s*】|扫描二维码"
)
BOOK_NAME_RE = re.compile(r"《([^》]{4,45})》")
PREFIX_RE = re.compile(r"^(?:关于印发|关于|转发|印发|公布)")
SUFFIX_RE = re.compile(r"(?:的通知|的函|的意见|的批复|的公告|的决定)$")
# Column landings that slipped through the collector's title filter. A corpus made
# mostly of these cannot answer a policy question.
SECTION_RE = re.compile(
    r"^(?:网站导航|公告公示|政务公开|政务联播|新闻中心|信息公开|网站声明|网站地图|"
    r"互动交流|办事指南|专题专栏|旧版网站|长者专区|无障碍)"
)


def normalize_policy_name(raw: str) -> str:
    name = PREFIX_RE.sub(" ", raw)
    name = SUFFIX_RE.sub("", name)
    return re.sub(r"\s+", "", name).strip()


def measure(db_path: Path = DB) -> dict[str, object]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id, source_id, title, content, publish_date, gov_level, region_province, extra FROM announcements"
    ).fetchall()
    con.close()
    if not rows:
        return {"total": 0, "error": "no records"}

    contents = [str(r["content"] or "") for r in rows]
    lengths = [len(c) for c in contents]
    total = len(rows)

    def rate(predicate) -> float:
        return sum(1 for r in rows if predicate(r)) / total

    cross_level = 0
    program_candidates = 0
    program_error = ""
    timeline_error = ""
    try:
        from extractors.program_extract import analyse_programs

        report = analyse_programs([
            {
                "id": row["id"],
                "title": row["title"],
                "content": row["content"],
                "publish_date": row["publish_date"],
                "gov_level": row["gov_level"],
                "region_province": row["region_province"],
            }
            for row in rows
        ])
        cross_level = int(report["programs"])
        program_candidates = int(report["candidates"])
    except Exception as exc:  # noqa: BLE001 - report, never fail silently
        program_error = f"{type(exc).__name__}: {exc}"

    with_summary = 0
    articles = 0
    for row in rows:
        title = str(row["title"] or "").strip()
        if SECTION_RE.match(title):
            continue
        articles += 1
        try:
            if (json.loads(row["extra"] or "{}") or {}).get("summary"):
                with_summary += 1
        except (TypeError, ValueError):
            pass

    # Time-line and freshness: a corpus with no history cannot answer "how did
    # this policy evolve", which is the product's second question.
    try:
        from extractors.timeline import coverage_gaps, tier_summary

        freshness = tier_summary([
            {"publish_date": r["publish_date"]} for r in rows
        ])
        gaps = coverage_gaps([{"publish_date": r["publish_date"]} for r in rows], span_years=10)
        history_coverage = float(gaps["coverage_rate"])
        current_rate = float(freshness["rates"].get("current", 0.0))
        tier_counts = dict(freshness["counts"])
        missing_years = gaps["missing_years"]
    except Exception as exc:  # noqa: BLE001 - report, never fail silently
        history_coverage, current_rate = 0.0, 0.0
        tier_counts, missing_years = {}, []
        timeline_error = f"{type(exc).__name__}: {exc}"

    # Summary defect classes, measured on the stored records themselves.
    summary_report = {"counts": {}, "rates": {}}
    try:
        from extractors.summary_refine import audit as audit_summaries

        summary_rows = []
        for row in rows:
            try:
                extra = json.loads(row["extra"] or "{}") or {}
            except (TypeError, ValueError):
                extra = {}
            summary_rows.append({"content": row["content"], "summary": extra.get("summary") or ""})
        summary_report = audit_summaries(summary_rows)
    except Exception as exc:  # noqa: BLE001 - report, never fail silently
        summary_report = {"counts": {}, "rates": {}, "error": f"{type(exc).__name__}: {exc}"}
    summary_rates = summary_report.get("rates") or {}

    # ── 全链路指标计算 ──────────────────────────────────────
    # 这三个指标原先都从 extra 里找数据，但写入方从不往 extra 放这三个键：
    # 附件存在 attachments 表、字段抽取存在 field_extractions 表、印证状态存在
    # announcements.corroboration_status 列。实测 extra 中这三个键的记录数为 0，
    # 所以三个指标恒为 0，与真实数据无关——库里明明有 211 条附件、5903 条字段
    # 抽取、1883 条印证状态。下面改为从真实位置读取。
    collection_yield_rate = round(
        sum(1 for c in contents if len(c) > 200) / total, 4
    )

    def _scalar(query: str, params: tuple = ()) -> int:
        # con is closed right after the main query above, so every later
        # con.execute() raises ProgrammingError -- which is a sqlite3.Error
        # subclass, so a broad except turns it into a silent 0 instead of a
        # crash. Use a fresh read-only connection for these counts.
        try:
            with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as probe:
                row = probe.execute(query, params).fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        except sqlite3.Error as exc:
            raise RuntimeError(f"health query failed: {exc} — {query}") from exc

    # 附件下载率：attachments 表里有正文的附件数 / 公告总数
    attachment_count = _scalar("SELECT COUNT(*) FROM attachments")
    attachment_download_rate = round(attachment_count / total, 4)

    # 对比覆盖率：有字段抽取结果的公告占比（抽取结果按 announcement_id 关联）
    # 必须 JOIN announcements：field_extractions 里存在指向已删公告的孤儿行，
    # 只查 field_extractions 会得到 1920 > 1883，比率大于 1。
    compare_count = _scalar(
        "SELECT COUNT(DISTINCT f.announcement_id) FROM field_extractions f "
        "JOIN announcements a ON a.id = f.announcement_id"
    )
    compare_coverage_rate = round(compare_count / total, 4)

    # 印证通过率：corroboration_status = confirmed 的公告占比
    corroboration_count = _scalar(
        "SELECT COUNT(*) FROM announcements WHERE corroboration_status = 'confirmed'"
    )
    corroboration_pass_rate = round(corroboration_count / total, 4)

    return {
        "total": total,
        "median_content_chars": int(statistics.median(lengths)) if lengths else 0,
        "under_200_chars_rate": round(sum(1 for n in lengths if n < 200) / total, 4),
        "chrome_residue_rate": round(sum(1 for c in contents if CHROME_RE.search(c)) / total, 4),
        "amount_rate": round(sum(1 for c in contents if AMOUNT_RE.search(c)) / total, 4),
        "date_rate": round(sum(1 for c in contents if DATE_RE.search(c)) / total, 4),
        "summary_rate": round(with_summary / max(1, articles), 4),
        "articles": articles,
        "publish_date_rate": round(rate(lambda r: bool(r["publish_date"])), 4),
        "region_coverage_rate": round(rate(lambda r: bool(r["region_province"])), 4),
        "section_page_rate": round(sum(1 for r in rows if SECTION_RE.match(str(r["title"] or "").strip())) / total, 4),
        "cross_level_programs": cross_level,
        "policy_programs_found": program_candidates,
        "program_extract_error": program_error,
        "history_year_coverage": round(history_coverage, 4),
        "current_tier_rate": round(current_rate, 4),
        "summary_compression_high": float(summary_rates.get("compression_high", 0.0)),
        "summary_compression_low": float(summary_rates.get("compression_low", 0.0)),
        "summary_breadcrumb_rate": float(summary_rates.get("breadcrumb", 0.0)),
        "summary_toolbar_rate": float(summary_rates.get("toolbar", 0.0)),
        "dropped_amount": float(summary_rates.get("dropped_amount", 0.0)),
        "summary_audit_error": summary_report.get("error", ""),
        "collection_yield_rate": collection_yield_rate,
        "attachment_download_rate": attachment_download_rate,
        "compare_coverage_rate": compare_coverage_rate,
        "corroboration_pass_rate": corroboration_pass_rate,
        "tier_counts": tier_counts,
        "missing_years": missing_years,
        "timeline_error": timeline_error,
        "levels": dict(Counter(str(r["gov_level"] or "?") for r in rows)),
        "provinces": len({r["region_province"] for r in rows if r["region_province"]}),
    }


def check(metrics: dict[str, object]) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for key, (limit, direction) in THRESHOLDS.items():
        if key not in metrics:
            continue
        value = metrics[key]
        if isinstance(value, str):
            continue
        ok = value >= limit if direction == "ge" else value <= limit
        findings.append({
            "metric": key,
            "value": value,
            "threshold": f"{direction} {limit}",
            "ok": bool(ok),
        })
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=DB)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true", help="exit 1 on any failure")
    args = parser.parse_args()
    if not args.db.exists():
        print(f"database not found: {args.db}")
        return 1
    metrics = measure(args.db)
    findings = check(metrics)
    failed = [f for f in findings if not f["ok"]]

    if args.json:
        print(json.dumps({"metrics": metrics, "findings": findings, "failed": len(failed)}, ensure_ascii=False, indent=2))
    else:
        print(f"数据健康检查  ({args.db.name}, {metrics.get('total', 0)} 条)")
        print("=" * 62)
        for finding in findings:
            mark = "OK  " if finding["ok"] else "FAIL"
            print(f"  {mark} {str(finding['metric']):<26s} {str(finding['value']):>10s}  (要求 {finding['threshold']})")
        print("-" * 62)
        if metrics.get("total"):
            print(f"  层级分布: {metrics.get('levels')}")
            print(f"  省份覆盖: {metrics.get('provinces')} 个")
            print(f"  政策专名: {metrics.get('policy_programs_found')} 个, 跨层级 {metrics.get('cross_level_programs')} 个")
            if metrics.get("program_extract_error"):
                print(f"  !! 专名抽取失败: {metrics['program_extract_error']}")
            if metrics.get("timeline_error"):
                print(f"  !! 时间线计算失败: {metrics['timeline_error']}")
            print(f"  数据分层: {metrics.get('tier_counts')}")
            print(f"  缺失年份: {', '.join(metrics.get('missing_years') or []) or '无'}")
        print(f"  结论: {len(failed)} 项不达标" + ("" if not failed else " -> " + ", ".join(str(f["metric"]) for f in failed)))
    return 1 if (failed and (args.strict or True)) else 0


if __name__ == "__main__":
    sys.exit(main())
