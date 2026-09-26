"""Self-improvement loop for the scrape -> clean -> summarise rules.

Why this exists: the rules were being tuned by hand and every fix broke something
else. Cleaning once cut an 807-char article to 37; over-aggressive compression
dropped the amount and the deadline, the two facts a comparison needs. So a fix
is now only kept when the whole metric panel improves, and the attempt history is
written to data/self_improve_log.jsonl.

Loop: measure -> pick the worst failing metric that still has an unused fix ->
apply on a copy -> rebuild -> re-measure -> keep or revert. No model, no guessing:
every decision is a before/after number.

Usage:
    python scripts/self_improve.py --status          # show the queue
    python scripts/self_improve.py --iterate         # run one improvement step
    python scripts/self_improve.py --iterate --max 3 # run several
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

REGISTRY = ROOT / "config" / "defect_registry.yaml"
LOG = ROOT / "data" / "self_improve_log.jsonl"
BACKUP = ROOT / "data" / "self_improve_backup"


@dataclass
class Step:
    defect_id: str
    metric: str
    fix_id: str
    description: str
    applied: bool = False
    kept: bool = False
    before: float = 0.0
    after: float = 0.0
    notes: list[str] = field(default_factory=list)


def load_registry() -> dict[str, Any]:
    with REGISTRY.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_history() -> list[dict[str, Any]]:
    if not LOG.exists():
        return []
    out = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def used_fix_ids() -> set[str]:
    """Fixes already attempted, so the loop moves to the next idea.

    A reverted attempt still counts as used: the measurement said it did not
    help, and repeating it would loop forever.
    """
    return {
        str(row.get("fix") or row.get("fix_id"))
        for row in load_history()
        if row.get("fix") or row.get("fix_id")
    }


def measure() -> dict[str, Any]:
    """Full metric panel. Imported fresh so constant edits take effect."""
    for name in [m for m in list(sys.modules) if m.startswith(("extractors", "scripts.data_health"))]:
        del sys.modules[name]
    import data_health

    metrics = data_health.measure()
    return metrics


def failing(metrics: dict[str, Any], registry: dict[str, Any]) -> list[dict[str, Any]]:
    """Defects that are out of bounds and still have an unused fix."""
    used = used_fix_ids()
    out = []
    for defect in registry.get("defects") or []:
        metric = defect.get("metric")
        if metric not in metrics:
            continue
        limit, direction = _bound(registry, metric)
        value = float(metrics[metric])
        ok = value >= limit if direction == "ge" else value <= limit
        if ok:
            continue
        severity = str(defect.get("severity") or "medium")
        available = [f for f in defect.get("fixes") or [] if str(f.get("id")) not in used]
        out.append({
            "id": defect.get("id"),
            "metric": metric,
            "value": value,
            "bound": f"{direction} {limit}",
            "severity": {"high": 0, "medium": 1, "low": 2}.get(severity, 1),
            "gap": (limit - value) if direction == "ge" else (value - limit),
            "description": defect.get("description"),
            "available_fixes": available,
        })
    out.sort(key=lambda d: (d["severity"], -d["gap"]))
    return out


def _bound(registry: dict[str, Any], metric: str) -> tuple[float, str]:
    """Threshold and direction for a metric, from the health thresholds.

    Read from data_health rather than the registry so the two never disagree;
    the registry describes fixes, data_health owns the bar.
    """
    from data_health import THRESHOLDS

    return THRESHOLDS.get(metric, (0.0, "ge"))


def apply_fix(fix: dict[str, Any]) -> tuple[bool, str]:
    """Apply one fix in place. Returns (applied, note)."""
    spec = fix.get("apply") or {}
    kind = str(spec.get("kind") or "none")
    if kind == "none":
        return False, str(spec.get("already_implemented") and "已在代码中实施" or "无可自动应用的修改")

    BACKUP.mkdir(parents=True, exist_ok=True)
    targets: list[Path] = []
    if kind in ("set_constant", "append_line_rule"):
        targets = [ROOT / str(spec["target"])]
    elif kind == "scale_config":
        targets = [ROOT / str(spec["config"])]
    if not any(t.exists() for t in targets):
        return False, "目标文件不存在"
    for target in targets:
        shutil.copy2(target, BACKUP / target.name)

    try:
        if kind == "set_constant":
            target = targets[0]
            text = target.read_text(encoding="utf-8")
            pattern = rf"^{re.escape(str(spec['name']))}\s*=\s*.+$"
            if not re.search(pattern, text, flags=re.M):
                return False, f"常量 {spec['name']} 未找到"
            text = re.sub(pattern, f"{spec['name']} = {spec['value']}", text, flags=re.M)
            target.write_text(text, encoding="utf-8")
            return True, f"{spec['name']} = {spec['value']}"
        if kind == "append_line_rule":
            target = targets[0]
            text = target.read_text(encoding="utf-8")
            rule = str(spec["rule"])
            if rule in text:
                return False, "规则已存在"
            marker = r"^(\s*r\"\^\(\?:网站地图"
            if not re.search(r"CHROME_PATTERNS\s*=\s*\(", text):
                return False, "未找到 CHROME_PATTERNS"
            text = re.sub(r"(\n\s*r\"\^扫描二维码)", rf'\n    {rule},\\1', text, count=1)
            target.write_text(text, encoding="utf-8")
            return True, f"新增行规则 {rule[:40]}"
        if kind == "scale_config":
            target = targets[0]
            config = yaml.safe_load(target.read_text(encoding="utf-8"))
            key, factor, cap = str(spec["key"]), int(spec.get("factor", 2)), int(spec.get("cap", 10**6))
            changed = 0
            for source in config.get("sources") or []:
                if not (source.get("status") == "enabled" and source.get("enabled")):
                    continue
                current = source.get(key)
                if not isinstance(current, int):
                    continue
                source[key] = min(cap, max(1, current * factor))
                changed += 1
            target.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False, width=10**6), encoding="utf-8")
            return True, f"{key} ×{factor}（上限 {cap}），影响 {changed} 个源"
    except Exception as exc:  # noqa: BLE001
        return False, f"应用失败: {type(exc).__name__}: {exc}"
    return False, f"未支持的修复类型 {kind}"


def revert() -> None:
    for backup in BACKUP.glob("*"):
        target = ROOT / "collectors" / backup.name
        if not target.exists():
            target = ROOT / "extractors" / backup.name
        if not target.exists():
            target = ROOT / "config" / backup.name
        if target.exists():
            shutil.copy2(backup, target)


def record(step: Step) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "at": datetime.now(timezone.utc).isoformat(),
            "defect": step.defect_id,
            "metric": step.metric,
            "fix": step.fix_id,
            "applied": step.applied,
            "kept": step.kept,
            "before": step.before,
            "after": step.after,
            "notes": step.notes,
        }, ensure_ascii=False) + "\n")


# ── discover_fix：从失败样本中发现新修复候选 ──────────────────
# 自动发现的修复必须先过人工审核，否则会污染进化历史。
# 审核通过后手工加入 defect_registry.yaml，不会自动写入。


# ── discover_fix：从失败样本中发现新修复候选 ──────────────────
# 自动发现的修复必须先过人工审核，否则会污染进化历史。
# 审核通过后手工加入 defect_registry.yaml，不会自动写入。


def collect_failing_samples(defect_id: str, metric: str, limit: int = 20) -> list[dict[str, Any]]:
    """从数据库中取出该缺陷的失败样本，用于根因分析。"""
    import sqlite3
    db = ROOT / "data" / "beacon.db"
    if not db.exists():
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    samples: list[dict[str, Any]] = []
    try:
        rows = con.execute(
            "SELECT id, title, content, extra FROM announcements ORDER BY id DESC LIMIT ?",
            (limit * 5,),
        ).fetchall()
        for row in rows:
            try:
                extra = json.loads(row["extra"] or "{}") or {}
            except (TypeError, ValueError):
                extra = {}
            content = str(row["content"] or "")
            # 按缺陷类型筛选失败样本
            if defect_id == "chrome_residue" and not _has_chrome_residue(content):
                continue
            if defect_id == "dropped_amount" and not _has_dropped_amount(content, extra):
                continue
            if defect_id == "short_bodies" and len(content) >= 200:
                continue
            samples.append({
                "id": row["id"],
                "title": str(row["title"] or ""),
                "content": content[:2000],
                "summary": extra.get("summary") or "",
            })
            if len(samples) >= limit:
                break
    finally:
        con.close()
    return samples


def _has_chrome_residue(content: str) -> bool:
    return bool(re.search(r"打印此页|网站地图|ICP备|当前位置|扫描二维码", content))


def _has_dropped_amount(content: str, extra: dict) -> bool:
    has_amount_in_content = bool(re.search(r"\d+(?:\.\d+)?\s*(?:万元|亿元|万|亿|元)", content))
    summary = extra.get("summary") or ""
    has_amount_in_summary = bool(re.search(r"\d+(?:\.\d+)?\s*(?:万元|亿元|万|亿|元)", summary))
    return has_amount_in_content and not has_amount_in_summary


def discover_fix(defect_id: str, metric: str, failing_samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """在失败样本上做根因分析，生成新修复候选。

    返回的候选修复带 require_human_approval=True，不会自动应用。
    需要人工审核后手工加入 defect_registry.yaml。
    """
    if not failing_samples:
        return []

    candidates: list[dict[str, Any]] = []

    if defect_id == "chrome_residue":
        # 从失败样本中提取共性残留模式
        patterns = _extract_chrome_patterns(failing_samples)
        for pattern in patterns:
            candidates.append({
                "id": f"auto_chrome_{hash(pattern) & 0xFFFFFF:06x}",
                "description": f"自动发现残留模式：{pattern}",
                "require_human_approval": True,
                "apply": {
                    "kind": "append_line_rule",
                    "target": "extractors/clean_summarize.py",
                    "rule": f'r"^(?:{re.escape(pattern)})"',
                },
            })

    elif defect_id == "dropped_amount":
        # 从失败样本中提取金额句模式
        amount_patterns = _extract_amount_patterns(failing_samples)
        for pattern in amount_patterns:
            candidates.append({
                "id": f"auto_amount_{hash(pattern) & 0xFFFFFF:06x}",
                "description": f"自动发现金额模式：{pattern}",
                "require_human_approval": True,
                "apply": {
                    "kind": "none",
                    "suggestion": f"在 AMOUNT_RE 中加入模式：{pattern}",
                },
            })

    elif defect_id == "short_bodies":
        # 短正文的根因：清洗切太多
        candidates.append({
            "id": "auto_short_body_analysis",
            "description": f"分析 {len(failing_samples)} 个短正文样本，可能需要放宽 TAIL_ZONE",
            "require_human_approval": True,
            "apply": {
                "kind": "none",
                "suggestion": "检查 clean_summarize.py 的 TAIL_ZONE 和 SWITCHER_PATTERNS",
            },
        })

    return candidates


def _extract_chrome_patterns(samples: list[dict[str, Any]]) -> list[str]:
    """从失败样本中提取共性的 chrome 残留模式。"""
    pattern_counts: dict[str, int] = {}
    for sample in samples:
        content = sample.get("content") or ""
        # 取正文前 200 字和后 200 字（残留通常在头尾）
        head = content[:200]
        tail = content[-200:] if len(content) > 200 else ""
        for text in [head, tail]:
            # 提取 2-6 字的连续中文片段
            for match in re.finditer(r"[\u4e00-\u9fff]{2,6}", text):
                fragment = match.group()
                if fragment in ("当前位置", "网站地图", "打印此页", "主办单位"):
                    continue  # 已有规则
                pattern_counts[fragment] = pattern_counts.get(fragment, 0) + 1
    # 出现 ≥3 次的才算共性
    return [p for p, c in sorted(pattern_counts.items(), key=lambda x: -x[1]) if c >= 3][:5]


def _extract_amount_patterns(samples: list[dict[str, Any]]) -> list[str]:
    """从失败样本中提取金额相关模式。"""
    patterns: list[str] = []
    for sample in samples:
        content = sample.get("content") or ""
        # 找"数字+单位"但摘要里没有的
        for match in re.finditer(r"([\u4e00-\u9fff]{0,4}\d+(?:\.\d+)?\s*(?:万元|亿元|万|亿|元)[\u4e00-\u9fff]{0,4})", content):
            patterns.append(match.group(1))
    # 去重，取前 5
    seen: set[str] = set()
    unique: list[str] = []
    for p in patterns:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique[:5]


def iterate(metrics: dict[str, Any], registry: dict[str, Any]) -> Step | None:
    queue = failing(metrics, registry)
    for item in queue:
        if not item["available_fixes"]:
            continue
        fix = item["available_fixes"][0]
        step = Step(
            defect_id=str(item["id"]),
            metric=str(item["metric"]),
            fix_id=str(fix.get("id")),
            description=str(fix.get("description") or ""),
            before=float(item["value"]),
        )
        applied, note = apply_fix(fix)
        step.applied = applied
        step.notes.append(note)
        if not applied:
            # Nothing was changed, so before/after are the same measurement by
            # construction. Record it as skipped so the log is not misread as a
            # failed attempt.
            step.notes.append("未修改任何文件（需人工或已在代码中实施），不计入尝试")
            record(step)
            continue
        after_metrics = measure()
        step.after = float(after_metrics.get(step.metric, step.before))
        tolerance = float((registry.get("defaults") or {}).get("regression_tolerance", 0.02))
        regressions = []
        for key, value in after_metrics.items():
            if key == step.metric:
                # The metric being optimised is judged below, not counted as a
                # regression against itself; that self-veto made every successful
                # fix look like a failure.
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            before_value = metrics.get(key)
            if not isinstance(before_value, (int, float)):
                continue
            if value < before_value - tolerance and key not in ("total",):
                regressions.append(f"{key} {before_value}→{value}")
        direction = item.get("bound", "ge").split()[0]
        improved = step.after < step.before if direction == "le" else step.after > step.before
        if improved and not regressions:
            step.kept = True
            step.notes.append("指标改善且无回归，保留")
        else:
            step.notes.append("无改善或出现回归，撤销")
            step.notes.extend(regressions[:3])
            # 记录失败样本，让进化可解释
            try:
                samples = collect_failing_samples(step.defect_id, step.metric, limit=5)
                if samples:
                    step.notes.append(f"失败样本 {len(samples)} 条: {[s['id'] for s in samples]}")
            except Exception:
                pass
            revert()
        record(step)
        return step
    return None


def iterate_with_discover(metrics: dict[str, Any], registry: dict[str, Any]) -> Step | None:
    """先尝试预定义修复，用尽后触发 discover_fix 发现新修复候选。"""
    step = iterate(metrics, registry)
    if step is not None:
        return step

    # 预定义修复用尽，尝试 discover_fix
    queue = failing(metrics, registry)
    for item in queue:
        defect_id = str(item["id"])
        metric = str(item["metric"])
        try:
            samples = collect_failing_samples(defect_id, metric, limit=20)
            candidates = discover_fix(defect_id, metric, samples)
        except Exception:
            candidates = []
        if candidates:
            step = Step(
                defect_id=defect_id,
                metric=metric,
                fix_id="discover_fix",
                description=f"发现 {len(candidates)} 个新修复候选（需人工审核）",
                before=float(item["value"]),
            )
            step.notes.append(f"候选修复: {[c['id'] for c in candidates]}")
            for c in candidates:
                step.notes.append(f"  - {c['id']}: {c['description']}")
            step.notes.append("以上候选需人工审核后手工加入 defect_registry.yaml")
            record(step)
            return step
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--iterate", action="store_true")
    parser.add_argument("--discover", action="store_true", help="用尽预定义修复后，自动发现新修复候选")
    parser.add_argument("--max", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    registry = load_registry()
    metrics = measure()
    queue = failing(metrics, registry)

    if args.status or not args.iterate:
        history = load_history()
        if args.json:
            print(json.dumps({"metrics": metrics, "queue": queue, "history": history[-10:]}, ensure_ascii=False, indent=2))
            return 0
        print("", f"自我修正队列（{len(queue)} 项待处理，已尝试 {len(history)} 次）", "=" * 66)
        if not queue:
            print("  全部指标达标")
        for item in queue:
            label = {0: "high", 1: "medium", 2: "low"}.get(item["severity"], "medium")
            print(f"  [{label:<6s}] {item['id']:<26s} {item['metric']}={item['value']} (要求 {item['bound']})")
            print(f"           待用修复: {', '.join(str(f.get('id')) for f in item['available_fixes']) or '无'}")
        return 0

    for _ in range(max(1, args.max)):
        if args.discover:
            step = iterate_with_discover(metrics, registry)
        else:
            step = iterate(metrics, registry)
        if step is None:
            print("没有可用修复，全部指标达标或修复已用尽")
            break
        metrics = measure()
        mark = "保留" if step.kept else "撤销"
        print(f"  [{mark}] {step.defect_id} / {step.fix_id}: {step.before} -> {step.after}")
        for note in step.notes:
            print(f"        {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
