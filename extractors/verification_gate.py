"""Cross-check sourcing: which media columns qualify as verification sources.

AGENTS.md 1.6 allows mainstream and industry media as corroboration for a
policy fact, but restricts them to industry, livelihood and economic coverage --
political reporting is out of scope. A media item only becomes corroborating
evidence when it also carries a comparable fact (amount, deadline, applicant),
otherwise it is context and must not enter the comparison set.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

# Subject areas a verification source may cover. Deliberately excludes politics.
# ponytail: hand-maintained vocabulary, so an unlisted industry term gets rejected
# (沪昆铁路 集中修 was dropped for lacking 交通). The project taxonomy in
# config/categories.yaml cannot replace it -- it is recruitment-oriented and files
# 11/20 real media items under 新闻动态, 8 under 其他. Replace this list with the
# classifier once that taxonomy actually covers industry/livelihood.
ALLOWED_SECTORS = (
    "能源", "电力", "电网", "充电", "油气", "煤炭", "石化", "新能源", "光伏", "风电", "储能",
    "碳达峰", "碳中和", "减排",
    "工信", "制造", "产业", "供应链", "汽车", "机械", "钢铁", "化工", "建材",
    "发改", "经济", "金融", "银行", "证券", "保险", "财政", "税收", "投资", "营商",
    "农业", "粮食", "化肥", "农药", "种业", "农田", "高标准农田", "水利", "林业", "畜牧", "渔业",
    "住建", "建筑", "房地产", "公积金", "城管", "物业", "社区",
    "交通", "铁路", "公路", "水运", "航空", "地铁", "公交", "高速", "客运", "货运",
    "桥梁", "隧道", "养护", "车站", "航班", "物流", "港口", "民航",
    "教育", "高校", "职业教育", "招生", "人社", "就业", "招聘", "岗位", "职业技能", "失业",
    "社保", "医保", "医疗", "养老", "供暖", "供水", "燃气",
    "民政", "社会救助", "应急", "安全生产", "生态环境", "环保", "科技", "信息化",
    "人工智能", "大模型", "算力", "通信", "卫星", "半导体", "消费", "零售", "餐饮", "纺织",
)

# Political reporting: allowed for a general news feed, rejected as a
# verification source. The list errs toward rejection -- a false rejection only
# costs one item of corroboration, a false acceptance breaks the user's
# no-political-content rule.
POLITICAL_SECTORS = (
    "时政", "要闻", "时事", "政治", "党建", "党务", "纪检", "巡视", "干部", "人大",
    "政协", "外事", "外交", "国防", "军事", "台海", "两岸", "国际", "评论员",
    "社论", "任免", "悼念", "贺电", "唁电", "会谈", "接待", "出访", "会见",
    "国事访问", "总书记", "政治局", "党中央", "党组会", "党委", "纪委",
    "外交部", "贺信", "亲切", "接见", "出席并讲话",
)
# Named officeholders appear in political reporting far more often than in
# industry news, and the sector list alone will not catch "习近平会见外宾".
POLITICAL_PEOPLE = (
    "习近平", "李强", "赵乐际", "王沪宁", "蔡奇", "丁薛祥", "李希",
)

FACT_PATTERNS = {
    "amount": r"\d+(?:\.\d+)?\s*(?:万元|亿元|万|亿|元|美元)",
    "deadline": r"(?:截止|截至|不迟于|自|于)\s*\d{4}年\d{1,2}月\d{1,2}日|\d{4}年\d{1,2}月\d{1,2}日前",
    "applicant": r"(?:申报主体|支持对象|适用对象|面向|由.{2,20}(?:负责|承担|组织))",
    "scope": r"(?:试点|覆盖|范围|地区|市|省|县|乡镇)",
    "program": r"《[^》]{4,45}》|实施方案|行动计划|专项|规划",
}

VERIFY_MARK = "verified"
PENDING_MARK = "needs_review"


def classify_for_verification(
    record: Mapping[str, Any], require_fact: bool = True
) -> dict[str, Any]:
    """Decide whether a media item may corroborate a policy fact.

    Returns a verdict with the reason, so a rejection is auditable rather than a
    silent drop. `require_fact` enforces the rule that corroboration needs a
    comparable fact, not just the same subject.
    """
    text = " ".join(
        str(record.get(field) or "")
        for field in ("title", "summary", "content", "issuing_body")
    )
    normalized = re.sub(r"\s+", "", text)
    reasons: list[str] = []

    if any(word in normalized for word in POLITICAL_SECTORS) or any(
        person in normalized for person in POLITICAL_PEOPLE
    ):
        reasons.append("政治类内容，按 §1.6 不作为印证来源")
        return {"usable": False, "mark": None, "reasons": reasons, "facts": {}}

    sectors = [word for word in ALLOWED_SECTORS if word in normalized]
    if not sectors:
        reasons.append("未命中行业/民生/经济领域")
        return {"usable": False, "mark": None, "reasons": reasons, "facts": {}}

    facts = {
        name: bool(re.search(pattern, text))
        for name, pattern in FACT_PATTERNS.items()
    }
    present = [name for name, hit in facts.items() if hit]
    if require_fact and not present:
        reasons.append("无可比对事实（金额/期限/对象/范围/专项），仅为背景报道")
        return {"usable": False, "mark": None, "reasons": reasons, "facts": facts}

    reasons.append(f"命中领域: {'、'.join(sectors[:5])}")
    if present:
        reasons.append(f"可比事实: {'、'.join(present)}")
    return {
        "usable": True,
        "mark": VERIFY_MARK if len(present) >= 2 else PENDING_MARK,
        "reasons": reasons,
        "facts": facts,
        "sectors": sectors,
    }


def partition_for_verification(
    records: list[Mapping[str, Any]], require_fact: bool = True
) -> dict[str, Any]:
    """Split a batch into corroborating items and rejected ones."""
    accepted, rejected = [], []
    for record in records:
        verdict = classify_for_verification(record, require_fact=require_fact)
        if verdict["usable"]:
            accepted.append({"record": record, "verdict": verdict})
        else:
            rejected.append({"record": record, "verdict": verdict})
    reason_counts: dict[str, int] = {}
    for item in rejected:
        key = item["verdict"]["reasons"][0] if item["verdict"]["reasons"] else "其他"
        reason_counts[key] = reason_counts.get(key, 0) + 1
    return {
        "total": len(records),
        "accepted": accepted,
        "rejected": rejected,
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "verified_count": sum(1 for a in accepted if a["verdict"]["mark"] == VERIFY_MARK),
        "rejection_reasons": reason_counts,
    }
