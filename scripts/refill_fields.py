"""给结构化字段补抽，让摘要头部能带上金额/截止/申报对象。

为什么需要
  announcements.amount 只有 10% 有值、deadline 只有 6%，而这两个恰恰是
  摘要里最该出现的东西。根因是 policy_fields.BODY_PATTERNS 的线索词表太窄：
  金额只认「补贴|资助|奖励|补助|扶持」，而政务公告实际写的是「采购预算」
  「最高限价」「控制价」「成交价」「售价」；截止只认「截止|截至|申报时间」，
  实际还有「报名时间」「公示时间」「开标时间」「有效期」。

  本脚本用放宽后的规则扫全库，把抽到的值写回 amount / deadline / target_group
  / institution_name。摘要重算时（scripts/resummarize.py）会把这些字段拼成
  「金额：…｜截止：…」放最前面。

  谨慎项
  - 只在字段为空时写，已有值不覆盖（不破坏人工/上游已确认的数据）
  - 抽出的值超过 60 字就丢弃：说明正则抓歪了，把整句话当成了值
  - 改动前先备份；--apply 才落盘
  - 只读解析，不碰 robots / 不发请求

用法
  python scripts/refill_fields.py            # 预览
  python scripts/refill_fields.py --apply    # 落盘
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[1]

from policy_fields import _patterns_for  # noqa: E402

# 字段 -> (列, 归一化后的值上限)
FIELDS = {
    "subsidy_amount": ("amount", 60),
    "deadline": ("deadline", 60),
    # target_group 不进补抽。
    #
    # 补抽 107 条里抽样仍有明显错的：「1.各省级文化和旅游行政部门：每省…」
    # 「等需满足指南各项要求，牵头申报单位和项目负责人要对申报材料负责…」——
    # "申报条件/申报主体"后面跟 4-60 字这个抓法在通知正文里太容易撞上正常句子。
    # 它对摘要的价值也最低（金额和截止才是刚需），所以宁可不补，也不往摘要头部
    # 塞一行错的话。要补的话得先做句式判定，收益不抵成本。
    "target_group": ("target_group", 0),   # 0 = 禁用
    "institution": ("institution_name", 60),
}
# 抽歪了的典型形态：值里带句号/换行，或整句超过上限
_BAD_CHARS = re.compile(r"[。\n；;]")
_LEAD_LABEL = re.compile(r"^(?:时间|日期|期限|截止|截至|起|至)\s*[:：]?\s*")

# 各字段的"必须像什么"校验。
#
# 没有校验时 834 个待写里绝大多数是垃圾：「截止」的兜底正则抓的是后面 4-40 个
# 字符，于是「公示期间，接受公众来电…」被当成截止日期；「申报对象」抓成
# 新闻正文；「责任机构」抓成电话号码。抽样就能看出一半以上是错的。
#
# 判据用"值里必须出现该类型的内容"，而不是"长度在范围内"——
# 长度对"间，将接受公众来电、来信、来访，对所反映的问题进行调查"也成立。
_MUST_CONTAIN = {
    "subsidy_amount": re.compile(r"\d"),
    # 日期/期限：必须有年月或日点，且不能只是"（节假日除外）"这类尾巴
    "deadline": re.compile(r"\d{1,2}\s*[月\-/.]\s*\d{1,2}\s*日?"
                           r"|\d{4}\s*年|\d{1,2}\s*月|日\s*\d{1,2}\s*:\d{2}"
                           r"|\d{1,2}\s*:\d{2}"),
    # 申报对象：应该有组织/人群/单位这类名词，或"符合…要求"这类表述
    "target_group": re.compile(r"(?:单位|企业|个人|居民|机构|部门|申报|申请|"
                              r"符合|具备|取得|持有|辖区|在.{1,12}(?:境内|注册)"
                              r"|以上|不低于|不低于|不少于)"),
    # 责任机构：应该是机构名，不该是纯电话/地址
    "institution": re.compile(r"(?:局|厅|委|办|中心|公司|机构|部门|院|校|所|协会|"
                             r"管理处|服务局|管委会)"),
}


def clean(value: str) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    value = _LEAD_LABEL.sub("", value)
    return value.strip(" ：:，,、")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(ROOT / "data" / "beacon.db"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--show", type=int, default=10)
    args = parser.parse_args()

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id, title, content, amount, deadline, target_group, institution_name "
        "FROM announcements WHERE content IS NOT NULL").fetchall()
    total = len(rows)
    print(f"扫描 {total} 条\n")

    updates: list[tuple[str, str, int]] = []
    filled = {name: 0 for name in FIELDS}
    samples: dict[str, list[tuple[int, str]]] = {name: [] for name in FIELDS}

    for row in rows:
        body = row["content"] or ""
        if not body.strip():
            continue
        for field, (column, limit) in FIELDS.items():
            if limit == 0:
                continue  # 该字段本轮禁用
            if row[column]:
                continue  # 已有值不覆盖
            for _name, (pattern, _conf) in _patterns_for(field):
                match = pattern.search(body)
                if not match:
                    continue
                value = clean(match.group(1))
                if not value or len(value) > limit or _BAD_CHARS.search(value):
                    continue
                if not _MUST_CONTAIN[field].search(value):
                    continue
                updates.append((value, column, row["id"]))
                filled[field] += 1
                if len(samples[field]) < args.show:
                    samples[field].append((row["id"], value))
                break

    print("=== 将会补上的字段 ===")
    for field, count in filled.items():
        column = FIELDS[field][0]
        print(f"   {column:<18} {count:>5} 条")
        for rid, value in samples[field][:5]:
            print(f"       [{rid}] {value[:60]}")

    if not updates:
        print("\n没有可补的字段")
        return 0
    if not args.apply:
        print(f"\n（预览模式，{len(updates)} 个待写。加 --apply 落盘）")
        return 0

    # 备份
    import shutil

    stamp = __import__("datetime").datetime.now().strftime("%Y%m%dT%H%M%S")
    backup = ROOT / "data" / "backups" / f"beacon-{stamp}-pre-refill.db"
    backup.parent.mkdir(exist_ok=True)
    src = sqlite3.connect(args.db)
    dst = sqlite3.connect(str(backup))
    src.backup(dst)
    dst.close()
    src.close()
    print(f"备份 -> {backup.name}")

    write = sqlite3.connect(args.db)
    by_column: dict[str, list[tuple[str, int]]] = {}
    for value, column, rid in updates:
        by_column.setdefault(column, []).append((value, rid))
    for column, pairs in by_column.items():
        write.executemany(
            f"UPDATE announcements SET {column} = ? WHERE id = ?", pairs)
    write.commit()
    write.close()
    print(f"已写回 {len(updates)} 个字段值")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
