"""行业维度：与「体裁」正交的一等分类，用于跨省政策研究。

问题
----
现有 ``category_l1`` 混编了三种语义：

* **体裁**（文件长什么样）：公示公告 / 政策法规 / 政策发布 / 新闻动态 …共 64%
* **主题**（讲什么）：行业政策 / 商业机会 / 招标信息 / 人才政策 …共 34%
* **部门**（谁发的）：藏在 ``category_l2`` 里，21 个行业门类

后果很具体：**64% 的记录在体裁判就丢了行业信息**。「公示公告」里，
科技政策的公示和农业补贴的公示混在一起，于是无法回答
「各省的科技创新政策趋势」这种问题——而这正是研究行业政策发展方向的基础。

为什么不改category_l1
--------------------
直接改会破坏既有查询（成刊、统计、工作台都按它过滤），而且**两个维度
本来就需要并存**：「这条是什么体裁」和「这条讲什么行业」是两个独立的问题，
塞进一个字段必然丢一个。分开之后前端可以默认按行业聚合，体裁作为次级筛选。

行业判定
--------
优先用**发文机构**——比标题关键词可靠得多（实测：「财政部关于记账式贴现国债
发行」不管标题怎么写都是财政金融）。机构判不出来时退回标题/正文关键词，
再判不出来就是「未分类」，**不硬猜**（同 stage|baseline_is_dirty：
宁可留空，不要编一个看起来合理的分类）。

用法
----
    python scripts/industry_taxonomy.py --build   # 写库：加 industry 列并回填
    python scripts/industry_taxonomy.py --report  # 看各省 × 行业的分布
    python scripts/industry_taxonomy.py --trend  # 看某行业的各省政策强度
"""
from __future__ import annotations

import argparse
import collections
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DB = ROOT / "data" / "beacon.db"

# ── 行业门类 ────────────────────────────────────────────────────
# 来源：现有 config/category_rules.yaml 里「行业政策」的 21 个二级分类，
# 再合并几个明显缺失的（教育、人口、退役军人、法律援助）。
# 用「门类 -> 关键词/机构关键词」的顺序匹配，**先长后短**避免「工业制造」
# 被「工业」抢先匹配。
INDUSTRIES: list[tuple[str, tuple[str, ...]]] = [
    ("科技创新", ("科技", "创新", "知识产权", "专精特新", "技术攻关", "研发",
                "高新技术", "成果转化", "科技项目", "双创", "孵化")),
    ("工业制造", ("工业", "制造", "装备", "钢铁", "化工", "建材", "机械",
                "半导体", "芯片", "新材料", "智能制造", " discrete")),
    ("能源电力", ("能源", "电力", "光伏", "风电", "核电", "电网", "煤炭",
                "油气", "储能", "天然气", "水电")),
    ("生态环保", ("环保", "污染", "排放", "环境影响评价", "环评", "督察整改",
                "生态", "碳排放", "双碳", "碳达峰", "环境")),
    ("农业农村", ("农业", "农村", "乡村振兴", "耕地", "种业", "粮食",
                "高标准农田", "农产品", "农机", "渔", "林业", "水利")),
    ("交通物流", ("交通", "公路", "铁路", "民航", "港口", "物流", "快递",
                "运输", "高速", "航道", "枢纽")),
    ("文化旅游", ("文旅", "旅游", "文化", "文物", "体育", "景区", "演出",
                "非遗", "博物馆")),
    ("医疗保障", ("医保", "医疗", "卫生健康", "疾病预防", "公共卫生", "医联体",
                "医院", "药品", "疫苗", "中医药")),
    ("教育人才", ("教育", "学校", "高校", "职业院校", "招生", "学位", "教材",
                "教师", "留学")),
    ("财政金融", ("财政", "预算", "税", "金融", "信贷", "保险", "基金", "国债",
                "会计", "审计")),
    # ★ 不含「目录」「指南」——它们是**体裁词**不是行业词。
    #
    # 2026-10-05 实测：商务贸易里 37 条含「目录」、23 条含「指南」，抽读发现是
    # 「应急管理部关于修改《危险化学品目录》…的通知」这类——目录/指南描述的是
    # 文件的形态（一份清单、一份手册），不是它讲的行业。
    # 这和「缺日期≠垃圾」是同一类错误：**把文件形态当成了内容主题**。
    ("商务贸易", ("商务", "对外贸易", "外贸", "进出口", "外资", "招商引资",
                "口岸", "跨境电商", "消费促进")),
    ("市场监管", ("市场监管", "食品安全", "抽检", "质量监督", "价格监督",
                "反不正当竞争", "知识产权保护", "标准")),
    ("应急安全", ("应急", "安全生产", "消防", "防汛", "防灾", "救援",
                "隐患排查", "事故", "危险化学品", "危化品", "安全生产责任")),
    ("住房保障", ("住房", "保障房", "公积金", "不动产", "房地产", "城乡建设",
                "城市更新", "物业")),
    # ★ 不含「民政」二字。
    #
    # 2026-10-05 实测：原本这里有「民政」，于是 1380 条标题被判成民政社会，
    # 抽读发现全是「陕西省人民政府**民政厅**关于…」「XX市民政局…」——
    # **那是发文机关名，不是行业主题**。省级部门名（民政厅/退役军人事务厅/
    # 市场监管局…）都会出现在标题首部，只要当行业词用，
    # 该部门发的**所有**文件都会被归到那个行业去。
    # 机关名只该走 DEPARTMENT/PROVINCE_DEPT 两张机构表（那里本来就有），
    # 不能进关键词表。
    ("民政社会", ("社会救助", "养老服务", "社会组织", "慈善", "婚姻登记",
                "殡葬", "残疾人保障", "社区治理")),
    ("人口退役", ("人口", "计生", "退役军人", "军转", "优抚", "双拥")),
    ("司法法治", ("司法", "法治", "律师", "公证", "仲裁", "法律援助", "普法")),
    ("资源环境", ("自然资源", "国土", "矿产", "测绘", "地质")),
]

# 发文机构 -> 行业。比标题可靠。
DEPARTMENT_INDUSTRY: list[tuple[str, tuple[str, ...]]] = [
    ("科技创新", ("科技部", "科学技术部", "国家知识产权局", "专利局")),
    ("工业制造", ("工业和信息化部", "工信部")),
    ("能源电力", ("国家能源局", "能源局")),
    ("生态环保", ("生态环境部", "环保部")),
    ("农业农村", ("农业农村部", "农业部")),
    ("交通物流", ("交通运输部", "中国民用航空局", "中国铁路")),
    ("文化旅游", ("文化和旅游部", "文化和旅游", "国家文物局", "体育总局")),
    ("医疗保障", ("国家卫生健康委员会", "卫健委", "医保局", "国家中医药管理局")),
    ("教育人才", ("教育部", "人力资源和社会保障部", "人社部", "国家留学")),
    ("财政金融", ("财政部", "中国人民银行", "金融监管总局", "证监会", "税务总局")),
    ("商务贸易", ("商务部")),
    ("市场监管", ("市场监管总局", "国家标准化管理", "知识产权局")),
    ("应急安全", ("应急管理部", "国家消防救援")),
    ("住房保障", ("住房和城乡建设部", "住建部")),
    ("司法法治", ("司法部", "最高人民法院", "最高人民检察院", "公安部")),
    ("资源环境", ("自然资源部", "国家林业和草原局", "国家测绘地理信息")),
    ("人口退役", ("国家卫生健康委", "退役军人事务部")),
]

# 省级部门名（不带「部」）
PROVINCE_DEPT_INDUSTRY: list[tuple[str, tuple[str, ...]]] = [
    ("科技创新", ("科技厅", "科技局", "科技委", "知识产权局")),
    ("工业制造", ("工信厅", "工信局", "经济和信息化厅", "经济和信息化局")),
    ("能源电力", ("能源局", "能源厅")),
    ("生态环保", ("生态环境厅", "环保局", "生态环境局")),
    ("农业农村", ("农业农村厅", "农业厅", "农业农村局", "农牧厅", "林业厅")),
    ("交通物流", ("交通运输厅", "交通厅", "交通运输局")),
    ("文化旅游", ("文旅厅", "文化和旅游厅", "体育局")),
    ("医疗保障", ("卫健委", "卫生健康委", "卫生健康厅", "医保局")),
    ("教育人才", ("教育厅", "教育局", "人社厅", "人社局", "人才")),
    ("财政金融", ("财政厅", "财政局", "金融办", "税务局")),
    ("商务贸易", ("商务厅", "商务局", "贸促会")),
    ("市场监管", ("市场监管局", "市场监管厅", "质量技术监督局")),
    ("应急安全", ("应急管理厅", "应急管理局", "消防救援支队")),
    ("住房保障", ("住房和城乡建设厅", "住建厅", "住建局")),
    ("民政社会", ("民政厅", "民政局", "人社厅")),
    ("人口退役", ("退役军人事务厅", "退役军人事务局")),
    ("司法法治", ("司法厅", "司法局", "公安厅", "公安厅")),
    ("资源环境", ("自然资源厅", "自然资源局", "林草局")),
]


# 标题里的发文机关前缀。中文公文的标准句式是
# 「<机关名称>关于<主题>的通知/公告/公示」，机关名一定在最前面。
#
# 2026-10-05 实测：数据库的 institution_name 字段**只有 27 条有值（0.2%）**，
# 而且全是招标代理公司——抽取规则早已失效。可发文机关其实就明明白白写在标题
# 首部（「山东省人力资源和社会保障厅关于开展…的通知」），比任何字段都可靠。
# 所以机构判定的第一顺位应该是**从标题里解析机关名**，而不是读那个空字段。
_ISSUING_RE = re.compile(
    r"^\s*([\u4e00-\u9fa5]{3,40}?(?:"
    r"人民政府办公厅|人民政府|管理委员会|厅|局|委|部|办公厅|办公室|"
    r"总局|管理局|监事会|办公室|中心|集团|公司"
    r"))"
    r"(?:\s*[\u4e00-\u9fa5]{0,30}?(?:厅|局|委|部|办公厅))?"
    r"\s*(?:关于|印发|发布|公布|开展|组织|做好|的通知|的公告)")


# 部委简称：这类文件标题以「关于…」开头，机关名不出现在首部
# （实测财政部国债发行那一批 26% 的命中率里就漏了它们），
# 但**文种和主题能反推机关**——国债/会计准则只可能是财政部。
_MINISTRY_HINT = [
    ("财政金融", ("记账式贴现", "国债发行", "会计准则", "会计司",
                "国库", "转移支付", "预算管理", "增值税", "企业所得税")),
    ("教育人才", ("学位", "教材", "普通高校", "职业院校", "教师资格")),
    ("医疗保障", ("医保", "药品", "疫苗", "临床试验", "罕见病")),
    ("科技创新", ("高新技术企业认定", "专精特新", "技术攻关", "科技成果转化")),
]


def issuing_body_of(record: dict) -> str:
    """取发文机关：优先标题首部，其次机构字段。

    顺序反过来不行——2026-10-05 实测 institution_name 只有 0.2% 有值，
    指望它等于没有。而标题首部的机关名是公文格式的一部分，**几乎不会缺**。
    """
    title = str(record.get("title") or "")
    m = _ISSUING_RE.match(title)
    if m:
        return m.group(0)
    # 部委文件常用「关于…的通知」句式，机关名不在首部。这类靠**主题词**
    # 反推——国债发行/会计准则只可能是财政部，不会是别的部门。
    for industry, markers in _MINISTRY_HINT:
        for w in markers:
            if w in title:
                return industry
    for key in ("institution_name", "issuing_body"):
        value = str(record.get(key) or "").strip()
        if value:
            return value
    return ""


def classify(record: dict) -> str:
    """判定行业。顺序：发文机关 > 标题关键词 > 未分类。

    **只看机关名和标题，不看正文全文。**

    2026-10-05 实测：第一版按「机构名 + 正文前 4000 字」匹配，商务贸易占 51%，
    抽检 200 条误判 40 条（20%）——正文里几乎每篇都提到「贸易」「消费」「招商」，
    连「养老有补贴 孝心更从容」的民政部公示都因正文提到消费被判成商务贸易。

    收紧后的规则：
      * 机关判定最优先，且机关名**从标题首部解析**（institution_name 字段
        实测只有 0.2% 有值，指向它等于没有）。
      * 行业词**只看标题**——标题是编纂者对主题的概括；正文里出现某个行业词
        不代表这条政策属于那个行业。
      * 找不到就返回「未分类」。宁可留空也不猜——猜出来的分类会污染跨省对比，
        而跨省对比正是这个维度存在的理由（同 stage|baseline_is_dirty）。
    """
    institution = issuing_body_of(record)
    title = str(record.get("title") or "")

    for table in (DEPARTMENT_INDUSTRY, PROVINCE_DEPT_INDUSTRY):
        for industry, markers in table:
            for m in markers:
                if m in institution:
                    return industry
    for industry, words in INDUSTRIES:
        for w in words:
            if w and w in title:
                return industry
    return "未分类"


def build(apply: bool) -> int:
    con = sqlite3.connect(str(DB), timeout=30)
    con.execute("PRAGMA busy_timeout = 30000")
    cols = {r[1] for r in con.execute("PRAGMA table_info(announcements)")}
    if "industry" not in cols:
        if not apply:
            print("industry 列不存在，先跑 --build")
            return 1
        con.execute("ALTER TABLE announcements ADD COLUMN industry TEXT")
        con.commit()
        print("已加 industry 列")

    rows = con.execute(
        "SELECT id, title, content, institution_name, issuing_body, category_l2 "
        "FROM announcements").fetchall()
    updates = []
    tally: collections.Counter = collections.Counter()
    for rid, title, content, inst, issuing, cat_l2 in rows:
        rec = {"title": title, "content": (content or "")[:4000],
               "institution_name": inst, "issuing_body": issuing}
        # 已有 category_l2 门类的直接沿用（那是人工沉淀的行业分类）
        known = cat_l2 if cat_l2 and cat_l2 not in ("未分类", "主管部门归类") else None
        if known and any(known == name for name, _ in INDUSTRIES):
            industry = known
        else:
            industry = classify(rec)
        tally[industry] += 1
        updates.append((industry, rid))
    print()
    print("行业分布（%d 条）:" % len(rows))
    for k, v in tally.most_common():
        print("  %-12s %5d  %5.1f%%" % (k, v, 100 * v / max(len(rows), 1)))
    unknown = tally.get("未分类", 0)
    print()
    print("未分类 %d 条 (%.1f%%) —— 这些不会进任何行业视图，"
          "但**宁缺勿滥**，硬猜会污染跨省对比" % (unknown, 100 * unknown / max(len(rows), 1)))

    if apply:
        con.executemany("UPDATE announcements SET industry=? WHERE id=?", updates)
        con.commit()
        print()
        print("已回填 %d 条" % len(updates))
    con.close()
    return 0


def report(limit: int = 20) -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    print("=== 行业 × 地区 交叉表（这是新维度解锁的分析）===")
    rows = con.execute(
        "SELECT coalesce(region_province, '未知') p, coalesce(industry,'未分类') i, "
        "COUNT(*) c FROM announcements WHERE industry IS NOT NULL "
        "AND industry <> '未分类' GROUP BY p, i ORDER BY c DESC").fetchall()
    per = collections.defaultdict(dict)
    for p, i, c in rows:
        per[i][p] = c
    print()
    print("%-12s %s  合计" % ("行业", "  ".join("%-8s" % p for p in
                                             sorted(per, key=lambda k: -sum(per[k].values()))[:6])))
    for i in sorted(per, key=lambda k: -sum(per[k].values()))[:limit]:
        top6 = sorted(per, key=lambda k: -sum(per[k].values()))[:6]
        print("%-12s %s  %5d" % (i, "  ".join("%-8d" % per[i].get(p, 0) for p in top6),
                                  sum(per[i].values())))
    con.close()
    return 0


def trend(industry: str, limit: int = 15) -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    print("=== 「%s」各省政策强度（条数 / 近1年 / 年份跨度）===" % industry)
    rows = con.execute(
        "SELECT coalesce(region_province,'未知') p, COUNT(*) c, "
        "SUM(CASE WHEN publish_date >= date('now','-1 year') THEN 1 ELSE 0 END) y1, "
        "MIN(substr(publish_date,1,4)) y0, MAX(substr(publish_date,1,4)) y1 "
        "FROM announcements WHERE industry=? GROUP BY p ORDER BY c DESC LIMIT ?",
        (industry, limit)).fetchall()
    for p, c, y1, y0, y1max in rows:
        print("  %-10s %4d 条  近1年 %3d  覆盖 %s-%s" % (p, c, y1, y0, y1max))
    con.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="行业维度分类")
    ap.add_argument("--build", action="store_true", help="加列并回填")
    ap.add_argument("--report", action="store_true", help="行业×地区交叉表")
    ap.add_argument("--trend", help="某行业的各省强度")
    args = ap.parse_args()
    if args.build:
        return build(True)
    if args.report:
        return report()
    if args.trend:
        return trend(args.trend)
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())