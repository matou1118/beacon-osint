from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(timezone.utc)


def _read(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    return data if isinstance(data, dict) else default


def _atomic_write(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(dict(data), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def metrics_from_result(result: Mapping[str, Any]) -> dict[str, Any]:
    sources = list(result.get("sources") or [])
    collected = sum(int(item.get("records") or 0) for item in sources if isinstance(item, Mapping))
    attempted = int(result.get("extraction_total") or collected)
    successful = int(result.get("extraction_success") or 0)
    failures = sum(1 for item in sources if isinstance(item, Mapping) and item.get("errors"))
    return {
        "collected": collected,
        "extraction_total": attempted,
        "extraction_success": successful,
        "matches": len(list(result.get("matches") or [])),
        "failures": failures,
        "sources": len(sources),
        "source_stats": [
            {"source_id": str(item.get("source_id") or ""), "records": int(item.get("records") or 0), "errors": item.get("errors") or []}
            for item in sources
            if isinstance(item, Mapping)
        ],
    }


def update_quality(
    quality_dir: str | Path,
    metrics: Mapping[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    current = _now(now)
    directory = Path(quality_dir)
    date_key = current.date().isoformat()
    path = directory / f"{date_key}.json"
    data = _read(path, {"date": date_key, "runs": 0, "collected": 0, "extraction_total": 0, "extraction_success": 0, "matches": 0, "failures": 0, "sources": 0, "source_stats": {}})
    for key in ("collected", "extraction_total", "extraction_success", "matches", "failures", "sources"):
        data[key] = int(data.get(key) or 0) + int(metrics.get(key) or 0)
    data["runs"] = int(data.get("runs") or 0) + 1
    data["extraction_success_rate"] = round(data["extraction_success"] / data["extraction_total"], 4) if data["extraction_total"] else 0.0
    data["updated_at"] = current.isoformat()
    source_stats = data.setdefault("source_stats", {})
    for source in metrics.get("source_stats", []) if isinstance(metrics.get("source_stats"), list) else []:
        if not isinstance(source, Mapping):
            continue
        source_id = str(source.get("source_id") or "")
        if not source_id:
            continue
        item = source_stats.setdefault(source_id, {"runs": 0, "collected": 0, "failures": 0})
        item["runs"] += 1
        item["collected"] += int(source.get("records") or 0)
        item["failures"] += int(bool(source.get("errors")))
    _atomic_write(path, data)
    _atomic_write(directory / "latest.json", data)
    render_dashboard(data, directory / "dashboard.html")
    return data


def render_dashboard(data: Mapping[str, Any], output_path: str | Path) -> None:
    rows = []
    for source_id, item in sorted(dict(data.get("source_stats") or {}).items()):
        rows.append(f"<tr><td>{html.escape(source_id)}</td><td>{int(item.get('runs') or 0)}</td><td>{int(item.get('collected') or 0)}</td><td>{int(item.get('failures') or 0)}</td></tr>")
    body = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>数据质量看板</title>
<style>body{{font-family:system-ui,sans-serif;margin:2rem;color:#222}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ddd;padding:.5rem;text-align:left}}th{{background:#f3f4f6}}.cards{{display:flex;gap:1rem;flex-wrap:wrap}}.card{{border:1px solid #ddd;padding:1rem;min-width:9rem}}</style></head>
<body><h1>数据质量看板</h1><p>统计日期：{html.escape(str(data.get('date') or ''))}</p>
<div class="cards"><div class="card">采集量<br><strong>{int(data.get('collected') or 0)}</strong></div><div class="card">抽取成功率<br><strong>{float(data.get('extraction_success_rate') or 0):.2%}</strong></div><div class="card">匹配量<br><strong>{int(data.get('matches') or 0)}</strong></div><div class="card">失败源次数<br><strong>{int(data.get('failures') or 0)}</strong></div></div>
<h2>按来源</h2><table><thead><tr><th>来源</th><th>运行次数</th><th>采集量</th><th>失败次数</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="4">暂无数据</td></tr>'}</tbody></table>
<p>更新时间：{html.escape(str(data.get('updated_at') or ''))}</p></body></html>"""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
