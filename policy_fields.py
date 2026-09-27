"""Policy field extraction — deterministic, rule-based.

Extracts the comparison schema fields from an announcement record. Priority is
always the structured column already on the row (highest confidence), falling
back to regex windows over the body text. No model inference: a field either
matches a rule or lands in the review queue.

Confidence bands:
    >= 80  auto     trusted, goes straight into the comparison table
    >= 50  pending  plausible but needs human confirmation
    <  50  pending with empty value (nothing usable was found)
"""
from __future__ import annotations

import re
from typing import Any

# 通用对比字段：field_key → 提取规则。value_column 指向 Announcement 已有列。
FIELD_SCHEMA: list[dict[str, str]] = [
    {"field_key": "subsidy_amount", "label": "补贴标准", "value_column": "amount"},
    {"field_key": "target_group", "label": "申报条件", "value_column": "target_group"},
    {"field_key": "deadline", "label": "申报截止", "value_column": "deadline"},
    {"field_key": "institution", "label": "责任机构", "value_column": "institution_name"},
    {"field_key": "region", "label": "所属地区", "value_column": "region_province"},
]

# 正文兜底正则：仅在结构化列缺失时使用，置信度低于列来源。
#
# 线索词表原先窄到只认「补贴|资助|奖励|补助|扶持」和「截止|截至|申报时间|申报期限」，
# 实测导致 amount 只在 10% 的记录上有值、deadline 只有 6%——而这些字段是摘要
# 里最该出现的东西（金额、截止）。政务公告实际用词远不止这些：
#   金额：采购预算、最高限价、控制价、预算金额、成交价、合同金额、售价、限价
#   截止：报名时间、公示时间、开标时间、开启时间、接收时间、申报时间、有效期
# 线索词放宽到政务公文里真实出现的形态，置信度保持不变（仍是兜底来源）。
BODY_PATTERNS: dict[str, tuple[re.Pattern[str], int]] = {
    "subsidy_amount": (
        re.compile(
            r"(?:补贴|资助|奖励|补助|扶持|采购预算|最高限价|预算金额|控制价|"
            r"拦标价|成交价|中标价|合同金额|预算|限价|售价|单价|总价|"
            r"资助标准|补助标准|奖补)[^。；\n]{0,40}?"
            r"(\d+(?:\.\d+)?\s*(?:万元|亿元|万|亿|元))"),
        65,
    ),
    # 表格里的金额：数字和单位之间可能隔着竖线
    "subsidy_amount_table": (
        re.compile(r"(?:采购预算|最高限价|预算金额|控制价)\s*[\|｜]?\s*"
                   r"(\d+(?:\.\d+)?\s*(?:万元|亿元|元))"),
        68,
    ),
    # 申报对象：只认**明确的字段标签**。
    # 原来还包含「面向」「适用于」「服务对象」，而"面向"在政务文本里太常见——
    # 「面向社会公开征集我省商标违法违规问题线索」是征集通知的正文，
    # 不是申报对象，于是把整句当成了 target_group。补抽抽样里这类占了多数。
    "target_group": (
        re.compile(r"(?:适用对象|补贴对象|申报条件|申报主体|申报范围|支持对象|"
                   r"资助对象|扶持对象|服务对象|保障对象|覆盖范围)"
                   r"[：:为]?\s*([^。；\n]{4,60})"),
        60,
    ),
    "deadline": (
        re.compile(
            r"(?:截止|截至|不迟于|最后|报名时间|报名日期|公示时间|公示期|"
            r"开标时间|开启时间|接收时间|申报时间|申报期限|提交时间|送达时间|"
            r"响应时间|有效期)[^。；\n]{0,20}?"
            # 只抓**日期本身**，不是线索词后面那一整段文本。
            # 原来抓"线索词 + 后面 4-40 字"，于是「公示时间：2026年9月13日至30日」
            # 还能用，但「2025年底，全国城市地下管网总长386万公里」也被当成截止
            # 日期——因为它确实含年月。补抽 437 个里就有这种，抽样可见
            # 「的部分行政规范性文件目录 国家能源局 2026年…附件1 附件2」。
            # 抓日期本身后，这些会退化成"2026年"这种短值，可读且可再加工。
            r"((?:20\d{2}\s*[-/年.]\s*)?\d{1,2}\s*[-/月.]\s*\d{1,2}\s*日?"
            r"(?:\s*[至到\-—]\s*(?:20\d{2}\s*[-/年.]\s*)?\d{1,2}\s*[-/月.]\s*\d{1,2}\s*日?)*"
            r"|20\d{2}\s*年(?:\d{1,2}\s*月)?|\d{1,2}\s*月\d{1,2}\s*日)"),
        55,
    ),
    "institution": (
        # 只抓机构名。原正则 `[：:]\s*([^。；\n，,]{3,30})` 会把后面的地址和电话
        # 一并吞进来，实测抽出过「四川重德招标有限责任公司 通讯地址：成都市高新区
        # 天府大道…」和纯电话「028-86702309」。
        #
        # 贪婪 + 机构后缀收尾：非贪婪会在"有限责任"里的"公司"之前停下（错的），
        # 而贪婪后跟"必须是机构名结尾"的断言才能既保住"有限责任公司"、又切掉
        # 后面的" 通讯地址：…"。纯电话没有机构后缀，断言不满足，自然被拒。
        re.compile(r"(?:联系单位|经办机构|咨询电话|采购人|代理机构|受理机构)"
                   r"[：:]\s*([^。；\n，,、]{3,30}?"
                   r"(?:有限责任公司|股份有限公司|有限公司|管理中心|服务局|管理局|"
                   r"委员会|办事处|服务中心|研究院|研究所|管理处|管委会|中心|银行|"
                   r"公司|局|厅|委|院|所|校|社)"
                   r"|[^。；\n，,、\s]{3,30}?)(?=[，,。、；;\s]|$)"),
        50,
    ),
}

AUTO_THRESHOLD = 80
PENDING_THRESHOLD = 50


def _patterns_for(field_key: str) -> list[tuple[str, tuple[re.Pattern[str], int]]]:
    """取一个 field_key 的全部候选正文规则。

    BODY_PATTERNS 的键是 field_key 或 field_key 的扩展（subsidy_amount_table）。
    候选按"与 field_key 完全相同优先、扩展在后"排序——表格形态的规则置信度
    更高但形态更窄，所以完全匹配的那条先试。
    """
    exact = BODY_PATTERNS.get(field_key)
    out: list[tuple[str, tuple[re.Pattern[str], int]]] = []
    if exact is not None:
        out.append((field_key, exact))
    prefix = field_key + "_"
    for table_key, pattern in BODY_PATTERNS.items():
        if table_key.startswith(prefix) and table_key != field_key:
            out.append((table_key, pattern))
    return out

COLUMN_CONFIDENCE = {
    "region": 95,
    "institution": 90,
    "subsidy_amount": 85,
    "target_group": 80,
    "deadline": 80,
    "key_measures": 75,
}


def _clean(text: Any) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    # 抽取结果不该把自己的线索词带出来。deadline 的值来自
    # 「(?:截止|报名时间|…)[：:为]?\s*(…)」，中文冒号是全角而正则里写了
    # [：:]，两者混排时会把「时间：2026年10月20日」整段当值——线索词重复
    # 一次，读起来像卡带。去掉开头残留的标签。
    value = re.sub(r"^(?:时间|日期|期限|截止|截至|起|至)\s*[:：]?\s*", "", value)
    return value.strip()


def _band(confidence: int) -> str:
    if confidence >= AUTO_THRESHOLD:
        return "auto"
    if confidence >= PENDING_THRESHOLD:
        return "pending"
    return "pending"


def extract_fields(record: dict[str, Any], field_schema: list[dict[str, str]] | None = None) -> list[dict[str, Any]]:
    """Extract schema fields from one announcement record (plain dict, no DB)."""
    schema = field_schema or FIELD_SCHEMA
    body = _clean(record.get("content"))[:600]
    results: list[dict[str, Any]] = []
    for spec in schema:
        key = spec["field_key"]
        column = spec.get("value_column", "")
        value = _clean(record.get(column)) if column else ""
        evidence = f"结构化字段 {column}"
        if key in COLUMN_CONFIDENCE:
            confidence = COLUMN_CONFIDENCE[key]
        else:
            confidence = 70
        if not value:
            # 一个 field_key 可能对应多条正文兜底规则：原先这里只取
            # BODY_PATTERNS.get(key) 的第一条，而我是按 field_key 建的表，
            # 加了 subsidy_amount_table 这样的补充键之后就永远查不到——
            # 死键。现在按前缀收集全部候选，逐个尝试直到命中。
            for table_key, pattern in _patterns_for(key):
                if not body:
                    break
                match = pattern[0].search(body)
                if match:
                    value = _clean(match.group(1))
                    confidence = pattern[1]
                    evidence = f"正文命中：…{body[max(0, match.start() - 10):match.end() + 10]}…"
                    break
        if not value:
            value = None
            confidence = 0
            evidence = "结构化列与正文均未命中"
        results.append(
            {
                "field_key": key,
                "label": spec.get("label", key),
                "value": value,
                "confidence": confidence,
                "evidence_text": evidence,
                "method": "rule",
                "status": _band(confidence) if value else "pending",
            }
        )
    return results


def key_measures_from(record: dict[str, Any]) -> dict[str, Any]:
    """Key points live in a JSON list column; returned separately by callers that need it."""
    points = record.get("key_points") or []
    joined = "；".join(_clean(p) for p in points if _clean(p))
    confidence = COLUMN_CONFIDENCE["key_measures"] if joined else 0
    return {
        "field_key": "key_measures",
        "label": "关键举措",
        "value": joined or None,
        "confidence": confidence,
        "evidence_text": "结构化字段 key_points" if joined else "无 key_points 数据",
        "method": "rule",
        "status": _band(confidence) if joined else "pending",
    }
