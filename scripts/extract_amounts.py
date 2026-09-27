"""把金额抽进 amounts 表，供时间轴跨年对比。

为什么不用 announcements.amount 那个单值列
  一份文件可以有多条金额，而且是同标签不同值。实测 id=86 那份环评公示：
    「本项目动态投资总计1923610万元」
    「本工程总投资390622万元」
    「本项目动态总投资约441128万元」
  三个都是"投资总额"标签、不同值——单值列只能留一个，field_extractions 的
  UNIQUE(announcement_id, topic_id, field_key) 同样装不下。金额是时间轴对比
  的核心数据，少一条就少一个可比的点，所以给它自己的表。

用户给的方法论落在三处
  1. 有表格用表格：表格里的金额按**列位置**配对（|采购预算|最高限价| 对
     |495000元|495000元|），置信度 88，比散文的 74 高。
  2. 没表格交叉印证：同一标签同值但表格和散文都报了 → +10；同标签多个接近值
     → 降一点并标注待复核；同标签多个量级差 3 倍以上 → 判为不同项目，不罚。
  3. 金额要准：裸数字（无标签）不采信，只列出来等人看。错误金额会污染时间轴，
     比没有金额更糟。

同时回填 announcements.amount 单值列，取置信度最高的一条，让摘要头部
「金额：…」和既有查询继续可用。

谨慎项
  - amounts 有 UNIQUE(announcement_id, label, value_yuan)，重复跑幂等
  - 只读解析，不发请求、不碰 robots
  - --apply 才落盘；落盘前备份
  - 正文里没写年份的，回退 publish_date 的年

用法
  python scripts/extract_amounts.py           # 预览
  python scripts/extract_amounts.py --apply   # 落盘
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[1]

from extractors.amounts import extract_amounts  # noqa: E402

DB = ROOT / "data" / "beacon.db"
BACKUP_DIR = ROOT / "data" / "backups"


def backup(tag: str) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    dest = BACKUP_DIR / f"beacon-{stamp}-{tag}.db"
    shutil.copy2(DB, dest)
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description="抽取金额到 amounts 表")
    ap.add_argument("--apply", action="store_true", help="落盘（默认只预览）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 条（调试用）")
    args = ap.parse_args()

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    cols = {r[1] for r in con.execute("PRAGMA table_info(announcements)")}
    have_publish = "publish_date" in cols
    sql = "SELECT id, title, content" + (", publish_date" if have_publish else "")
    sql += " FROM announcements ORDER BY id"
    if args.limit:
        sql += f" LIMIT {args.limit}"
    rows = con.execute(sql).fetchall()

    plan: list[tuple] = []
    best: dict[int, tuple[int, str, str]] = {}
    cand_docs = 0
    for row in rows:
        year = None
        if have_publish and row["publish_date"]:
            head = str(row["publish_date"])[:4]
            year = int(head) if head.isdigit() else None
        hits, cands = extract_amounts(row["content"] or "", row["title"] or "", year=year)
        if not hits and cands:
            cand_docs += 1
        for hit in hits:
            plan.append((row["id"], hit.label, hit.value_yuan, hit.unit,
                         hit.raw, hit.year, hit.confidence, hit.source, hit.evidence))
            # 单值列取置信度最高的一条；同分取金额最大的（通常是"总"的那个）
            cur = best.get(row["id"])
            key = (hit.confidence, hit.value_yuan)
            if cur is None or key > (cur[0], _to_yuan(cur[1])):
                best[row["id"]] = (hit.confidence, hit.raw or "", hit.unit or "")

    docs = len({p[0] for p in plan})
    print(f"待写 amounts {len(plan)} 行，来自 {docs} / {len(rows)} 条公告"
          f"（{docs/len(rows):.0%}）")
    print(f"待回填 announcements.amount {len(best)} 条")
    if cand_docs:
        print(f"有裸数字未采信、需人工看的 {cand_docs} 条")

    by_label: dict[str, int] = {}
    by_conf: dict[int, int] = {}
    for p in plan:
        by_label[p[1]] = by_label.get(p[1], 0) + 1
        by_conf[p[6]] = by_conf.get(p[6], 0) + 1
    print("标签分布 " + str(sorted(by_label.items(), key=lambda kv: -kv[1])[:10]))
    print("置信度分布 " + str(sorted(by_conf.items(), reverse=True)))

    if not args.apply:
        print("\n预览模式，未落盘。加 --apply 写入。")
        sample = sorted(plan, key=lambda p: -p[6])[:5]
        for p in sample:
            print(f"  [{p[6]}|{p[7]}] {p[0]} {p[1]:<7} {p[4]:<20} {p[2]:>16,.2f} 元")
        return 0

    dest = backup("pre-amounts")
    print(f"\n已备份 {dest}")

    con.execute("CREATE TABLE IF NOT EXISTS amounts ("
                "id INTEGER PRIMARY KEY, "
                "announcement_id INTEGER NOT NULL, "
                "label VARCHAR(32) NOT NULL, "
                "value_yuan REAL NOT NULL, "
                "unit VARCHAR(8), "
                "raw VARCHAR(96), "
                "year INTEGER, "
                "confidence INTEGER NOT NULL, "
                "source VARCHAR(8) NOT NULL, "
                "evidence TEXT, "
                "created_at DATETIME NOT NULL, "
                "UNIQUE(announcement_id, label, value_yuan))")
    con.execute("CREATE INDEX IF NOT EXISTS ix_amount_year_label "
                "ON amounts(year, label, value_yuan)")
    con.executemany(
        "INSERT OR IGNORE INTO amounts (announcement_id, label, value_yuan, unit,"
        " raw, year, confidence, source, evidence, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7], p[8],
          datetime.now().isoformat(sep=" ")) for p in plan])

    # 只在 amount 为空时回填，不覆盖已有值
    if "amount" in cols:
        filled = 0
        for aid, (conf, raw, unit) in best.items():
            cur = con.execute("SELECT amount FROM announcements WHERE id=?", (aid,)).fetchone()
            if cur and not (cur["amount"] or "").strip():
                con.execute("UPDATE announcements SET amount=? WHERE id=?", (raw, aid))
                filled += 1
        print(f"回填 announcements.amount {filled} 条（只填空值）")

    con.commit()
    total = con.execute("SELECT COUNT(*) FROM amounts").fetchone()[0]
    print(f"amounts 表现有 {total} 行")
    con.close()
    return 0


def _to_yuan(raw: str) -> float:
    import re
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(万元|亿元|万元整|元|万|亿)", raw or "")
    if not m:
        return 0.0
    base = m.group(2).rstrip("整")
    return float(m.group(1).replace(",", "")) * {
        "元": 1, "万元": 10_000, "万": 10_000, "亿元": 100_000_000, "亿": 100_000_000,
    }.get(base, 1)


if __name__ == "__main__":
    raise SystemExit(main())
