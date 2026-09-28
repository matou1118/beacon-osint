from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

from backup import backup_database
from cleanup import cleanup_runtime_artifacts, scrub_text
from main import load_crawl4ai_schemas, load_sources
from quality import metrics_from_result, render_dashboard, update_quality

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
except ImportError:
    BackgroundScheduler = None
    CronTrigger = None
    IntervalTrigger = None


ROOT = Path(__file__).resolve().parent


def load_scheduler_config(path: str = "config/scheduler.yaml") -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("config/scheduler.yaml must contain a mapping")
    return data


def _resolve(path: str | Path) -> Path:
    value = Path(path)
    return value if value.is_absolute() else ROOT / value


def _parse_result(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    if not text:
        raise RuntimeError("collector produced no JSON result")
    try:
        value = json.loads(text)
    except ValueError:
        start = text.rfind("{")
        if start < 0:
            raise RuntimeError("collector result is not JSON")
        value = json.loads(text[start:])
    if not isinstance(value, dict):
        raise RuntimeError("collector result must be a JSON object")
    return value


def _source_errors(result: Mapping[str, Any], source_id: str) -> list[Any]:
    for item in result.get("sources") or []:
        if isinstance(item, Mapping) and str(item.get("source_id") or "") == source_id:
            return list(item.get("errors") or [])
    return []


def _atomic_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(dict(data), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def update_failure_report(report_path: Path, source_id: str, entry: Mapping[str, Any] | None) -> None:
    if report_path.exists():
        try:
            data = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    else:
        data = {}
    sources = data.setdefault("sources", {})
    if entry is None:
        sources.pop(source_id, None)
    else:
        sources[source_id] = dict(entry)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_json(report_path, data)


def run_source_once(
    source: Mapping[str, Any],
    config_path: str,
    schemas_path: str,
    db_path: str,
    limit: int,
    timeout_seconds: int,
    markdown_output: str | None = None,
    heatmap_output: str | None = None,
    project_root: Path = ROOT,
    progress: "ProgressReporter | None" = None,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(project_root / "main.py"),
        "--config",
        config_path,
        "--crawl4ai-schemas",
        schemas_path,
        "--db",
        db_path,
        "--source",
        str(source.get("id") or ""),
        "--limit",
        str(limit),
        "--force",
    ]
    if markdown_output:
        command.extend(["--markdown-output", markdown_output])
    if heatmap_output:
        command.extend(["--heatmap-output", heatmap_output])
    if progress is not None:
        process = subprocess.Popen(
            command, cwd=project_root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace",
        )
        while True:
            try:
                out, err = process.communicate(timeout=progress.heartbeat_seconds)
                break
            except subprocess.TimeoutExpired:
                progress.tick(heartbeat)
        completed = subprocess.CompletedProcess(command, process.returncode, out or "", err or "")
    else:
        completed = subprocess.run(command, cwd=project_root, capture_output=True, text=True, timeout=timeout_seconds, check=False)
    if completed.returncode != 0:
        raise RuntimeError(scrub_text(completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"))
    result = _parse_result(completed.stdout)
    errors = _source_errors(result, str(source.get("id") or ""))
    if errors:
        raise RuntimeError(scrub_text(json.dumps(errors, ensure_ascii=False)))
    return result


def run_source_with_retry(
    source: Mapping[str, Any],
    config: Mapping[str, Any],
    run_once: Callable[[Mapping[str, Any]], dict[str, Any]],
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    retries = max(0, int(config.get("retry_attempts", 3)))
    backoff = max(0, int(config.get("retry_backoff_seconds", 60)))
    last_error = ""
    for attempt in range(1, retries + 2):
        try:
            result = run_once(source)
            return {"success": True, "attempts": attempt, "result": result, "error": None}
        except Exception as exc:
            # 带上异常类型名。str(exc) 可能是空的（裸 Exception()、或者某些
            # 只设了 args[0] 为 None 的异常），于是 last_error 落库成
            # "unknown error"——库里 57 个连续失败的源全是这四个字，分不清是
            # 代理断了、412、robots 还是选择器失效。类型名是白捡的诊断信息。
            last_error = scrub_text(f"{type(exc).__name__}: {exc}")[:2000]
            if attempt <= retries:
                sleep(backoff * (2 ** (attempt - 1)))
    return {"success": False, "attempts": retries + 1, "result": None, "error": last_error or "unknown error"}


def _source_entry(source: Mapping[str, Any], outcome: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    return {
        "source_id": str(source.get("id") or ""),
        "source_url": str(source.get("url") or ""),
        "attempts": int(outcome.get("attempts") or 0),
        "last_error": outcome.get("error"),
        "failed_at": now.isoformat(),
    }


def create_scheduler(
    sources: list[Mapping[str, Any]],
    config: Mapping[str, Any],
    source_job: Callable[[Mapping[str, Any]], None],
    backup_job: Callable[[], None],
    quality_job: Callable[[], None],
    cleanup_job: Callable[[], None],
    digest_job: Callable[[], None] | None = None,
) -> Any:
    if BackgroundScheduler is None or CronTrigger is None or IntervalTrigger is None:
        raise RuntimeError("APScheduler is required; install requirements.txt")
    scheduler = BackgroundScheduler(
        timezone="UTC",
        job_defaults={
            "coalesce": bool(config.get("coalesce", True)),
            "max_instances": int(config.get("max_instances", 1)),
            "misfire_grace_time": int(config.get("misfire_grace_time", 3600)),
        },
    )
    for source in sources:
        scheduler.add_job(
            lambda current=source: source_job(current),
            trigger=IntervalTrigger(seconds=max(60, int(source.get("cadence_seconds") or 86400))),
            id=f"source:{source.get('id')}",
            replace_existing=True,
        )
    scheduler.add_job(backup_job, CronTrigger(hour=int(config.get("backup_hour", 2)), minute=int(config.get("backup_minute", 30))), id="backup", replace_existing=True)
    scheduler.add_job(quality_job, CronTrigger(hour=int(config.get("quality_hour", 23)), minute=int(config.get("quality_minute", 55))), id="quality", replace_existing=True)
    scheduler.add_job(cleanup_job, CronTrigger(hour=3, minute=0), id="cleanup", replace_existing=True)
    if digest_job is not None:
        scheduler.add_job(
            digest_job,
            CronTrigger(hour=int(config.get("digest_hour", 8)), minute=int(config.get("digest_minute", 0))),
            id="compare_digest",
            replace_existing=True,
        )
    return scheduler


class ProgressReporter:
    """Print per-source progress so a long run never looks hung.

    Writes one line per finished source plus a heartbeat while a source is in
    flight, and a machine-readable snapshot the UI or an operator can poll.
    """

    def __init__(self, total: int, stream: Any = None, snapshot: Path | None = None, heartbeat_seconds: int = 30) -> None:
        self.total = max(0, int(total))
        self.stream = stream if stream is not None else sys.stderr
        self.snapshot = snapshot
        self.heartbeat_seconds = max(5, int(heartbeat_seconds))
        self.done = 0
        self.succeeded = 0
        self.failed = 0
        self.records = 0
        self.current: str | None = None
        self.started = time.monotonic()
        self.heartbeat = 0

    def start(self, source_id: str, name: str = "") -> None:
        self.current = source_id
        self.heartbeat = 0
        label = f"{source_id}" + (f" {name}" if name else "")
        print(f"[{self._fraction()}] START  {label}", file=self.stream, flush=True)

    def tick(self, seconds: int = 60) -> None:
        """Emit a heartbeat so a slow source still shows life."""
        if not self.current:
            return
        if seconds > 0 and time.monotonic() - self.heartbeat < seconds:
            return
        self.heartbeat = time.monotonic()
        elapsed = int(time.monotonic() - self.started)
        print(
            f"[{self._fraction()}] ..    {self.current} 运行中 已用 {elapsed}s",
            file=self.stream, flush=True,
        )
        self._write_snapshot("running")

    def finish(self, source_id: str, success: bool, records: int = 0, error: str = "") -> None:
        self.done += 1
        self.current = None
        if success:
            self.succeeded += 1
        else:
            self.failed += 1
        self.records += int(records or 0)
        mark = "OK  " if success else "FAIL"
        tail = f" 记录={records}" if success else f" {str(error)[:80]}"
        print(
            f"[{self._fraction()}] {mark}  {source_id}{tail} "
            f"({self.succeeded}成功/{self.failed}失败 累计{self.records}条)",
            file=self.stream, flush=True,
        )
        self._write_snapshot("running")

    def summary(self) -> dict[str, Any]:
        payload = {
            "status": "done",
            "total": self.total,
            "done": self.done,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "records": self.records,
            "elapsed_seconds": int(time.monotonic() - self.started),
        }
        print("[100%] DONE  " + json.dumps(payload, ensure_ascii=False), file=self.stream, flush=True)
        self._write_snapshot("done", payload)
        return payload

    def _fraction(self) -> str:
        if not self.total:
            return "  ?%"
        return f"{min(100, self.done * 100 // self.total):>3d}%"

    def _write_snapshot(self, status: str, extra: dict[str, Any] | None = None) -> None:
        if not self.snapshot:
            return
        payload = {
            "status": status,
            "total": self.total,
            "done": self.done,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "records": self.records,
            "current": self.current,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if extra:
            payload.update(extra)
        try:
            self.snapshot.parent.mkdir(parents=True, exist_ok=True)
            self.snapshot.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass


def apply_level_switch(
    sources: list[dict[str, Any]], switch: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Narrow the run to the levels currently in focus.

    Sampling keeps the run bounded: a national sweep of every city and county in
    one pass would take weeks, and lower levels are not the focus yet. Their
    sources stay registered so turning the switch back on needs no work.
    """
    if not switch:
        return list(sources), {"kept": len(sources), "dropped": 0, "kept_by_level": {}}
    level_flags = {
        "国家部委": switch.get("national_enabled", True),
        "国家级": switch.get("national_enabled", True),
        "省级": switch.get("province_enabled", True),
        "直辖市": switch.get("province_enabled", True),
        "特别行政区": switch.get("sar_enabled", False),
        "市级": switch.get("city_enabled", True),
        "县级": switch.get("county_enabled", False),
        "区县级": switch.get("county_enabled", False),
    }
    samples = {
        "省级": int(switch.get("province_sample") or 0),
        "直辖市": int(switch.get("province_sample") or 0),
        "特别行政区": int(switch.get("province_sample") or 0),
        "市级": int(switch.get("city_sample") or 0),
        "县级": int(switch.get("county_sample") or 0),
    }
    kept: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    dropped: list[str] = []
    for source in sources:
        level = str(source.get("gov_level") or "")
        if not level_flags.get(level, True):
            dropped.append(str(source.get("id")))
            continue
        cap = samples.get(level, 0)
        if cap and seen.get(level, 0) >= cap:
            dropped.append(str(source.get("id")))
            continue
        seen[level] = seen.get(level, 0) + 1
        kept.append(source)
    return kept, {
        "kept": len(kept),
        "dropped": len(dropped),
        "kept_by_level": seen,
        "switch": {k: v for k, v in switch.items()},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="APScheduler source runner and maintenance jobs")
    parser.add_argument("--config", default="config/sources.yaml")
    parser.add_argument("--schemas", default="config/crawl4ai_schemas.yaml")
    parser.add_argument("--scheduler-config", default="config/scheduler.yaml")
    parser.add_argument("--db", default="data/beacon.db")
    parser.add_argument("--source", action="append", dest="source_ids")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--once", action="store_true", help="run each source once and exit")
    args = parser.parse_args()
    scheduler_config = load_scheduler_config(args.scheduler_config)
    sources = load_sources(args.config, load_crawl4ai_schemas(args.schemas))
    sources = [source for source in sources if source.get("status") == "enabled" and source.get("enabled", True)]
    level_switch = scheduler_config.get("level_switch") or {}
    if level_switch and not args.source_ids:
        sources, switch_report = apply_level_switch(sources, level_switch)
        if switch_report.get("dropped"):
            print(json.dumps({"level_switch": switch_report}, ensure_ascii=False))
    if args.source_ids:
        wanted = set(args.source_ids)
        sources = [source for source in sources if str(source.get("id")) in wanted]
    failure_dir = _resolve(str(scheduler_config.get("failure_report_dir") or "data/failure_reports"))
    quality_dir = _resolve(str(scheduler_config.get("quality_dir") or "data/quality"))
    markdown_dir = _resolve(str(scheduler_config.get("markdown_dir") or "data/markdown"))
    heatmap_output = _resolve(str(scheduler_config.get("heatmap_output") or "data/heat/latest.md"))
    backup_dir = _resolve(str(scheduler_config.get("backup_dir") or "data/backups"))
    runtime_dirs = [_resolve(str(path)) for path in scheduler_config.get("runtime_cleanup_dirs", [])]
    debug_logs = [_resolve(str(path)) for path in scheduler_config.get("debug_log_files", [])]

    def run_job(source: Mapping[str, Any]) -> None:
        report_path = failure_dir / f"{datetime.now(timezone.utc).date().isoformat()}.json"
        source_id = str(source.get("id") or "source")
        if progress:
            progress.start(source_id, str(source.get("name") or ""))
        outcome = run_source_with_retry(
            source,
            scheduler_config,
            lambda current: run_source_once(current, args.config, args.schemas, args.db, args.limit, args.timeout, str(markdown_dir / str(current.get("id") or "source")), str(heatmap_output), progress=progress),
        )
        if outcome["success"]:
            update_failure_report(report_path, source_id, None)
            result = outcome["result"]
            update_quality(quality_dir, metrics_from_result(result))
            if progress:
                progress.finish(source_id, True, int(result.get("record_count") or 0))
        else:
            update_failure_report(report_path, source_id, _source_entry(source, outcome, datetime.now(timezone.utc)))
            update_quality(quality_dir, {"collected": 0, "extraction_total": 0, "extraction_success": 0, "matches": 0, "failures": 1, "sources": 1, "source_stats": [{"source_id": source.get("id"), "records": 0, "errors": [outcome["error"]]}]})
            if progress:
                progress.finish(source_id, False, 0, str(outcome.get("error") or ""))
        if scheduler_config.get("cleanup_after_run", True):
            cleanup_runtime_artifacts(runtime_dirs, debug_logs, preserve_audit=bool(scheduler_config.get("preserve_audit", True)))

    def backup_job() -> None:
        backup_database(args.db, backup_dir, int(scheduler_config.get("backup_retention_days", 30)))

    def quality_job() -> None:
        latest = quality_dir / "latest.json"
        if latest.exists():
            render_dashboard(json.loads(latest.read_text(encoding="utf-8")), quality_dir / "dashboard.html")

    def cleanup_job() -> None:
        cleanup_runtime_artifacts(runtime_dirs, debug_logs, preserve_audit=bool(scheduler_config.get("preserve_audit", True)))

    def digest_job() -> None:
        from compare_digest import run_daily_digest

        try:
            run_daily_digest(args.db)
        except Exception as exc:  # 通知失败不拖垮采集调度
            scrubbed = scrub_text(str(exc))[:500]
            print(json.dumps({"digest_error": scrubbed}, ensure_ascii=True), file=sys.stderr)

    def digest_job() -> None:
        from compare_digest import run_daily_digest

        try:
            run_daily_digest(args.db)
        except Exception as exc:  # 通知失败不拖垮采集调度
            scrubbed = scrub_text(str(exc))[:500]
            print(json.dumps({"digest_error": scrubbed}, ensure_ascii=True), file=sys.stderr)

    if not scheduler_config.get("enabled", True) and not args.once:
        print(json.dumps({"enabled": False, "mode": "daemon"}, ensure_ascii=True))
        return 0
    progress = ProgressReporter(
        len(sources),
        snapshot=_resolve(str(scheduler_config.get("progress_snapshot") or "data/runtime/collect_progress.json")),
        heartbeat_seconds=int(scheduler_config.get("progress_heartbeat_seconds") or 30),
    )
    if args.once:
        for source in sources:
            run_job(source)
        summary = progress.summary()
        print(json.dumps({
            "sources": len(sources),
            "mode": "once",
            "succeeded": summary["succeeded"],
            "failed": summary["failed"],
            "records": summary["records"],
            "elapsed_seconds": summary["elapsed_seconds"],
        }, ensure_ascii=True))
        return 0
    scheduler = create_scheduler(sources, scheduler_config, run_job, backup_job, quality_job, cleanup_job, digest_job)
    scheduler.start()
    print(json.dumps({"sources": len(sources), "mode": "daemon"}, ensure_ascii=True))
    try:
        import threading

        threading.Event().wait()
    except KeyboardInterrupt:
        scheduler.shutdown(wait=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
