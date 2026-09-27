"""摸清金额在正文里的真实形态，为准确抽取定规则。

用户要求"金额要想法办法准确抽取"，并给了方法论：有表格用表格，没表格
交叉印证。所以先要分清金额到底以什么形式出现——实测之前发现过三种完全不同
的形态，各需要不同处理：
  1. 表格行（管道符压平）  |采购预算|最高限价|…|495000元|495000元|服务|
  2. 正文散文              "安排资金300万元支持…"
  3. 标题                 "关于下达2026年补助资金500万元的通知"

还要分清**不是金额的数字**，这是准确性的关键：
  电话 028-86702309 / 邮编 610000 / 身份证 510100199001011234
  日期 2026-09-28 / 百分比 12.5% / 编号 渝人社规〔2026〕2号 / 面积 1200平方米
"""
from __future__ import annotations

import collections
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[1]

# 带货币单位的数字
MONEY = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(万元|亿元|万|元)(?![额人])")
# 管道符表格行
TABLE = re.compile(r"\|[^|\n]{2,}\|")
# 容易与金额混淆的非金额数字
NOT_MONEY = re.compile(
    r"(?:电话|传真|邮编|邮　编|开户行|账号|身份证|统一社会信用代码|"
    r"序号|编号|批号|证书号|文号|件号)")


def main() -> int:
    con = sqlite3.connect(f"file:{ROOT / 'data' / 'beacon.db'}?mode=ro", uri=True)
    rows = con.execute("SELECT id, title, content, amount FROM announcements").fetchall()
    total = len(rows)
    print(f"总量 {total}\n")

    in_table = in_prose = in_title = 0
    table_rows: list[tuple] = []
    prose: list[tuple] = []
    for rid, title, content, amount in rows:
        body = content or ""
        if TABLE.search(body):
            in_table += 1
            for line in body.splitlines():
                if line.count("|") >= 2:
                    table_rows.append((rid, line))
        if MONEY.search(body):
            in_prose += 1
            for m in MONEY.finditer(body):
                ctx = body[max(0, m.start() - 18):m.end() + 8]
                if not NOT_MONEY.search(ctx):
                    prose.append((rid, ctx))
        if title and MONEY.search(title):
            in_title += 1

    print(f"=== 金额出现的位置 ===")
    print(f"  正文含管道符表格行  {in_table:>5} 条 ({in_table/total:.0%})")
    print(f"  正文含金额          {in_prose:>5} 条 ({in_prose/total:.0%})")
    print(f"  标题含金额          {in_title:>5} 条 ({in_title/total:.0%})")

    print(f"\n=== 表格行样例（共 {len(table_rows)} 行）===")
    for rid, line in table_rows[:10]:
        cells = [c.strip() for c in line.split("|") if c.strip()]
        print(f"  [{rid}] {len(cells)} 格")
        print(f"      {cells[:8]}")

    print(f"\n=== 正文金额样例（排除电话/邮编类，共 {len(prose)} 处）===")
    for rid, ctx in prose[:12]:
        print(f"  [{rid}] …{ctx.strip()[:56]}…")

    # 单位分布
    units = collections.Counter()
    for _rid, title, content, _a in rows:
        for m in MONEY.finditer((content or "") + " " + (title or "")):
            units[m.group(2)] += 1
    print(f"\n=== 单位分布 ===\n  {dict(units.most_common())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
