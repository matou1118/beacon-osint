"""摸清 1465 条「其他」到底是什么，为补分类体系提供依据。

为什么先看数据再加类
  现有 13 个分类是招生/招录/招聘/养老/招标/补贴/人才/活动，加起来没有一个能
  装下「环保督察整改」「储能项目财政奖励」「煤电关停公示」这类政务内容，于是全部
  落到默认值「其他」。但补哪些类不能靠猜——先把真实标题的词频统计出来。

同时这也是分类器本身的问题定位：config/category_rules.yaml 里
  scoring.body_weight = 0
意味着正文完全不参与打分，只看标题。而 tuned_on 写的是「synthetic 5000 条带
标签数据」，98.3% 是合成数据上的分数，真实记录上只有 7%。
"""
from __future__ import annotations

import collections
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[1]

# 政务文书的体裁词。文种比内容关键词更可靠：它直接说明这是一份什么文件。
DOC_TYPE = re.compile(
    r"(通知|公告|公示|通告|批复|办法|规定|方案|规划|条例|细则|规程|"
    r"意见|决定|名单|结果|指南|标准|目录|说明|解读|征求意见|立案|处罚)"
)
# 主题词：按政务实际业务切分，而不是按现有那 13 个类。
TOPIC = re.compile(
    r"(补贴|补助|奖励|资金|扶持|贴息|减税|降费|申报|征集|遴选|评选|"
    r"招标|采购|中标|成交|磋商|比选|"
    r"招聘|招录|招考|引进|人才|就业|培训|考核|职称|"
    r"招生|录取|中考|高考|学区|学位|转学|"
    r"医保|医疗|养老|社保|低保|救助|住房|保障|公积金|婚姻|生育|"
    r"环保|污染|排放|能耗|双碳|碳排放|生态|督察|整改|"
    r"项目|工程|建设|改造|施工|审批|核准|备案|许可|登记|"
    r"执法|检查|监管|处罚|抽检|监测|预警|"
    r"规划|产业|经济|投资|贸易|消费|市场|价格|统计|数据|"
    r"教育|学校|学院|高校|文化|旅游|体育|卫生|健康|"
    r"科技|创新|信息化|数字|智慧|制造|能源|电力|农业|农村|交通|水利)"
)


def main() -> int:
    con = sqlite3.connect(f"file:{ROOT / 'data' / 'beacon.db'}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT id, source_id, title FROM announcements "
        "WHERE category_l1 = '其他' AND title IS NOT NULL"
    ).fetchall()
    total = con.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
    print(f"总量 {total} 条，其中待归类（category_l1=其他）{len(rows)} 条 "
          f"= {len(rows)/total:.0%}\n")

    doc_types: collections.Counter = collections.Counter()
    topics: collections.Counter = collections.Counter()
    by_source: collections.Counter = collections.Counter()
    hit_any = 0
    for _rid, source_id, title in rows:
        title = title or ""
        for kind in set(DOC_TYPE.findall(title)):
            doc_types[kind] += 1
        found = set(TOPIC.findall(title))
        if found:
            hit_any += 1
        for topic in found:
            topics[topic] += 1
        by_source[source_id] += 1

    print("=== 标题里的文种词 top20（这是哪一类文件）===")
    for kind, n in doc_types.most_common(20):
        print(f"   {kind:<8} {n:>5}  {n/len(rows):>5.0%}")
    print("\n=== 标题里的主题词 top30（讲的是什么）===")
    for topic, n in topics.most_common(30):
        print(f"   {topic:<10} {n:>5}  {n/len(rows):>5.0%}")
    print("\n=== 「其他」最多的源 ===")
    for source_id, n in by_source.most_common(14):
        print(f"   {source_id:<18} {n:>5}  {n/len(rows):>5.0%}")

    cover = sum(n for _t, n in topics.most_common(30))
    print(f"\ntop30 主题词可覆盖 {cover}/{len(rows)} = {cover/len(rows):.0%} 的记录")
    print(f"至少命中 1 个主题词: {hit_any}/{len(rows)} = {hit_any/len(rows):.0%}")
    print("\n=== 抽样看 20 条真实标题 ===")
    for _rid, source_id, title in rows[:20]:
        print(f"   [{source_id:<14}] {title[:52]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
