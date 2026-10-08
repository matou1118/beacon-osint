"""按 sources.yaml 的登记值回填 announcements 的地区字段。

为什么需要
  lighthouse/run.py 装配记录时漏了 region_province/region_city/region_county，
  而 db.py 的 upsert 映射是对的 —— 于是「不传」直接等于「写 NULL」。
  后果实测（2026-09-29）：库里 6403 条只有 1991 条有地区，而 id>3434 的近 3000 条
  全空；首页地区分布、中国地图都读不到新数据。根因已在 run.py 补上，本脚本只负责
  修历史数据。

  这一类和 scripts/reclassify.py 是同一个模式的问题：旁路链路自己入库，
  不经过主链路的字段补全。

判据
  - 只按 source_id 关联 sources.yaml 的人工登记值，不从正文/标题猜地区。
    源注册表是 AGENTS.md §2 的白名单，它是权威；正文抽取是启发式。
  - 只在字段为空时写，已有值不覆盖（不破坏已确认的数据）
  - 来源不在注册表、或注册表里也没登记地区的，一律跳过并报数，不硬填
  - 预览模式默认，不加 --apply 不写盘

用法
  python scripts/refill_region.py            # 预览
  python scripts/refill_region.py --apply    # 落盘
"""
from __future__ import annotations

import argparse
import collections
import sqlite3
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[1]

FIELDS = ("region_province", "region_city", "region_county")


def registry() -> dict[str, dict[str, str | None]]:
    data = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8")) or {}
    out: dict[str, dict[str, str | None]] = {}
    for item in data.get("sources") or []:
        sid = str(item.get("id") or "")
        if not sid:
            continue
        out[sid] = {f: (str(item.get(f)).strip() or None if item.get(f) else None)
                    for f in FIELDS}
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(ROOT / "data" / "beacon.db"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--show", type=int, default=12)
    args = parser.parse_args()

    reg = registry()
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT id, source_id, title, region_province, region_city, region_county "
        "FROM announcements"
    ).fetchall()
    con.close()
    total = len(rows)
    print(f"总量 {total} 条，注册表 {len(reg)} 个源\n")

    plan: list[tuple[str | None, str | None, str | None, int]] = []
    skipped_no_reg: collections.Counter[str] = collections.Counter()
    skipped_no_region: collections.Counter[str] = collections.Counter()
    already: collections.Counter[str] = collections.Counter()

    for rid, source_id, _title, prov, city, county in rows:
        entry = reg.get(str(source_id or ""))
        if entry is None:
            skipped_no_reg[str(source_id)] += 1
            continue
        if not any(entry.values()):
            skipped_no_region[str(source_id)] += 1
            continue
        # 已有值的字段不覆盖，只补空的那几个
        want = [entry["region_province"] if not (prov or "").strip() else None,
                entry["region_city"] if not (city or "").strip() else None,
                entry["region_county"] if not (county or "").strip() else None]
        if not any(want):
            already[str(entry["region_province"])] += 1
            continue
        plan.append((want[0], want[1], want[2], rid))

    fillable = sum(1 for p in plan if p[0])
    print(f"可回填 {len(plan)} 条（其中省级 {fillable} 条）")
    print(f"跳过：来源不在注册表 {sum(skipped_no_reg.values())} 条"
          f"（{len(skipped_no_reg)} 个源），注册表里没登记地区 {sum(skipped_no_region.values())} 条"
          f"（{len(skipped_no_region)} 个源）")
    print(f"已有地区不用动 {sum(already.values())} 条\n")

    after = collections.Counter(p[0] for p in plan if p[0])
    print("=== 回填后的省级分布（前 20）===")
    for prov, n in after.most_common(20):
        print(f"   {prov:<12} {n:>5}")
    if not fillable:
        print("   （没有可回填的省级值 —— 注册表里这些源都没登记 region_province）")

    if not args.apply:
        print("\n（预览模式，未写入。加 --apply 落盘）")
        return 0

    write = sqlite3.connect(args.db)
    try:
        write.executemany(
            "UPDATE announcements SET "
            "region_province = COALESCE(?, region_province), "
            "region_city     = COALESCE(?, region_city), "
            "region_county   = COALESCE(?, region_county) "
            "WHERE id = ?", plan)
        write.commit()
    finally:
        write.close()
    print(f"写回 {len(plan)} 条")

    check = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    now = check.execute(
        "SELECT COUNT(*) FROM announcements "
        "WHERE region_province IS NOT NULL AND TRIM(region_province) != ''").fetchone()[0]
    check.close()
    print(f"有地区记录 {now}/{total} = {now / total:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
