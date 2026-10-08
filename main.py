from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from collectors.source_router import SourceRouter
from extractors.gliner_extractor import Gliner2Extractor
from extractors.regex_fallback import RegexFallbackExtractor
from extractors.attachment_downloader import process_attachments
from extractors.attachment_downloader import process_attachments
from graph_service import rebuild_graph
from output.heat_report import write_heat_report
from output.markdown import write_markdown_records
from output.pusher import EmailPusher
from rules.engine import match_announcements
from rules.industry_focus import apply_industry_focus
from rules.tracker import apply_focus_tracking
from lighthouse.postprocess import analyze
from collectors.lock import collect_lock
from storage.db import Database, compute_content_hash


def load_config(path: str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("config/sources.yaml must contain a mapping")
    sources = data.get("sources", [])
    if not isinstance(sources, list):
        raise ValueError("config/sources.yaml must contain a sources list")
    return data


def load_rules(path: str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("config/rules.yaml must contain a mapping")
    return data


def load_notify(path: str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("config/notify.yaml must contain a mapping")
    return data.get("email", {})


def load_industry_focus(path: str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("config/industry_focus.yaml must contain a mapping")
    return data


def enrich_records(records: list[dict[str, Any]], sources: dict[str, dict[str, Any]], use_gliner: bool = False) -> list[dict[str, Any]]:
    fallback = RegexFallbackExtractor()
    model = Gliner2Extractor() if use_gliner else None
    enriched = []
    for record in records:
        source = sources.get(str(record.get("source_id")), {})
        extracted: dict[str, Any]
        if model is not None:
            try:
                extracted = model.extract(str(record.get("content") or ""), source=record)
            except Exception:
                extracted = fallback.extract(str(record.get("content") or ""), source=record)
        else:
            extracted = fallback.extract(str(record.get("content") or ""), source=record)
        merged = dict(record)
        protected_fields = {"title", "publish_date", "gov_level", "category_l1", "category_l2", "issuing_body", "region_province", "region_city", "region_county", "admission_sub_category", "institution_name", "institution_type", "admission_type", "enrollment_count", "tuition_info", "employment_guarantee"}
        for key, value in extracted.items():
            if key == "needs_review":
                continue
            if key in protected_fields and merged.get(key) not in (None, "", []):
                continue
            if value not in (None, "", []):
                merged[key] = value
        for key in ("issuing_body", "gov_level", "jurisdiction_code", "region_province", "region_city", "region_county", "level", "project_role", "source_type", "category_l1", "category_l2", "target_group", "target_industry", "institution_name", "institution_type", "admission_type", "admission_sub_category"):
            if source.get(key) is not None and merged.get(key) in (None, "", []):
                merged[key] = source[key]
        if source.get("name") is not None and not merged.get("issuing_body"):
            merged["issuing_body"] = source["name"]
        required_present = bool(merged.get("title")) and bool(merged.get("issuing_body")) and bool(merged.get("gov_level"))
        extracted_review = bool(extracted.get("needs_review")) and not required_present
        merged["needs_review"] = int(bool(merged.get("needs_review")) or extracted_review)
        if not merged.get("category_l1"):
            merged["category_l1"] = "其他"
        if not merged.get("content_hash"):
            merged["content_hash"] = compute_content_hash(merged)
        enriched.append(merged)
    return enriched


def build_user(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "region": args.user_region,
        "levels": args.user_level or [],
        "categories": args.user_category or [],
        "target_group": args.target_group,
        "target_industry": args.target_industry,
        "keywords": args.keyword or [],
        "exclude_review": True,
    }


def load_crawl4ai_schemas(path: str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("config/crawl4ai_schemas.yaml must contain a mapping")
    return data.get("schemas", {})


def resolve_sources(config: dict[str, Any], crawl4ai_schemas: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    cadence_tiers = config.get("cadence_tiers", {})
    retention_policies = config.get("retention_policies", {})
    default_request_interval = float(config.get("default_request_interval_seconds", 3))
    resolved = []
    for raw_source in config.get("sources", []):
        source = dict(raw_source)
        cadence_tier = str(source.get("cadence_tier") or "daily")
        tier = cadence_tiers.get(cadence_tier)
        if not isinstance(tier, dict) or not tier.get("interval_seconds"):
            raise ValueError(f"unknown cadence tier: {cadence_tier}")
        source["cadence_tier"] = cadence_tier
        source["cadence_seconds"] = int(source.get("cadence_seconds") or tier["interval_seconds"])
        retention_class = str(source.get("retention_class") or "active")
        policy = retention_policies.get(retention_class)
        if not isinstance(policy, dict):
            raise ValueError(f"unknown retention class: {retention_class}")
        source["retention_class"] = retention_class
        if "retention_days" not in source:
            source["retention_days"] = policy.get("days")
        source["request_interval_seconds"] = float(source.get("request_interval_seconds", default_request_interval))
        source.setdefault("forge_rss", config.get("forge_rss", {"enabled": False}))
        source["crawl4ai"] = dict(source.get("crawl4ai") or config.get("crawl4ai") or {"enabled": False})
        source["downloads"] = dict(source.get("downloads") or config.get("downloads") or {})
        schema_name = source.get("css_schema_name")
        if schema_name and crawl4ai_schemas and schema_name in crawl4ai_schemas:
            source["crawl4ai"]["css_schema"] = crawl4ai_schemas[schema_name]
        source.setdefault("render_mode", config.get("collector_engine", {}).get("default_render_mode", "static"))
        source.setdefault("minimum_content_chars", int(config.get("crawl4ai", {}).get("minimum_content_chars", 80)))
        resolved.append(source)
    return resolved


def load_sources(path: str, crawl4ai_schemas: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return resolve_sources(load_config(path), crawl4ai_schemas)


def collector_for(source: dict[str, Any]) -> Any:
    return SourceRouter().collector_for(source)


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect approved national ministry announcements")
    parser.add_argument("--config", default="config/sources.yaml")
    parser.add_argument("--crawl4ai-schemas", default="config/crawl4ai_schemas.yaml")
    parser.add_argument("--rules", default="config/rules.yaml")
    parser.add_argument("--industry-config", default="config/industry_focus.yaml")
    parser.add_argument("--notify-config", default="config/notify.yaml")
    parser.add_argument("--db", default="data/beacon.db")
    parser.add_argument("--source", action="append", dest="source_ids")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--include-pending", action="store_true")
    parser.add_argument("--force", action="store_true", help="ignore the source next-due time")
    parser.add_argument("--use-gliner", action="store_true")
    parser.add_argument("--user-region")
    parser.add_argument("--user-level", action="append")
    parser.add_argument("--user-category", action="append")
    parser.add_argument("--target-group")
    parser.add_argument("--target-industry")
    parser.add_argument("--keyword", action="append")
    parser.add_argument("--send-email", action="store_true")
    parser.add_argument("--notify-frequency", choices=["daily", "weekly"], default="daily")
    parser.add_argument("--output")
    parser.add_argument("--markdown-output")
    parser.add_argument("--heatmap-output")
    args = parser.parse_args()

    # 采集互斥。公告批量采集（batch_collect.py）、政府工作报告、统计公报、
    # 以及两个计划任务直接调用的 scheduler.py，都会写同一个 SQLite。
    # 锁放在这里是因为 scheduler.py 是以子进程方式调 main.py 的，
    # 加在调度器层拦不住。
    # BEACON_COLLECT_LOCK 可以改锁路径，是为了测试。通宵采集常驻时采集锁会被真实
    # 持有，集成测试若也用生产路径就必然失败——tests/test_real_pipeline_local.py 和
    # tests/test_real_pipeline_modes.py 之前就是这么红的。让测试指向自己的临时路径，
    # 它们才能在采集运行时照常验证真实行为。
    _collect_lock_dir = os.environ.get("BEACON_COLLECT_LOCK") or None

    with collect_lock(f"main.py {args.source_ids or '全部源'}",
                      lock_dir=_collect_lock_dir) as locked:
        if not locked:
            return 0
        return _run(args)


def _run(args) -> int:
    sources = load_sources(args.config, load_crawl4ai_schemas(args.crawl4ai_schemas))
    rules = load_rules(args.rules)
    industry_config = load_industry_focus(args.industry_config)
    if args.source_ids:
        wanted = set(args.source_ids)
        sources = [source for source in sources if source.get("id") in wanted]
    if not args.include_pending:
        sources = [source for source in sources if source.get("status") == "enabled" and source.get("enabled", True)]

    source_map = {str(source.get("id")): source for source in sources}
    user = build_user(args)
    has_filters = any((user.get("region"), user.get("levels"), user.get("categories"), user.get("target_group"), user.get("target_industry"), user.get("keywords")))
    records: list[dict[str, Any]] = []
    source_results: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    database = Database(args.db)
    purge_result = {"deleted": 0}
    storage_result = {"inserted": 0, "updated": 0, "unchanged": 0}
    matches: list[dict[str, Any]] = []
    graph_result: dict[str, Any] = {"announcements": 0, "nodes": 0, "edges": 0, "evidence": 0}
    corroboration_result: dict[str, int] = {"total": 0, "corroborated": 0, "single_source": 0, "disabled": 0}
    email_sent = 0
    email_errors: list[dict[str, str]] = []
    markdown_result: dict[str, Any] | None = None
    media_clusters: list[dict[str, Any]] = []
    heat_result: dict[str, Any] = {"total": 0, "official_topics": 0, "top_score": 0.0}
    try:
        purge_result = database.purge_expired(now)
        for source in sources:
            source_id = str(source.get("id") or "")
            due = args.force or database.source_due(source_id, now)
            if not due:
                source_results.append({"source_id": source_id, "skipped": True, "reason": "not_due"})
                continue
            collector = None
            try:
                collector = collector_for(source)
                raw_records = collector.collect(limit=max(0, args.limit))
                enriched_records = [apply_industry_focus(record, industry_config) for record in enrich_records(raw_records, source_map, use_gliner=args.use_gliner)]
                source_records = [apply_focus_tracking(record, rules) for record in enriched_records]
                records.extend(source_records)
                errors = list(getattr(collector, "errors", []) or [])
                source_results.append(
                    {
                        "source_id": source_id,
                        "skipped": False,
                        "records": len(source_records),
                        "focus_records": sum(1 for record in source_records if record.get("focus_tracked")),
                        "errors": errors,
                    }
                )
                error_text = json.dumps(errors, ensure_ascii=False) if errors else None
                database.mark_source_result(
                    source_id=source_id,
                    cadence_tier=str(source["cadence_tier"]),
                    interval_seconds=int(source["cadence_seconds"]),
                    success=not errors,
                    error=error_text,
                    now=now,
                )
            except Exception as exc:
                error = [{"url": source.get("url", ""), "error": str(exc)}]
                source_results.append({"source_id": source_id, "skipped": False, "records": 0, "errors": error})
                database.mark_source_result(
                    source_id=source_id,
                    cadence_tier=str(source["cadence_tier"]),
                    interval_seconds=int(source["cadence_seconds"]),
                    success=False,
                    error=str(exc),
                    now=now,
                )
            finally:
                if collector is not None:
                    collector.close()
        # 交叉验证 + 聚簇。与 lighthouse 链路共用 lighthouse.postprocess.analyze，
        # 避免两处各写一遍、判据漂移（曾经就是这个原因：lighthouse 那份漏了整段）。
        analyzed = analyze(records, rules, now=now)
        records = analyzed["records"]
        corroboration_result = analyzed["corroboration"]
        media_clusters = analyzed["clusters"]
        heat_result = analyzed["heat"]
        attachment_result = process_attachments(records)
        storage_result = database.upsert_announcements(records)
        extraction_result = database.save_field_extractions(records)
        attachment_db_result = database.save_attachments(records)
        database.upsert_heat_topics(media_clusters, now.date().isoformat())
        try:
            graph_result = rebuild_graph(database)
        except Exception as exc:
            graph_result = {"error": str(exc)}
        if args.markdown_output:
            markdown_result = write_markdown_records(records, args.markdown_output)
        if args.heatmap_output:
            write_heat_report(media_clusters, args.heatmap_output, now.date().isoformat())
        if has_filters:
            matches = match_announcements(records, user, rules)
        if args.send_email and matches:
            notify_config = load_notify(args.notify_config)
            pusher = EmailPusher(database, frequency=args.notify_frequency, config=notify_config)
            limit = int(notify_config.get("max_records_per_run", 20))
            for match in matches[:limit]:
                try:
                    if pusher.send(match, now=now):
                        email_sent += 1
                except Exception as exc:
                    email_errors.append({"source_id": str(match.get("source_id") or ""), "error": str(exc)})
    finally:
        database.close()

    extraction_total = len(records)
    extraction_success = sum(1 for record in records if record.get("content") and record.get("content_hash") and not record.get("needs_review"))
    result = {
        "sources": source_results,
        "records": records,
        "matches": matches,
        "extraction_total": extraction_total,
        "extraction_success": extraction_success,
        "storage": storage_result,
        "field_extractions": extraction_result,
        "attachments": attachment_result,
        "attachment_db": attachment_db_result,
        "graph": graph_result,
        "corroboration": corroboration_result,
        "media_clusters": media_clusters,
        "heat_topics": heat_result,
        "markdown": markdown_result,
        "purge": purge_result,
        "focus_tracked": sum(1 for record in records if record.get("focus_tracked")),
        "industry_focused": sum(1 for record in records if record.get("industry_focus")),
        "email_sent": email_sent,
        "email_errors": email_errors,
    }
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
