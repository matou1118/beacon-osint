"""跨记录重复正文检测：抓「标题对、正文是列表页导航」这类脏数据。

为什么单条判据抓不到它
----------------------
2026-10-03 实测：某轮入库 1721 条，其中 350 条（20%）的**标题是真实公告、
日期也正常**，但正文全是同一段列表页导航：

    标题: 山东省劳动人事争议调解专家库首批入选专家名单公示   ← 对
    日期: 2023-07-26                                          ← 对
    正文: 索引 服务 日一二三四五六 最新发布 …                 ← 错

根因是详情页抓取失败后退回了列表页：链接找对了、正文取错了。
它在逐源结果里显示为 ``ok``（链接拿到了、日期抽到了），所以

* 单条内容统计（句读密度、短行占比、chrome 占比）全都被短正文带偏
* 标题形态判据抓不到（标题是真的）
* 缺日期判据抓不到（日期是对的）

唯一稳定的信号是**关系**：同一个源下多条文案的正文一模一样。
这不受单条长度影响，也不会被比例阈值带偏。

用法
----
    python scripts/find_duplicate_bodies.py --since "2026-10-03 06:00"
    python scripts/find_duplicate_bodies.py --apply     # 打 needs_review

``--apply`` 只**打标记**不删数据：标记可逆（清掉标记就恢复），
而删除不可逆。
"""
from __future__ import annotations

import argparse
import collections
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "beacon.db"

# 正文比对用前N 字符：列表页导航模板在开头几百字内就完全一致，
# 而正文尾部的「附件下载/版权所有」之类差异只会制造噪声。
HEAD = 200
MIN_REPEAT = 3


def find(since: str | None, scope: str | None = None,
         min_repeat: int = MIN_REPEAT) -> list[tuple[str, str, int, str]]:
    """返回 [(source_id, 正文模板, 重复次数, 模板预览)]，按次数降序。"""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    where, params = [], []
    if since:
        where.append("created_at >= ?")
        params.append(since)
    if scope:
        where.append("source_id = ?")
        params.append(scope)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    try:
        rows = con.execute(
            f"SELECT id, source_id, content FROM announcements {clause}",
            params).fetchall()
    finally:
        con.close()

    heads = collections.Counter((c or "")[:HEAD] for _, _, c in rows)
    out = []
    for body, n in heads.items():
        if n >= min_repeat and body.strip():
            sid = next(s for _, s, c in rows if (c or "")[:HEAD] == body)
            out.append((sid, body, n, body[:90].replace("\n", " ")))
    out.sort(key=lambda r: -r[2])
    return out


def ids_for(dupes, since: str | None) -> list[int]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    where, params = [], []
    if since:
        where.append("created_at >= ?")
        params.append(since)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    try:
        rows = con.execute(
            f"SELECT id, source_id, content FROM announcements {clause}",
            params).fetchall()
    finally:
        con.close()
    heads = collections.Counter((c or "")[:HEAD] for _, _, c in rows)
    dup_heads = {b for _, b, n, _ in dupes if n >= MIN_REPEAT}
    return [i for i, _, c in rows if (c or "")[:HEAD] in dup_heads]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="跨记录重复正文检测")
    ap.add_argument("--since", help="只看 created_at >= 这个时间")
    ap.add_argument("--source", help="只看某个源")
    ap.add_argument("--min-repeat", type=int, default=MIN_REPEAT)
    ap.add_argument("--apply", action="store_true",
                    help="给命中的记录打 needs_review（不删数据）")
    args = ap.parse_args(argv)

    dupes = find(args.since, args.source, args.min_repeat)
    if not dupes:
        print("没有重复正文模板")
        return 0
    total = sum(n for _, _, n, _ in dupes)
    print("=" * 68)
    print("重复正文模板 %d 个，涉及 %d 条记录" % (len(dupes), total))
    print("=" * 68)
    for sid, body, n, preview in dupes[:10]:
        print("  %4d 条  %-16s %s" % (n, sid[:16], preview))
    if len(dupes) > 10:
        print("  ... 另有 %d 个模板" % (len(dupes) - 10))

    if args.apply:
        ids = ids_for(dupes, args.since)
        con = sqlite3.connect(str(DB))
        try:
            con.executemany("UPDATE announcements SET needs_review = 1 WHERE id = ?",
                            [(i,) for i in ids])
            con.commit()
        finally:
            con.close()
        print()
        print("已给 %d 条打 needs_review（可逆：清掉标记即恢复）" % len(ids))
    else:
        print()
        print("加 --apply 打 needs_review 标记（不删数据）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())