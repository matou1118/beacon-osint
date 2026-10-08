"""Collection operations view — why a source is or is not being collected.

Everything here answers one operator question: "the numbers look thin, what is the
collector actually doing?" It reads the registry, the scheduler progress snapshot
and the health log instead of recomputing anything, so the page cannot disagree
with the collector.

Reads only. The one write is an explicit "run one collection" button, which shells
out to the same entry point the scheduled task uses.
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config" / "sources.yaml"
PROGRESS = ROOT / "data" / "runtime" / "collect_progress.json"
IMPROVE_LOG = ROOT / "data" / "self_improve_log.jsonl"
ROUNDS_LOG = ROOT / "data" / "rounds.jsonl"
SOURCES_MEDIA = ROOT / "config" / "media_sources.yaml"

# 真正在跑的采集器是 lighthouse，它不写上面那两个文件 —— 那套是旧
# scripts/batch_collect.py 的产物，collect_progress.json 最后一次写入停在
# 2026-09-27，页面却照读不误，于是一直显示一份两天前的"status: running"。
# 进度和逐轮台账都得从 lighthouse 自己的产物里读。
LIGHTHOUSE_DIR = ROOT / "data" / "runtime" / "lighthouse"
LIGHTHOUSE_STATUS = LIGHTHOUSE_DIR / "status.json"
LIGHTHOUSE_LOG = LIGHTHOUSE_DIR / "overnight.log"

# [  5/109] 采到    0 丢弃   0 | 用时   4.2m 剩 25.8m 后停 | 在跑: ah_fgw,ah_gxt
LIVE_PROGRESS_RE = re.compile(
    r"^\[\s*(?P<done>\d+)/(?P<total>\d+)\]\s*"
    r"采到\s*(?P<got>\d+)\s*丢弃\s*(?P<dropped>\d+)\s*\|"
    r"\s*用时\s*(?P<elapsed>[\d.]+)m\s*剩\s*(?P<left>[\d.]+)m\s*后停\s*\|"
    r"\s*在跑:\s*(?P<running>.*)$"
)

STATUS_LABEL = {
    "enabled": "启用", "pending_review": "待审核", "pending_reprobe": "待重探",
    "blocked": "受阻", "disabled": "已停用",
}
ROBOT_RE = re.compile(r"robots", 2)


def load_registry() -> dict[str, list[dict[str, Any]]]:
    import yaml

    out: dict[str, list[dict[str, Any]]] = {}
    for name, path in (("政府源", CONFIG), ("媒体源", SOURCES_MEDIA)):
        if path.exists():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            out[name] = data.get("sources") or []
    return out


def summarize_registry(groups: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Counts per status plus the two things that explain a thin store: robots
    refusals and per-province skew."""
    summary: dict[str, Any] = {"by_status": {}, "robots_blocked": 0, "by_province": {},
                               "enabled_by_province": {}, "total": 0, "level": {}}
    for _name, sources in groups.items():
        for source in sources:
            summary["total"] += 1
            status = str(source.get("status") or "unknown")
            summary["by_status"][status] = summary["by_status"].get(status, 0) + 1
            notes = str(source.get("notes") or "")
            if status == "blocked" and ROBOT_RE.search(notes):
                summary["robots_blocked"] += 1
            province = str(source.get("region_province") or source.get("province") or "").strip()
            if province:
                summary["by_province"][province] = summary["by_province"].get(province, 0) + 1
                if source.get("status") == "enabled" and source.get("enabled"):
                    summary["enabled_by_province"][province] = \
                        summary["enabled_by_province"].get(province, 0) + 1
            level = str(source.get("gov_level") or source.get("level") or "未标注")
            summary["level"][level] = summary["level"].get(level, 0) + 1
    summary["by_status_labeled"] = {
        STATUS_LABEL.get(k, k): v for k, v in summary["by_status"].items()
    }
    return summary


def read_progress() -> dict[str, Any] | None:
    if not PROGRESS.exists():
        return None
    try:
        data = json.loads(PROGRESS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict):
        return data
    return None


def read_lighthouse_status() -> dict[str, Any] | None:
    """lighthouse 调度器的状态快照（轮次 / 深度 / 库内条数 / 逐轮台账）。"""
    if not LIGHTHOUSE_STATUS.exists():
        return None
    try:
        data = json.loads(LIGHTHOUSE_STATUS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read_live_progress(max_age_seconds: float = 180.0) -> dict[str, Any] | None:
    """本轮实时进度：源进度 / 采到 / 丢弃 / 已用时 / 剩余 / 在跑的源。

    日志每 tick 一行，只认最后一条，并且要求日志文件本身是新鲜的
    （max_age_seconds）—— 否则停了几天的日志会把最后一帧当成"正在进行"，
    那正是旧快照「status: running 停更两天」的老毛病。
    """
    if not LIGHTHOUSE_LOG.exists():
        return None
    try:
        age = datetime.now().timestamp() - LIGHTHOUSE_LOG.stat().st_mtime
    except OSError:
        return None
    if age > max_age_seconds:
        return {"stale": True, "age_seconds": round(age, 1)}

    try:
        with LIGHTHOUSE_LOG.open("r", encoding="utf-8", errors="replace") as handle:
            tail = handle.readlines()[-400:]
    except OSError:
        return None

    for line in reversed(tail):
        matched = LIVE_PROGRESS_RE.match(line.strip())
        if not matched:
            continue
        got = matched.group("got")
        dropped = matched.group("dropped")
        running = [s.strip() for s in matched.group("running").split(",") if s.strip()]
        return {
            "stale": False,
            "done": int(matched.group("done")),
            "total": int(matched.group("total")),
            "records": int(got),
            "dropped": int(dropped),
            "elapsed_minutes": float(matched.group("elapsed")),
            "left_minutes": float(matched.group("left")),
            "running": running,
            "log_age_seconds": round(age, 1),
        }
    return None


def announcement_count(db_path: str = "data/beacon.db") -> int:
    """库内公告条数，直接查库。

    不读 status.json 的 db_now：那份快照只在一轮**结束**时才写（write_status
    都在轮次之间），一轮 30 分钟里它一直是开轮时的旧值，面板上的"库内总数"
    会比真实值少整整一轮的量。条数是一次 COUNT，代价可忽略。
    """
    path = ROOT / db_path if not Path(db_path).is_absolute() else Path(db_path)
    if not path.exists():
        return 0
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return int(con.execute("SELECT COUNT(*) FROM announcements").fetchone()[0])
        finally:
            con.close()
    except sqlite3.Error:
        return 0


def status_age_seconds() -> float | None:
    """status.json 距上次写入多久。None = 文件不存在。"""
    if not LIGHTHOUSE_STATUS.exists():
        return None
    try:
        return round(datetime.now().timestamp() - LIGHTHOUSE_STATUS.stat().st_mtime, 1)
    except OSError:
        return None


def read_rounds(limit: int | None = None) -> list[dict[str, Any]]:
    """Per-round collection ledger written by scripts/batch_collect.py.

    One JSON line per completed round: how many sources were due, how many
    succeeded, how many records came back, and how many were actually new. The
    inserted/unchanged split is what separates "the collector is working" from
    "the sources are exhausted" -- repeating a pass over sources that have
    yielded everything re-fetches the same rows and inserts nothing.
    """
    if not ROUNDS_LOG.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in ROUNDS_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows[-limit:] if limit else rows


def rounds_summary(rounds: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Aggregate the round ledger into the numbers the workbench shows."""
    rows = read_rounds() if rounds is None else rounds
    if not rows:
        return {"rounds": 0}
    wall = sum(float(r.get("wall_seconds") or 0) for r in rows)
    fetched = sum(int(r.get("records") or 0) for r in rows)
    inserted = sum(int(r.get("inserted") or 0) for r in rows)
    dry = sum(1 for r in rows if int(r.get("inserted") or 0) == 0)
    return {
        "rounds": len(rows),
        "wall_seconds": wall,
        "fetched": fetched,
        "inserted": inserted,
        "duplicates": fetched - inserted,
        "dry_rounds": dry,
        "sources_ok": sum(int(r.get("success") or 0) for r in rows),
        "sources_failed": sum(int(r.get("failed") or 0) for r in rows),
        "records_per_hour": round(fetched / wall * 3600, 1) if wall else 0.0,
        # A run whose recent rounds insert nothing is scraping exhausted sources.
        "exhausted": dry >= 3 and dry == len(rows[-3:]),
        "first_at": rows[0].get("at"),
        "last_at": rows[-1].get("at"),
    }


def read_improve_log(limit: int = 12) -> list[dict[str, Any]]:
    if not IMPROVE_LOG.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in IMPROVE_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows[-limit:][::-1]


DRILLABLE_METRICS = (
    "under_200_chars_rate", "chrome_residue_rate", "summary_rate", "publish_date_rate",
    "region_coverage_rate", "section_page_rate", "date_rate", "amount_rate",
    "median_content_chars",
)


def metric_offenders(metric: str, db_path: str = "data/beacon.db", limit: int = 50) -> dict[str, Any]:
    """§P0-2：把一个体检指标还原成触发它的记录，而不是只给一个比率。

    判定式与 data_health 的阈值同源：直接复用它的正则，避免页面口径和体检口径
    漂移。指标是比率时列出「拉低比率的记录」，是字数时列出最短的一批。
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import data_health  # type: ignore
    except ImportError:
        return {"count": 0, "rows": [], "error": "体检模块不可用"}

    def no_summary(row: sqlite3.Row) -> bool:
        if data_health.SECTION_RE.match(str(row["title"] or "").strip()):
            return False
        try:
            extra = json.loads(row["extra"] or "{}") or {}
        except (TypeError, ValueError):
            extra = {}
        return not extra.get("summary")

    predicates = {
        "under_200_chars_rate": lambda r: len(str(r["content"] or "")) < 200,
        "chrome_residue_rate": lambda r: bool(data_health.CHROME_RE.search(str(r["content"] or ""))),
        "amount_rate": lambda r: not data_health.AMOUNT_RE.search(str(r["content"] or "")),
        "date_rate": lambda r: not data_health.DATE_RE.search(str(r["content"] or "")),
        "summary_rate": no_summary,
        "publish_date_rate": lambda r: not r["publish_date"],
        "region_coverage_rate": lambda r: not r["region_province"],
        "section_page_rate": lambda r: bool(data_health.SECTION_RE.match(str(r["title"] or "").strip())),
    }
    predicate = predicates.get(metric)
    if predicate is None and metric != "median_content_chars":
        return {"count": 0, "rows": [], "error": ""}

    path = ROOT / db_path if not Path(db_path).is_absolute() else Path(db_path)
    if not path.exists():
        return {"count": 0, "rows": [], "error": "数据库不存在"}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT id, source_id, title, content, publish_date, region_province, extra "
            "FROM announcements"
        ).fetchall()
    except sqlite3.Error as exc:
        return {"count": 0, "rows": [], "error": str(exc)}
    finally:
        con.close()

    if metric == "median_content_chars":
        hits = sorted(rows, key=lambda r: len(str(r["content"] or "")))
    else:
        hits = [r for r in rows if predicate(r)]
    return {
        "count": len(hits),
        "error": "",
        "rows": [
            {
                "id": r["id"],
                "title": str(r["title"] or "（无标题）"),
                "source_id": str(r["source_id"] or ""),
                "length": len(str(r["content"] or "")),
            }
            for r in hits[:limit]
        ],
    }


def read_health() -> dict[str, Any] | None:
    """Import the health module rather than re-implementing the thresholds."""
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import data_health  # type: ignore
    except ImportError:
        return None
    try:
        # measure() scans the whole corpus; calling it twice (once for metrics,
        # once for findings) doubled the cost of every workbench rerender.
        metrics = data_health.measure()
        return {"metrics": metrics, "findings": data_health.check(metrics)}
    except Exception as exc:  # noqa: BLE001 - the page must not die on a bad DB
        return {"error": f"{type(exc).__name__}: {exc}"}


def region_breakdown(db_path: str = "data/beacon.db", limit: int = 40) -> dict[str, Any]:
    """Per-province record counts, for the coverage picture behind the scalar.

    region_coverage_rate says how much of the corpus is attributed; this says
    where it actually is, so a thin map is distinguishable from a broad but
    shallow one.
    """
    path = ROOT / db_path if not Path(db_path).is_absolute() else Path(db_path)
    if not path.exists():
        return {"error": "数据库不存在"}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM announcements")
        total = cur.fetchone()[0]
        cur.execute(
            "SELECT COALESCE(NULLIF(TRIM(region_province), ''), '（未标注）') AS p, COUNT(*) AS c "
            "FROM announcements GROUP BY p ORDER BY c DESC"
        )
        rows = [(r["p"], r["c"]) for r in cur.fetchall()]
    except sqlite3.Error as exc:
        return {"error": str(exc)}
    finally:
        con.close()
    attributed = sum(c for p, c in rows if p != "（未标注）")
    return {
        "total": total,
        "attributed": attributed,
        "coverage": round(attributed / total, 4) if total else 0.0,
        "provinces": len([1 for p, _ in rows if p != "（未标注）"]),
        "rows": rows[:limit],
        "unattributed": total - attributed,
    }


def run_collection(limit: int = 5, timeout: int = 900) -> str:
    """Run one short collection pass, the same way the scheduled task does."""
    try:
        proc = subprocess.run(
            [sys.executable, "-B", "scheduler.py", "--once", "--limit", str(limit),
             "--timeout", str(timeout)],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout + 120,
        )
    except subprocess.TimeoutExpired:
        return f"采集超时（>{timeout}s），可能仍在后台运行"
    tail = (proc.stdout or proc.stderr or "").strip().splitlines()[-6:]
    return f"退出码 {proc.returncode}\n" + "\n".join(tail)


SAR_MARKERS = ("特别行政区", "香港", "澳门")


def provinces_without_enabled(groups: dict[str, list[dict[str, Any]]]) -> list[str]:
    """Provinces that have registered sources but none enabled.

    SARs are excluded on purpose: the registry disables them by requirement, so
    they are a decision, not a coverage gap.
    """
    summary = summarize_registry(groups)
    gaps = []
    for province in summary["by_province"]:
        if any(marker in province for marker in SAR_MARKERS):
            continue
        if not summary["enabled_by_province"].get(province):
            gaps.append(province)
    return sorted(gaps)


def skew_warning(summary: dict[str, Any]) -> str | None:
    """Flag the source-distribution skew that caps region coverage."""
    enabled = summary.get("enabled_by_province") or {}
    if not enabled:
        return None
    total = sum(enabled.values())
    top, count = max(enabled.items(), key=lambda kv: kv[1])
    if count == total:
        return (f"启用源全部集中在 {top}（{count}/{total}），分省覆盖为 0，"
                "提升新源时必须先补其他省份。")
    if count >= 2 and count / total > 0.5:
        return (f"启用源集中在 {top}（{count}/{total}），"
                "分省覆盖会被它拉低，提升新源时建议按省限额。")
    return None
