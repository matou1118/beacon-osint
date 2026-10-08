"""质量抽查：抽一批记录人眼看，产出「垃圾 / 真内容」两堆的判定结果。

为什么需要这个脚本
------------------
2026-10-03 同一批数据上，我用统计量（句读密度、短行占比、chrome 占比）
当判据调三轮阈值，误杀 23.2% / 10.4% / 13.7%。根因不是阈值没调好，
是**基准本身是脏的**：我拿「有 publish_date 的记录」当正常基准，而那批
"正常"里自己就混着目录页（目录页恰好抽到日期就混过了门禁）。

换成「先抽样、人眼读、再统计」之后，同一批数据的结论完全反过来：

    1721 条新增里 150 条缺 publish_date，看着像 10% 脏数据
    逐条读内容 → 149 条是完整正文的转载新闻稿
              →   1 条才是垃圾（某省商务厅「下载中心」页）

如果按「缺日期=垃圾」的假设去清，那 149 条真数据会被删掉。

本脚本做什么
------------
**不替你下结论。** 它只做三件事：

1. 随机抽 N 条，把标题 + 正文首段打印出来，让人（或后续轮次的我）读
2. 打印一批**结构性指纹**的命中数（表头空表格、标题等于机构名、
   正文无句读、markdown 表格有表头但无数据……）——这些比「缺日期」干净得多
3. 把已有的判定结论写进 ``data/runtime/lighthouse/sample_verdicts.json``，
   供 ``tests/test_source_throttle.py`` 引用为**人工确认过的基准**

为什么第 3 步要落盘
------------------
「样本是人看过的」这件事必须可复现、可断言，否则下一次又会退回
「拿统计量猜」。落盘之后，「基准是干净的」就成了一个可以被测试钉住的事实，
而不是一句口头承诺。

用法
----
    python scripts/sample_audit.py --limit 14
    python scripts/sample_audit.py --limit 40 --since "2026-10-03 06:00"
    python scripts/sample_audit.py --since "2026-10-03 06:00" --stats-only
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERDICTS = ROOT / "data" / "runtime" / "lighthouse" / "sample_verdicts.json"
DB = ROOT / "data" / "beacon.db"

# ── 结构性指纹 ──────────────────────────────────────────────────
#
# 每条都是「页面结构」层面的证据，不是内容统计。
# 区别在于：统计量在短正文上会失真（比例阈值对 180 字正文毫无意义），
# 而结构指纹（「表头声明了发布日期列但整页一个日期都没有」）在长正文上
# 同样成立，所以不受长度影响。
FINGERPRINTS: dict[str, str] = {
    # markdown 表格的表头里有「发布日期」，但整页找不到任何真实日期
    "empty_table": (
        r"\|\s*(序号|标题|发布日期|名称|时间)\s*\|"
        r".*?\|\s*-{3,}\s*\|"
    ),
    # 标题就是机构名本身，不是公告标题
    "title_is_org_only": None,      # 由代码判定，见 _title_is_org_only
    # 正文里一个句末标点都没有（导航只有词条，正文有句子）
    "no_sentence_marks": None,      # 由代码判定
}

_FULL_DATE = re.compile(r"(20\d{2})\s*[-年/.]\s*\d{1,2}\s*[-月/.]\s*\d{1,2}")
_SENT_END = re.compile(r"[。！？；]")
_ORG_SUFFIX = re.compile(
    r"^[\u4e00-\u9fa5]{2,12}(厅|局|委|政府|厅|厅办公室|厅)$"
    r"|^.{2,20}(厅|局|委员会|政府)$"
)


def _title_is_org_only(title: str) -> bool:
    """标题就是机构名（"河北省商务厅"），没有「关于…的通知」这类正文性。"""
    t = (title or "").strip()
    if not t:
        return False
    if len(t) > 24:               # 真公告标题通常更长
        return False
    if _ORG_SUFFIX.match(t):
        return True
    # 「栏目名_机构名」也算：拆开看最后一段是不是机构名
    if "_" in t and _ORG_SUFFIX.match(t.split("_")[-1].strip()):
        return True
    return False


def _fingerprint(title: str, content: str) -> list[str]:
    hits: list[str] = []
    text = content or ""
    if re.search(FINGERPRINTS["empty_table"], text) and not _FULL_DATE.search(text):
        hits.append("empty_table")
    if _title_is_org_only(title):
        hits.append("title_is_org_only")
    if len(text.strip()) >= 120 and not _SENT_END.search(text):
        hits.append("no_sentence_marks")
    return hits


def _query(limit: int, since: str | None, missing_date: bool) -> list[tuple]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    where = []
    params: list[object] = []
    if since:
        where.append("created_at >= ?")
        params.append(since)
    if missing_date:
        where.append("(publish_date IS NULL OR trim(publish_date) = '')")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    sql = (f"SELECT source_id, title, publish_date, content "
           f"FROM announcements {clause} ORDER BY random() LIMIT ?")
    params.append(limit)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="质量抽查（不替你下结论）")
    parser.add_argument("--limit", type=int, default=14, help="抽多少条")
    parser.add_argument("--since", help="只看 created_at >= 这个时间")
    parser.add_argument("--missing-date", action="store_true",
                        help="只看缺 publish_date 的")
    parser.add_argument("--stats-only", action="store_true",
                        help="只统计指纹命中数，不打印样本")
    parser.add_argument("--record", choices=["garbage", "real"],
                        help="把这次人工判定的结论写进基准文件")
    args = parser.parse_args(argv)

    rows = _query(args.limit, args.since, args.missing_date)
    if not rows:
        print("没抽到样本")
        return 0

    tally: dict[str, int] = {}
    marked: list[dict] = []
    for source_id, title, pub, content in rows:
        hits = _fingerprint(title, content)
        for h in hits:
            tally[h] = tally.get(h, 0) + 1
        marked.append({"source_id": source_id, "title": (title or "")[:120],
                       "fingerprints": hits})

    print("=" * 70)
    print("  抽查样本 %d 条 —— **逐条读标题和正文首段再下结论**" % len(rows))
    print("=" * 70)
    if not args.stats_only:
        for (source_id, title, pub, content), info in zip(rows, marked):
            head = (content or "")[:150].replace("\n", " ")
            print()
            print("[%s] %s字  日期=%s" % (source_id[:18],
                                       len(content or ""), pub or "(无)"))
            print("  标题: %s" % (title or "")[:70])
            print("  正文: %s" % head)
            if info["fingerprints"]:
                print("  指纹: %s" % ", ".join(info["fingerprints"]))

    print()
    print("=" * 70)
    print("结构性指纹命中（比「缺日期」干净，不受正文长度影响）:")
    for key, n in sorted(tally.items(), key=lambda kv: -kv[1]):
        print("  %-22s %d / %d" % (key, n, len(rows)))
    if not tally:
        print("  （无命中）")
    print()
    print("提示：指纹命中≠垃圾。缺日期也不等于垃圾——2026-10-03 实测 150 条")
    print("      缺日期里 149 条是完整正文的转载新闻稿，只有 1 条真垃圾。")

    if args.record:
        payload = json.loads(VERDICTS.read_text(encoding="utf-8")) \
            if VERDICTS.exists() else {}
        payload.setdefault("verdicts", []).append({
            "at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
            "since": args.since,
            "sampled": len(rows),
            "label": args.record,
            "fingerprints_seen": tally,
        })
        VERDICTS.parent.mkdir(parents=True, exist_ok=True)
        VERDICTS.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        print()
        print("已记入基准文件: %s（label=%s）" % (VERDICTS, args.record))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())