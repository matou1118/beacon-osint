"""分析 343 条兜底记录，判断是分类器的问题还是数据本身的问题。

上一轮分类从 93% 兜底降到 22%，剩下的不能再靠加词硬凑——每加一轮词就要在
真实语料上做一轮取舍，合成基线也跟着冒险。这一步是先把剩余兜底的构成看清楚：
  - 多少是栏目页/导航（该清，不该分类）
  - 多少是真公告但没词能命中（该补词）
  - 多少是新闻/播报（不是公告体裁）
"""
from __future__ import annotations

import collections
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[1]

# 站点框架/栏目页特征。内容为空或极短、标题像栏目名的，都不是公告。
NAV_TITLES = ("门户网站", "政务服务", "网站首页", "返回首页", "手机应用", "微信",
              "微博", "公众号", "导航", "跳转", "信箱", "投诉", "智能问答",
              "无障碍", "长者", "适老化", "登录", "注册", "搜索", "网站地图",
              "版权", "联系我们", "主办", "主管")


def bucket(title: str, content: str) -> str:
    text = title or ""
    if len(content or "") < 50:
        return "A_无正文(抓取失败)"
    if any(word in text for word in NAV_TITLES):
        return "B_栏目页特征"
    if len(text) <= 8:
        return "C_超短标题"
    if re.search(r"(举办|召开|开幕|启动|举行|出席|会见|抵达|考察)", text):
        return "D_活动新闻"
    if re.search(r"(同比增长|环比|再破|数据情况|运行情况|指数)", text):
        return "E_数据播报"
    return "F_真公告但未命中"


def main() -> int:
    con = sqlite3.connect(f"file:{ROOT / 'data' / 'beacon.db'}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT id, source_id, title, content FROM announcements "
        "WHERE category_l1 = '其他' AND title IS NOT NULL"
    ).fetchall()
    total = con.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
    print(f"总量 {total}，兜底 {len(rows)} = {len(rows)/total:.0%}\n")

    groups: dict[str, list] = collections.defaultdict(list)
    for rid, source_id, title, content in rows:
        groups[bucket(title, content or "")].append((rid, source_id, title))

    for name in sorted(groups):
        items = groups[name]
        print(f"{name}: {len(items)} 条 ({len(items)/len(rows):.0%})")
        by_source = collections.Counter(s for _r, s, _t in items)
        print(f"    源: {dict(by_source.most_common(6))}")
        for _rid, _s, t in items[:5]:
            print(f"      · {t[:56]}")
        print()

    actionable = sum(len(groups[k]) for k in
                     ("A_无正文(抓取失败)", "B_栏目页特征", "C_超短标题"))
    print(f"{'='*60}")
    print(f"该清不该分的: {actionable} 条 ({actionable/len(rows):.0%})")
    print(f"该补词的:     {len(groups.get('F_真公告但未命中', []))} 条")
    print(f"非公告体裁:   "
          f"{len(groups.get('D_活动新闻', [])) + len(groups.get('E_数据播报', []))} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
