"""采集统计报告：把"跑了一晚上到底攒了多少"说清楚。

读三处数据：
  data/rounds.jsonl   每轮采集的落账（batch_collect 写入）
  data/beacon.db      当前库存与增长分布
  scripts/data_health 质量面板（21 项指标 + 阈值）

用法:
    python scripts/collection_report.py                 # 打印到屏幕
    python scripts/collection_report.py -o out.md       # 同时写文件
    python scripts/collection_report.py --since 8       # 只看最近 8 轮
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ROUNDS_LOG = ROOT / "data" / "rounds.jsonl"
DB = ROOT / "data" / "beacon.db"


def load_rounds(since: int | None) -> list[dict]:
    if not ROUNDS_LOG.exists():
        return []
    rows = []
    for line in ROUNDS_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows[-since:] if since else rows


def db_snapshot() -> dict:
    if not DB.exists():
        return {}
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    out: dict = {}
    try:
        cur.execute("SELECT COUNT(*) FROM announcements")
        out["total"] = cur.fetchone()[0]
        cur.execute(
            "SELECT substr(created_at,1,13) h, COUNT(*) FROM announcements "
            "GROUP BY h ORDER BY h DESC LIMIT 24"
        )
        out["by_hour"] = [(r[0], r[1]) for r in cur.fetchall()]
        cur.execute(
            "SELECT source_id, COUNT(*) c FROM announcements GROUP BY source_id ORDER BY c DESC"
        )
        out["by_source"] = [(r[0], r[1]) for r in cur.fetchall()]
        cur.execute(
            "SELECT COUNT(*) FROM announcements WHERE region_province IS NOT NULL "
            "AND region_province <> ''"
        )
        out["with_region"] = cur.fetchone()[0]
        cur.execute("SELECT COUNT(DISTINCT region_province) FROM announcements")
        out["provinces"] = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM announcements WHERE amount IS NOT NULL AND amount <> ''")
        out["with_amount"] = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM attachments")
        out["attachments"] = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM source_sync_state")
        out["tracked_sources"] = cur.fetchone()[0]
    except sqlite3.Error as exc:
        out["error"] = str(exc)
    finally:
        con.close()
    return out


def health_panel() -> tuple[dict, dict]:
    import data_health

    return dict(data_health.measure()), dict(data_health.THRESHOLDS)


def render(rounds: list[dict], snap: dict, metrics: dict, thresholds: dict) -> str:
    lines: list[str] = []
    add = lines.append

    add("# 采集统计报告")
    add("")
    add(f"生成时间：{datetime.now(timezone.utc).isoformat()}")
    add("")

    add("## 一、采集轮次")
    add("")
    if not rounds:
        add("尚无轮次记录（data/rounds.jsonl 为空）。")
    else:
        add(f"已完成 {len(rounds)} 轮。")
        add("")
        add("| 轮 | 时间(UTC) | 到期源 | 成功 | 失败 | 取回 | 新增 | 更新 | 未变 | 墙钟(分) | 条/分 |")
        add("|---|---|---|---|---|---|---|---|---|---|---|")
        for r in rounds:
            add(
                f"| {r.get('round')} | {str(r.get('at',''))[11:19]} | {r.get('todo',0)} "
                f"| {r.get('success',0)} | {r.get('failed',0)} | {r.get('records',0)} "
                f"| {r.get('inserted',0)} | {r.get('updated',0)} | {r.get('unchanged',0)} "
                f"| {r.get('wall_seconds',0)/60:.1f} | {r.get('records_per_minute',0)} |"
            )
        total_wall = sum(r.get("wall_seconds", 0) for r in rounds)
        total_new = sum(r.get("inserted", 0) for r in rounds)
        total_fetch = sum(r.get("records", 0) for r in rounds)
        total_ok = sum(r.get("success", 0) for r in rounds)
        total_fail = sum(r.get("failed", 0) for r in rounds)
        add("")
        add(f"- 总墙钟：{total_wall/3600:.2f} 小时")
        add(f"- 累计取回：{total_fetch} 条；真正新增入库：{total_new} 条；重复命中：{total_fetch - total_new} 条")
        add(f"- 源级成功率：{total_ok}/{total_ok + total_fail}")
        if total_wall:
            add(f"- 平均吞吐：{total_fetch / total_wall * 3600:.0f} 条/小时"
                f"（{total_fetch / total_wall * 60:.1f} 条/分钟）")
    add("")

    add("## 二、库存")
    add("")
    if not snap or snap.get("error"):
        add(f"读取失败：{snap.get('error', '数据库不存在')}")
    else:
        add(f"- 公告总数：**{snap.get('total', 0)}**")
        add(f"- 有地区归属：{snap.get('with_region', 0)}"
            f"（覆盖率 {snap.get('with_region',0)/max(1,snap.get('total',1)):.1%}，"
            f"覆盖 {snap.get('provinces', 0)} 个省级区划）")
        add(f"- 含金额字段：{snap.get('with_amount', 0)}")
        add(f"- 附件记录：{snap.get('attachments', 0)}")
        add(f"- 调度表内源：{snap.get('tracked_sources', 0)}")
        by_source = snap.get("by_source", [])
        if by_source:
            add("")
            add(f"贡献最多的 15 个源（共 {len(by_source)} 个源有数据）：")
            add("")
            add("| 源 | 记录数 | 占比 |")
            add("|---|---|---|")
            total = snap.get("total", 1)
            for sid, count in by_source[:15]:
                add(f"| {sid} | {count} | {count/total:.1%} |")
    add("")

    add("## 三、最近 24 小时入库分布")
    add("")
    by_hour = snap.get("by_hour", []) if snap else []
    if not by_hour:
        add("无数据。")
    else:
        peak = max(c for _h, c in by_hour) or 1
        add("```")
        for hour, count in sorted(by_hour, reverse=True):
            bar = "#" * max(1, round(count / peak * 40))
            add(f"{hour}  {count:>5}  {bar}")
        add("```")
    add("")

    add("## 四、质量面板")
    add("")
    add("| 指标 | 当前 | 阈值 | 判定 |")
    add("|---|---|---|---|")
    misses = []
    for metric, (limit, direction) in sorted(thresholds.items()):
        if metric not in metrics:
            continue
        value = metrics[metric]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        ok = value >= limit if direction == "ge" else value <= limit
        bound = f"{'>=' if direction == 'ge' else '<='} {limit}"
        mark = "达标" if ok else "**未达标**"
        if not ok:
            misses.append((metric, value, bound))
        shown = f"{value:.4g}" if isinstance(value, float) else str(value)
        add(f"| {metric} | {shown} | {bound} | {mark} |")
    add("")
    if misses:
        add(f"未达标 {len(misses)} 项：")
        for metric, value, bound in misses:
            add(f"- `{metric}` = {value}（要求 {bound}）")
    else:
        add("全部达标。")
    add("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", type=int, default=None, help="only the last N rounds")
    parser.add_argument("-o", "--output", default=None, help="also write the report here")
    args = parser.parse_args()

    rounds = load_rounds(args.since)
    snap = db_snapshot()
    try:
        metrics, thresholds = health_panel()
    except Exception as exc:  # noqa: BLE001
        metrics, thresholds = {}, {}
        print(f"质量面板读取失败：{exc}", file=sys.stderr)

    report = render(rounds, snap, metrics, thresholds)
    print(report)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
        print(f"\n已写入 {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
