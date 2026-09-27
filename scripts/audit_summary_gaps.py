"""把剩下三处摘要问题量准，而不是凭上一轮的口头数字动手。

上一轮我说"20 条 1000+ 字超长"，但复核体检输出发现那是 601-1000 档，
1000+ 只有 1 条。数字记错了，先量准：

  A 过短（0-100 字）    —— 丢了关键信息，还是正文本来就短？
  B 摘要≈全文           —— 短正文下这是正常的；长正文下才是没做摘要
  C 超长（>600 字）      —— 剩下的 20 条是什么

判据必须区分"短正文"和"长正文"：一条 200 字的正文，摘要 180 字完全正常；
一条 3000 字的正文，摘要 2800 字就是没摘。所以 A/B 都要按正文长度分档看。
"""
from __future__ import annotations

import collections
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[1]

AMOUNT = re.compile(r"[0-9][0-9,]*(?:\.[0-9]+)?\s*[\|｜]?\s*(?:万元|亿元|万|亿|元)")
DEADLINE = re.compile(
    r"(?:截止|截至|不迟于|最后|报名时间|报名日期|公示时间|公示期|开标时间|开启时间|"
    r"接收时间|申报时间|申报期限|提交时间|送达时间|响应时间|有效期)"
    r"[^\n。；]{0,24}?20\d{2}\s*[-/年.]\s*\d{1,2}\s*[-/月.]\s*\d{1,2}")


def main() -> int:
    con = sqlite3.connect(f"file:{ROOT / 'data' / 'beacon.db'}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT id, source_id, title, content, key_points FROM announcements"
    ).fetchall()
    total = len(rows)
    print(f"总量 {total}\n")

    short: list[tuple] = []
    same: list[tuple] = []
    long_sum: list[tuple] = []
    for rid, source_id, title, content, key_points in rows:
        body = content or ""
        try:
            points = json.loads(key_points) if isinstance(key_points, str) else (key_points or [])
        except (TypeError, ValueError):
            points = []
        summary = "".join(str(p) for p in points)
        n = len(summary)
        if n == 0:
            continue
        item = (rid, source_id, title or "", len(body), n, bool(AMOUNT.search(body)),
                bool(DEADLINE.search(body)), bool(AMOUNT.search(summary) or DEADLINE.search(summary)))
        if n <= 100:
            short.append(item)
        if n > 600:
            long_sum.append(item)
        # "摘要≈全文"：只在正文不短时才值得怀疑
        if body and n >= len(body) * 0.95 and len(body) >= 800:
            same.append(item)

    def report(name: str, items: list[tuple], note: str) -> None:
        print(f"=== {name}: {len(items)} 条 ({len(items)/total:.0%}) —— {note}")
        if not items:
            print()
            return
        by_body = collections.Counter()
        for it in items:
            n = it[3]
            by_body["<300" if n < 300 else "300-800" if n < 800
                    else "800-2000" if n < 2000 else "2000+"] += 1
        print(f"    按正文长度: {dict(by_body)}")
        with_fact = sum(1 for it in items if it[7])
        print(f"    仍带关键事实: {with_fact} / {len(items)} = "
              f"{with_fact/len(items):.0%}")
        for rid, source_id, title, bl, sl, ba, bd, sf in items[:6]:
            flags = ("有金额" if ba else "") + ("有截止" if bd else "")
            print(f"    [{source_id:<14}] 正文{bl:>6} 摘要{sl:>5} {flags:<10} {title[:38]}")
        print()

    report("A 过短摘要(<=100字)", short,
           "短正文下正常；长正文下说明该留的没留")
    report("B 摘要≈全文(>=800字的正文)", same, "这才是真的没做摘要")
    report("C 超长摘要(>600字)", long_sum, "上限 600 仍有 20 条超出")

    # 事实行覆盖：有多少记录的摘要是靠头部事实行而不是正文句子
    with_line = con.execute(
        "SELECT COUNT(*) FROM announcements WHERE key_points LIKE '%金额：%'"
        " OR key_points LIKE '%截止：%' OR key_points LIKE '%发布日期：%'").fetchone()[0]
    print(f"=== 摘要有事实行的记录: {with_line} / {total} = {with_line/total:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
