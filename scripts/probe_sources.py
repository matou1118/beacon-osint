from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from main import load_crawl4ai_schemas, load_sources

# ── 断点续跑：每源完成即追加写 JSONL ──────────────────────────
PROBE_LOG = ROOT / "data" / "probe_results.jsonl"



# ── 断点续跑：每源完成即追加写 JSONL ──────────────────────────
PROBE_LOG = ROOT / "data" / "probe_results.jsonl"


def load_completed_probes() -> dict[str, dict]:
    """已探测的源结果，用于断点续跑时跳过。"""
    if not PROBE_LOG.exists():
        return {}
    done: dict[str, dict] = {}
    for line in PROBE_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            sid = str(row.get("source_id") or "")
            if sid:
                done[sid] = row
        except ValueError:
            continue
    return done


def append_probe_result(result: dict) -> None:
    """每源完成即追加写一行 JSONL，被中断不丢结果。"""
    PROBE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with PROBE_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result, ensure_ascii=False) + "\n")


def probe_one_source(source: dict, args: argparse.Namespace) -> dict:
    """探测单个源，完成后立即写 JSONL。"""
    source_id = str(source.get("id") or "")
    command = [
        sys.executable, str(ROOT / "main.py"),
        "--config", args.config,
        "--crawl4ai-schemas", args.schemas,
        "--db", args.db,
        "--limit", str(args.limit),
        "--force",
        "--markdown-output", args.markdown_output,
        "--heatmap-output", args.heatmap_output,
        "--source", source_id,
    ]
    if args.include_pending:
        command.append("--include-pending")
    completed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True,
        timeout=args.timeout, check=False,
    )
    result: dict = {
        "source_id": source_id,
        "level": source.get("level"),
        "mode": source_mode(source),
        "url": source.get("url"),
        "status": source.get("status"),
        "exit_code": completed.returncode,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    if completed.stdout.strip():
        try:
            raw = json.loads(completed.stdout)
            if isinstance(raw, dict):
                result["records"] = len(raw.get("records") or [])
                result["errors"] = list(raw.get("sources") or [{}])[0].get("errors", []) if raw.get("sources") else []
        except json.JSONDecodeError:
            pass
    if completed.stderr.strip():
        result["stderr"] = completed.stderr[-2000:]
    append_probe_result(result)
    return result


def source_mode(source: dict) -> str:
    if source.get("type") == "media" or source.get("media_type"):
        return "MEDIA"
    method = str(source.get("access_method") or "").lower()
    if method in {"api", "government_api", "json_api"} or source.get("list_endpoint") or source.get("api_kind"):
        return "API"
    if method == "rss" or source.get("rss_url"):
        return "RSS"
    return "WEB"


def failure_reason(errors: list[dict]) -> str:
    text = " ".join(str(item.get("error") or "") for item in errors).lower()
    if "robots.txt" in text:
        return "robots_denied"
    if "anti-bot" in text or "antibot" in text:
        return "anti_bot"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    if "http 403" in text:
        return "http_403"
    if "http 404" in text:
        return "http_404"
    if "http 412" in text:
        return "http_412"
    if "http 5" in text:
        return "http_5xx"
    if "selector" in text or "detail" in text or "parse" in text:
        return "parse_or_selector"
    return "other"


def build_probe_summary(selected_sources: list[dict], result: dict | None) -> dict:
    result = result or {}
    outcomes = {str(item.get("source_id") or ""): item for item in result.get("sources") or [] if isinstance(item, dict)}
    by_mode = {mode: {"sources": 0, "success": 0, "failed": 0, "records": 0} for mode in ("API", "RSS", "WEB", "MEDIA")}
    by_level = {level: {"sources": 0, "success": 0, "failed": 0, "records": 0} for level in ("national", "provincial", "municipal", "county")}
    reasons: dict[str, int] = {}
    failed_sources = []
    total_records = int(result.get("record_count") or 0)
    for source in selected_sources:
        source_id = str(source.get("id") or "")
        mode = source_mode(source)
        level = str(source.get("level") or "unknown")
        outcome = outcomes.get(source_id, {})
        errors = list(outcome.get("errors") or [])
        by_mode[mode]["sources"] += 1
        by_level.setdefault(level, {"sources": 0, "success": 0, "failed": 0, "records": 0})["sources"] += 1
        if errors:
            reason = failure_reason(errors)
            reasons[reason] = reasons.get(reason, 0) + 1
            by_mode[mode]["failed"] += 1
            by_level.setdefault(level, {"sources": 0, "success": 0, "failed": 0, "records": 0})["failed"] += 1
            failed_sources.append({"source_id": source_id, "mode": mode, "reason": reason, "error": errors[0].get("error", "")})
        else:
            by_mode[mode]["success"] += 1
            by_level.setdefault(level, {"sources": 0, "success": 0, "failed": 0, "records": 0})["success"] += 1
        record_count = int(outcome.get("records") or 0)
        by_mode[mode]["records"] += record_count
        by_level.setdefault(level, {"sources": 0, "success": 0, "failed": 0, "records": 0})["records"] += record_count
    plan = []
    for reason in reasons:
        if reason == "robots_denied":
            plan.append("robots_denied：保持来源禁用，改用官方 RSS/API/公开列表，不绕过 robots。")
        elif reason == "anti_bot":
            plan.append("anti_bot：保留失败记录，优先寻找官方 API/RSS 或静态公开入口，不启用 stealth/绕过。")
        elif reason in {"http_403", "http_412"}:
            plan.append(f"{reason}：核对公开访问策略、来源 URL 和频率，优先切换官方 API/RSS，不做请求伪装。")
        elif reason == "http_404":
            plan.append("http_404：修正列表选择器、detail_pattern 和失效链接过滤规则。")
        elif reason == "timeout":
            plan.append("timeout：降低并发、拆分批次并为该源单独设置合理超时和退避。")
        elif reason == "parse_or_selector":
            plan.append("parse_or_selector：补充真实页面 CSS 选择器、最小正文长度和 needs_review 规则。")
        else:
            plan.append("other：查看脱敏错误报告，补齐来源级测试夹具后再启用。")
    if not plan:
        plan.append("本轮没有失败源；保持来源 pending_review，完成数据质量和字段回归后再启用。")
    corroboration = result.get("corroboration") or {}
    return {"total_sources": len(selected_sources), "total_records": total_records, "by_mode": by_mode, "by_level": by_level, "failed_sources": failed_sources, "failure_reasons": reasons, "improvement_plan": plan, "extraction_total": int(result.get("extraction_total") or 0), "extraction_success": int(result.get("extraction_success") or 0), "corroborated": int(corroboration.get("corroborated") or 0), "corroboration_single_source": int(corroboration.get("single_source") or 0)}


def render_probe_summary(summary: dict, output_path: str | Path) -> None:
    lines = ["# 一轮采集统计", "", f"- 来源总数：{summary['total_sources']}", f"- 记录总数：{summary['total_records']}", f"- 抽取成功：{summary['extraction_success']}/{summary['extraction_total']}", f"- 交叉验证：{summary.get('corroborated', 0)} 条 corroborated / {summary.get('corroboration_single_source', 0)} 条 single_source", "", "## 按协议统计", "", "| 模式 | 来源 | 成功 | 失败 | 记录 |", "|---|---:|---:|---:|---:|"]
    for mode, values in summary["by_mode"].items():
        lines.append(f"| {mode} | {values['sources']} | {values['success']} | {values['failed']} | {values['records']} |")
    lines.extend(["", "## 按行政层级统计", "", "| 层级 | 来源 | 成功 | 失败 | 记录 |", "|---|---:|---:|---:|---:|"])
    for level, values in summary.get("by_level", {}).items():
        lines.append(f"| {level} | {values['sources']} | {values['success']} | {values['failed']} | {values['records']} |")
    lines.extend(["", "## 失败原因", "", "| 原因 | 源数 |", "|---|---:|"])
    for reason, count in sorted(summary["failure_reasons"].items()):
        lines.append(f"| {reason} | {count} |")
    lines.extend(["", "## 改进方案", ""])
    lines.extend(f"- {item}" for item in summary["improvement_plan"])
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe approved sources through the real collection pipeline")
    parser.add_argument("--config", default="config/sources.yaml")
    parser.add_argument("--schemas", default="config/crawl4ai_schemas.yaml")
    parser.add_argument("--db", default="data/probes/beacon.db")
    parser.add_argument("--markdown-output", default="data/probes/markdown")
    parser.add_argument("--report", default="data/probes/probe-report.json")
    parser.add_argument("--heatmap-output", default="data/probes/heatmap.md")
    parser.add_argument("--source", action="append", dest="source_ids")
    parser.add_argument("--level", action="append", choices=["national", "provincial", "municipal", "county"])
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--include-pending", action="store_true")
    parser.add_argument("--resume", action="store_true", help="从断点续跑，跳过已探测的源")
    parser.add_argument("--summarize-report")
    args = parser.parse_args()
    if args.summarize_report:
        report_path = Path(args.summarize_report)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        source_rows = [{"id": item.get("source_id"), "level": item.get("level"), "access_method": {"API": "api", "RSS": "rss", "WEB": "static_html", "MEDIA": "static_html"}.get(str(item.get("mode") or "WEB"), "static_html")} for item in report.get("sources") or []]
        summary = build_probe_summary(source_rows, report.get("result"))
        report["summary"] = summary
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        summary_path = report_path.with_name(f"{report_path.stem}-summary.md")
        render_probe_summary(summary, summary_path)
        print(json.dumps({"report": str(report_path), "summary": str(summary_path), "sources": summary["total_sources"], "records": summary["total_records"], "failed_sources": len(summary["failed_sources"]), "by_mode": {mode: values["sources"] for mode, values in summary["by_mode"].items()}}, ensure_ascii=True))
        return 0
    sources = load_sources(args.config, load_crawl4ai_schemas(args.schemas))
    if args.source_ids:
        wanted = set(args.source_ids)
        sources = [source for source in sources if str(source.get("id")) in wanted]
    if args.level:
        levels = set(args.level)
        sources = [source for source in sources if source.get("level") in levels]
    if not args.include_pending:
        sources = [source for source in sources if source.get("status") == "enabled" and source.get("enabled", True)]

    # ── 断点续跑：跳过已探测的源 ──
    if args.resume:
        done = load_completed_probes()
        before = len(sources)
        sources = [s for s in sources if str(s.get("id") or "") not in done]
        skipped = before - len(sources)
        if skipped:
            print(f"断点续跑：跳过已探测 {skipped} 个源，剩余 {len(sources)} 个", file=sys.stderr)

    if not sources:
        print("没有待探测的源（全部已完成或被过滤）", file=sys.stderr)
        return 0

    # ── 逐源探测：每源完成即写 JSONL，被中断不丢结果 ──
    all_results: list[dict] = []
    for i, source in enumerate(sources, 1):
        sid = str(source.get("id") or "")
        print(f"[{i}/{len(sources)}] 探测 {sid}...", file=sys.stderr)
        try:
            result = probe_one_source(source, args)
            all_results.append(result)
        except subprocess.TimeoutExpired:
            all_results.append({"source_id": sid, "exit_code": -1, "error": "timeout"})
        except Exception as exc:  # noqa: BLE001
            all_results.append({"source_id": sid, "exit_code": -1, "error": str(exc)})

    # 汇总报告
    summary = build_probe_summary(sources, {"sources": all_results, "record_count": sum(r.get("records", 0) for r in all_results)})
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "include_pending": args.include_pending,
        "limit": args.limit,
        "sources": [{"source_id": s.get("id"), "level": s.get("level"), "mode": source_mode(s), "url": s.get("url"), "status": s.get("status")} for s in sources],
        "results": all_results,
        "summary": summary,
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_path = report_path.with_name(f"{report_path.stem}-summary.md")
    render_probe_summary(summary, summary_path)
    print(json.dumps({"report": str(report_path), "summary": str(summary_path), "sources": len(sources), "records": int(summary["total_records"]), "failed_sources": len(summary["failed_sources"]), "by_mode": {mode: values["sources"] for mode, values in summary["by_mode"].items()}}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
