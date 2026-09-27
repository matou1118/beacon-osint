"""摘要质量体检：不只看长度，看有没有把关键信息留住。

为什么不能只看长度
  上一轮把"超过 600 字"从 33% 打到 2%，靠的是字数上限。但字少不等于摘要好——
  可能只是截掉了一半，用户该看到的金额/截止日期/申报主体全丢了。上一轮我就
  撞到过：id=2203 的 content 有 187,122 字，摘要被补到 18,993 字；反过来一个
  3 行的短通知也可能什么都不剩。

所以这里量四件事
  1. 长度分布（是否失控）
  2. 关键信息保留率：原文有的金额/截止日期/日期，摘要里还剩几个
  3. 摘要是否空（0 字或等于正文——后者说明没真正做摘要）
  4. 框架文字残留（打印/字号/上一篇…）
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sqlite3
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[1]

AMOUNT = re.compile(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:万元|亿元|万|亿|元)")
DEADLINE = re.compile(r"(?:截止|截至|申报期限|报名时间|投标截止)[^\d]{0,12}"
                      r"(20\d{2}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2}"
                      r"|\d{1,2}\s*月\s*\d{1,2}\s*日)")
# 日期。必须宽松：政务正文的日期有四种写法——
#   2026年9月24日 / 2026-09-24 / 9月24日 / 2026年（只有年）
# 第一版只认 2026年9月24日 这一种，测出来"保留率 24%"，看着像严重问题，
# 逐条看才发现摘要里其实有日期（"2026年10月10日前反馈"），只是正文侧被
# 编码损坏成"0月0日"而正则要求 \d{1,2} 匹配不上。校准后是 82%。
# 日期。**只统计"关键日期"**，即带语义线索词（截止/施行/有效期…）的那些。
#
# 第一版统计"正文里所有日期是否都在摘要里"，测出来 21%，看着像严重问题。
# 那个口径是错的：正文动辄几十个日期（发布日、沿革年份、批次号…），摘要只有
# 3-8 句，集合比较天然不成立。而且活动类公告的日期常挂在标题末尾
# （"…在河北高碑店举办 9月24日"），摘要从正文取，本来就不该指望它出现。
#
# 关键日期才是产品真正依赖的：投标截止、公示期、报名时间、施行日期。
# 校准后这个指标是 65%，而且丢的都是真损失。
PUBLISH = re.compile(
    r"(?:截止|截至|不迟于|最后|报名|公示|开标|开启|接收|申报|提交|送达|有效期|"
    r"施行|实施|起施行)[^\n。；]{0,24}?20\d{2}\s*[-/年.]\s*\d{1,2}\s*[-/月.]\s*\d{1,2}"
)
# 只列**不会出现在正常公文里**的框架词。
#
# 第一版把「备案」「无障碍」也算成框架词，结果 66 条汽车召回摘要被误报：
#   "向国家市场监督管理总局备案了召回计划" ——「备案」是这段正文的正常用词。
# 判据是"这个词出现在公文正文里是否正常"，而不是"它常出现在门户页上"。
CHROME = ("【打印】", "[打印]", "打印此页", "打印本页", "字号：", "【关闭",
          "上一篇", "下一篇", "分享到", "当前位置", "返回顶部", "网站地图",
          "扫一扫", "版权所有", "访问量：", "关闭窗口", "网站标识码", "ICP备")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--show", type=int, default=10)
    args = parser.parse_args()

    con = sqlite3.connect(f"file:{ROOT / 'data' / 'beacon.db'}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT id, source_id, title, content, key_points FROM announcements"
    ).fetchall()
    total = len(rows)
    print(f"总量 {total}\n")

    lengths: list[int] = []
    empty = 0
    same_as_content = 0
    has: dict[str, int] = collections.Counter()
    keep: dict[str, list[int]] = {"金额": [], "截止": [], "日期": []}
    chrome_hit: list[tuple] = []
    worst: list[tuple] = []
    unparsed = 0

    for rid, source_id, title, content, key_points in rows:
        try:
            points = json.loads(key_points) if isinstance(key_points, str) else (key_points or [])
        except (TypeError, ValueError):
            points = []
            unparsed += 1
        if not isinstance(points, list):
            points = []
            unparsed += 1
        summary = "".join(str(p) for p in points)
        body = content or ""
        n = len(summary)
        if n == 0:
            empty += 1
        if body and n and n >= len(body) * 0.95:
            same_as_content += 1
        if n:
            lengths.append(n)
            if n > 600:
                worst.append((n, rid, source_id, title or ""))
        for word in CHROME:
            if word in summary:
                chrome_hit.append((rid, word, title or ""))
                break

        # 关键信息保留：原文里有、摘要里没有 = 丢了
        for name, pattern, dest in (("金额", AMOUNT, "金额"),
                                    ("截止", DEADLINE, "截止"),
                                    ("关键日期", PUBLISH, "日期")):
            # 标题也要算进去：活动类公告的日期常挂在标题末尾。
            in_body = len(set(pattern.findall(body + " " + (title or ""))))
            if in_body:
                has[dest] += 1
                in_sum = len(set(pattern.findall(summary)))
                # 关键日期只要"有"就算保住（个数不应相等——摘要空间有限）；
                # 金额和截止要求个数不减，因为它们通常只有一两个。
                keep[dest].append(1 if (in_sum >= 1 if name == "关键日期" else in_sum >= in_body)
                                  else 0)

    print("=== 1. 长度 ===")
    if lengths:
        s = sorted(lengths)
        print(f"   非空摘要 {len(lengths)} 条 | 中位 {statistics.median(s):.0f} 字"
              f" | 均值 {statistics.mean(s):.0f} | p90 {s[int(len(s)*.9)]} | max {max(s)}")
        b = collections.Counter()
        for n in s:
            b["0-100" if n <= 100 else "101-200" if n <= 200 else "201-400" if n <= 400
              else "401-600" if n <= 600 else "601-1000" if n <= 1000 else "1000+"] += 1
        for k in ("0-100", "101-200", "201-400", "401-600", "601-1000", "1000+"):
            if b[k]:
                print(f"     {k:<10} {b[k]:>5}  {b[k]/len(s):>5.0%}")
    print(f"   空摘要 {empty} 条 ({empty/total:.0%})"
          f" | 摘要≈全文 {same_as_content} 条 ({same_as_content/total:.0%})"
          f" | key_points 解析失败 {unparsed} 条")

    print("\n=== 2. 关键信息保留率（原文有 → 摘要里还有）===")
    for name in ("金额", "截止", "日期"):
        flags = keep[name]
        if flags:
            kept = sum(flags)
            label = "关键日期" if name == "日期" else name
            print(f"   {label:<8} 原文有 {has[name]:>4} 条，摘要里保住 "
                  f"{kept:>4} 条 = {kept/len(flags):>0.0%}")

    print("\n=== 3. 框架文字残留 ===")
    print(f"   摘要含站点框架词: {len(chrome_hit)} 条 ({len(chrome_hit)/total:.0%})")
    for rid, word, title in chrome_hit[:args.show]:
        print(f"     「{word}」 {title[:48]}")

    print(f"\n=== 4. 超长摘要 top{args.show} ===")
    for n, rid, source_id, title in sorted(worst, reverse=True)[:args.show]:
        print(f"   {n:>6}字  [{source_id:<14}] {title[:44]}")

    print(f"\n=== 5. 抽样人工看 {args.show} 条 ===")
    for rid, source_id, title, content, key_points in rows[:args.show]:
        try:
            points = json.loads(key_points) if isinstance(key_points, str) else (key_points or [])
        except (TypeError, ValueError):
            points = []
        print(f"\n--- [{source_id}] {title[:50]}")
        print(f"    正文 {len(content or '')} 字 -> 摘要 {sum(len(str(p)) for p in points)} 字"
              f" / {len(points)} 条要点")
        for p in points[:2]:
            print(f"      · {str(p)[:96]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
