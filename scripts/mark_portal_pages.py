"""标记纯门户/栏目页为 needs_review=1。

**不删数据** —— 401 条判错就是永久丢失，标出来让人能筛、能查、能复核。

判据（三层，必须都满足才算「纯门户」）：

  1. `is_portal_or_section` 判为门户（标题像站点名/栏目，或正文短且含分页标记）
  2. 标题**不含公文动词** —— 含「通知/公告/印发…」的是真文档，只是标题带了
     栏目后缀，不该标记（实测 580 条里有 179 条属于这种）
  3. 正文**不含实质内容** —— 纯导航列表（`省厅召开会议 2026.09.24 …`）

只做标记，附带把 reason 写进 extra，方便工作台显示「为什么被标记」。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from compare_topics import DOCUMENT_VERB_RE
from quality_marks import fetch_announcements, is_portal_or_section

# 正文里出现这些说明有实质内容，不该标记
_NAV_PREFIX = ("首页", "更多", "返回", "网站地图", "关于我们", "联系我们",
               "版权", "备案", "无障碍", "长辈版", "打印", "关闭", "分享")
_SUBSTANTIVE_RE = None


def substantive(content: str) -> bool:
    """正文里有没有**实质内容**（成段的正文，而不是导航/列表/页脚）。

    这个判据试了三轮才对，过程值得记下来：

    1. 「连续 15 字以上且不含 http」太松 —— 门户页导航文字也满足。
    2. 「正文 >= 800 字」也松 —— 门户页把整站导航抓进来能有 2700 字。
    3. 「成段（每行 30+ 字）」也不行 —— **采集时正文被压成 1 行**，换行符丢失，
       门户页和真文档都成了「单行长文本」。这一轮踩的坑。

    换成看**语言形态**而不是行结构：真文档正文是完整句子（有句号/逗号、
    主谓宾、长度 40 字以上）；门户页是**标题+日期/摘要的列表**——短、密、
    大量数字日期、没有句读。

    判据：正文里能找出至少 2 个「>=35 字且含句读（。，；：）」的片段
    → 有实质内容；否则判为列表/导航。
    """
    text = str(content or "").strip()
    if not text:
        return False
    flat = re.sub(r"\s+", " ", text)

    # 按句读切分，找「够长的完整句子」
    sentences = [s for s in re.split(r"[。！？；]", flat) if len(s.strip()) >= 35]
    if len(sentences) >= 2:
        return True

    # 日期列表形态：标题后紧跟 (2026-09-28) 或 日期，命中 5 次以上
    dates = re.findall(r"[（(]\s*20\d{2}[-年]\d{1,2}[-月]?\d{0,2}日?\s*[)）]", flat)
    if len(dates) >= 5:
        return False

    # 一个长句子都没有 -> 列表/导航
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="真正写库（默认只预览）")
    args = ap.parse_args()

    db = ROOT / "data" / "beacon.db"
    recs = fetch_announcements(str(db))
    portals = [r for r in recs if is_portal_or_section(r)]

    mark: list[tuple] = []
    keep: list[tuple] = []
    for r in portals:
        title = str(r.get("title") or "")
        content = str(r.get("content") or "")
        rid = r.get("id")
        if DOCUMENT_VERB_RE.search(title):
            keep.append((rid, title, "标题含公文动词，是真文档"))
            continue
        if substantive(content):
            keep.append((rid, title, "正文有实质内容"))
            continue
        mark.append((rid, title, "门户/栏目页，正文为导航"))

    print("门户页标记总数: %d" % len(portals))
    print("  将标记 needs_review=1: %d" % len(mark))
    print("  保留（真文档）        : %d" % len(keep))
    print()
    print("=== 保留样例（前 8）===")
    for rid, t, why in keep[:8]:
        print("   %-6s %s" % (rid, t[:56]))
    print()
    print("=== 标记样例（前 8）===")
    for rid, t, why in mark[:8]:
        print("   %-6s %s   <- %s" % (rid, t[:48], why))

    if not args.apply:
        print("\n（预览模式，未写库。加 --apply 落盘）")
        return 0

    con = sqlite3.connect(db)
    try:
        cur = con.cursor()
        n = 0
        for rid, title, why in mark:
            row = cur.execute(
                "SELECT extra FROM announcements WHERE id = ?", (rid,)).fetchone()
            try:
                extra = json.loads((row[0] if row else "") or "{}")
            except (TypeError, ValueError):
                extra = {}
            extra["portal_page_marked"] = why
            cur.execute(
                "UPDATE announcements SET needs_review = 1, extra = ? WHERE id = ?",
                (json.dumps(extra, ensure_ascii=False), rid))
            n += 1
        con.commit()
    finally:
        con.close()
    print("\n已标记 %d 条 needs_review=1（未删除任何记录）" % n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())