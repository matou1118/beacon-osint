"""政府工作报告指标的缺口补齐与交叉验证。

为什么不用搜索引擎补数
  抓取搜索结果违反其服务条款；结果不是可引用的一手来源；同样���问题今天和
  明天可能给不同答案，数字不可复现。缺口该用**统计公报**补——统计局每年
  发布一次的《国民经济和社会发展统计公报》是一手权威来源，逐年结构稳定，
  URL 可枚举，robots 与限速规则和报告采集完全一致。

核心是信任模型，不是补数本身
  报告原文抽出的值有原文出处；公报补出的值有一手来源。两者证据强度不同，
  所以 provenance 是一等字段而不是备注。同一 (地区, 年份, 指标) 出现两个
  不同值时**不自动取舍**——各年度公报与次年报告里的上年回顾本来就常有
  修订差异，自动挑一个会把修订痕迹抹掉。冲突交�� resolve_conflicts() 报出
  来，由人决定。

缺失不补零
  找不到就是找不到，存 None。存 0 会让"未披露"在折线图上变成一个假谷底。
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "beacon.db"

PROVENANCE_REPORT = "report_text"
PROVENANCE_COMMUNIQUE = "statistical_communique"
PROVENANCE_MANUAL = "manual"

PROVENANCE_LABEL = {
    PROVENANCE_REPORT: "报告原文",
    PROVENANCE_COMMUNIQUE: "统计公报",
    PROVENANCE_MANUAL: "人工核定",
}

# 同一指标两个来源的相对差异超过这个比例就报为冲突。
# 统计公报与次年报告的"上年回顾"之间存在修订，5% 是个经验阈值：
# 低于它多半是四舍五入，高于它通常是真口径差异。
CONFLICT_TOLERANCE = 0.05


# ── 统计公报解析 ──────────────────────────────────────────
# 公报的指标表述比政府工作报告更规整，因为它就是给对标用的。
_COMMUNIQUE_NUMBER = rf"(?P<value>-?[0-9]+(?:\.[0-9]+)?)\s*(?P<unit>万亿元|亿元|万元|万人次|万人|万|亿|元|人|个|所|家|%)"
# 连接词允许连续出现最多两个。公文与公报里都有"平均值为""分别达到"这类
# 两段式表述，单 token 的可选组匹配不上：
#   "全年城镇调查失业率平均值为4.2%"  ← "平均" + "为"
_COMMUNIQUE_LINK = (r"(?:达到|完成|实现|达|为|是|平均|平均为|平均值为|分别|约|约为|近|超过|超出|"
                    r"超|逾|在|总计|累计|增长至|增至|增加到|控制在|保持在|降至|低于|不超过|预计){0,2}\s*")

COMMUNIQUE_METRICS: dict[str, dict[str, Any]] = {
    "gdp": {
        "name": "地区生产总值", "caliber": "地区生产总值",
        "patterns": [
            r"(?<!人均)(?:全市|全省)?(?:地区)?生产总值(?:总量)?\s*" + _COMMUNIQUE_LINK + _COMMUNIQUE_NUMBER,
        ],
    },
    "gdp_growth": {
        "name": "地区生产总值增速", "caliber": "地区生产总值(可比价)",
        "patterns": [
            r"(?:全市|全省)?(?:地区)?生产总值(?:同比)?(?:增长|增速|增幅)\s*"
            + _COMMUNIQUE_LINK + _COMMUNIQUE_NUMBER,
        ],
    },
    "population": {
        "name": "常住人口", "caliber": "常住人口",
        "patterns": [
            r"常住人口(?:总)?(?:达到|达)?\s*" + _COMMUNIQUE_LINK + _COMMUNIQUE_NUMBER,
        ],
    },
    "urban_surveyed_unemployment": {
        "name": "城镇调查失业率", "caliber": "城镇调查失业率",
        "patterns": [
            r"城镇调查失业率\s*" + _COMMUNIQUE_LINK + _COMMUNIQUE_NUMBER,
        ],
    },
    "new_urban_jobs": {
        "name": "城镇新增就业", "caliber": "城镇新增就业",
        "patterns": [
            r"城镇新增就业(?:人员)?\s*" + _COMMUNIQUE_LINK + _COMMUNIQUE_NUMBER,
        ],
    },
    "fiscal_revenue": {
        "name": "地方一般公共预算收入", "caliber": "地方一般公共预算收入",
        "patterns": [
            r"(?:地方)?一般公共预算收入\s*" + _COMMUNIQUE_LINK + _COMMUNIQUE_NUMBER,
        ],
    },
    "fiscal_expenditure": {
        "name": "地方一般公共预算支出", "caliber": "地方一般公共预算支出",
        "patterns": [
            r"(?:地方)?一般公共预算支出\s*" + _COMMUNIQUE_LINK + _COMMUNIQUE_NUMBER,
        ],
    },
    "cpi": {
        "name": "居民消费价格涨幅", "caliber": "居民消费价格(上年=100 的涨幅)",
        "patterns": [
            r"居民消费价格(?:总水平)?(?:比上年)?(?:上涨|涨幅|增长|下降)\s*"
            + _COMMUNIQUE_LINK + _COMMUNIQUE_NUMBER,
        ],
    },
    "per_capita_income": {
        "name": "居民人均可支配收入", "caliber": "全体居民人均可支配收入",
        "patterns": [
            r"(?:全体)?(?:居民)?人均可支配收入\s*" + _COMMUNIQUE_LINK + _COMMUNIQUE_NUMBER,
        ],
    },
    "per_capita_gdp": {
        "name": "人均地区生产总值", "caliber": "人均地区生产总值(按可比价)",
        "patterns": [
            r"人均(?:地区)?生产总值\s*" + _COMMUNIQUE_LINK + _COMMUNIQUE_NUMBER,
        ],
    },
}

# 只需从正文里排除的年份形态：1950-2049 的裸年份绝不能当指标值
# 句中出现这些就不是"本年值"：累计、多年加总、未来目标。理由同报告侧
# （见 extractors/work_report_metrics.py 的 _NOT_ANNUAL 注释）——五年累计
# 混进年度序列会造出假数据。
_NOT_ANNUAL = re.compile(
    r"累计|五年|三年|近[三五七八十\d]+年|过去[三五七八九十\d]+年|年均|"
    r"(?:到|预计到|力争到)\s*20\d{2}\s*年|上年同期|"
    r"“?[一二三四五]?[十]?[五]?五”?规划|上个?五年|今后[三五]?年|未来[三五]?年"
)

# 公报侧同样收紧口径。广州 2022 那份同时写「来源于广州地区的一般公共预算
# 收入」与「地方一般公共预算收入」，前者是全地区口径、后者是市本级，
# 两者差 3.7 倍，宽松匹配会把两个口径当成同一指标。
COMMUNIQUE_METRICS["fiscal_revenue"]["patterns"] = [
    r"(?:地方|本级|市本级)(?:一般公共预算|一般预算)收入\s*" + _COMMUNIQUE_LINK + _COMMUNIQUE_NUMBER,
]
COMMUNIQUE_METRICS["fiscal_expenditure"]["patterns"] = [
    r"(?:地方|本级|市本级)(?:一般公共预算|一般预算)支出\s*" + _COMMUNIQUE_LINK + _COMMUNIQUE_NUMBER,
]

# 裸年份绝不能当指标值：1950-2049 的数字是年份，不是指标。
_YEAR_ONLY = re.compile(r"^(?:19|20)\d{2}$")


def parse_communique(text: str, region_code: str, year: int, publisher: str, url: str) -> list[dict[str, Any]]:
    """从一份统计公报正文抽指标，打上 statistical_communique 来源。"""
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for key, spec in COMMUNIQUE_METRICS.items():
        for pattern in spec["patterns"]:
            for match in re.finditer(pattern, text):
                raw = match.group("value")
                unit = (match.groupdict().get("unit") or "").strip()
                if _YEAR_ONLY.match(raw):
                    continue
                # 与报告侧同一处坑：(?<!人均) 挡不住，因为 (?:地区)? 可选，
                # 正则会从"生"字处起匹配，那里前两字是"地区"。广州 2024 公报
                # 的「人均地区生产总值达164171元」因此被记成 gdp 总量。
                if "人均" in text[max(0, match.start() - 4):match.start()]:
                    continue
                try:
                    value = float(raw)
                except ValueError:
                    continue
                if value <= 0:
                    continue
                # 同一指标同一单位只取第一次，避开正文里反复引述同一数字
                if (key, unit) in seen:
                    continue
                seen.add((key, unit))
                start = text.rfind("。", 0, match.start()) + 1
                end = text.find("。", match.end())
                sentence = text[start: end if end > 0 else match.end() + 20].strip()
                if _NOT_ANNUAL.search(sentence):
                    continue
                out.append({
                    "region_code": region_code,
                    "year": year,
                    "topic": "经济运行",
                    "metric_key": key,
                    "metric_name": spec["name"],
                    "value": value,
                    "unit": unit or None,
                    "growth_value": None,
                    "growth_unit": None,
                    # 公报里的数字都是已实现值，不存在"目标"这一说
                    "is_target": False,
                    "caliber": spec["caliber"],
                    "section": "统计公报",
                    "evidence_text": (sentence or match.group(0))[:400],
                    "provenance": PROVENANCE_COMMUNIQUE,
                    "source_url": url,
                    "source_publisher": publisher,
                    "extract_version": "communique-v1",
                })
            if any(item["metric_key"] == key for item in out):
                break
    return out


# ── 交叉验证 ─────────────────────────────────────────────
# 单位归一。抽取层刻意不做量级归一（原文"64.1万人"就存 64.1 + 万人，
# 见 extractors/work_report_metrics.py 的说明），所以同一个量在不同来源里
# 单位不同：上海报告写"5.67万亿元"、统计公报写"56708.71亿元"，
# 上海报告写"8.8万元"、公报写"88366元"。不归一就比，这些会被报成 100% 的
# 冲突——那是单位表述差异，不是数据矛盾。
_UNIT_SCALE = {
    "万亿元": 1e12, "亿元": 1e8, "万元": 1e4, "元": 1.0,
    "亿美元": 1e8, "万美元": 1e4,
    "亿人": 1e8, "万人": 1e4, "万人次": 1e4, "人": 1.0,
    "万": 1e4, "亿": 1e8, "%": 1.0,
    "个": 1.0, "名": 1.0, "所": 1.0, "家": 1.0, "张": 1.0, "床位": 1.0,
}


def _normalized(value: float, unit: str | None) -> float:
    return float(value) * _UNIT_SCALE.get(unit or "", 1.0)


def _same_value(a: float, b: float) -> bool:
    scale = max(abs(a), abs(b), 1.0)
    return abs(a - b) / scale <= CONFLICT_TOLERANCE


def resolve_conflicts(db_path: Path | str = DB_PATH) -> list[dict[str, Any]]:
    """找出同一 (地区, 年份, 指标) 有实质差异的情况。

    先按单位归一再比。抽取层刻意保留原文单位，所以"5.67万亿元"与
    "56708.71亿元"、"8.8万元"与"88366元"是同一个量的不同表述，直接比会报成
    100% 冲突，那是噪声不是矛盾。

    归一后仍有差异的**不自动取舍**：统计公报发布后，次年政府工作报告的
    "上年回顾"有时会给出修订后的数字，这本身是有价值的信息，抹掉就看不出
    修订发生过。
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = list(con.execute("""
            SELECT m.region_code, r.region_name, m.year, m.metric_key, m.metric_name
            FROM work_report_metrics m
            JOIN work_reports r ON r.id = m.report_id
            WHERE m.is_target = 0
            GROUP BY m.region_code, m.year, m.metric_key
            ORDER BY m.region_code, m.year, m.metric_key
        """))
    except sqlite3.Error:
        return []
    finally:
        con.close()

    out: list[dict[str, Any]] = []
    for row in rows:
        con2 = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con2.row_factory = sqlite3.Row
        try:
            values = list(con2.execute("""
                SELECT DISTINCT m.value, m.unit, m.provenance, m.caliber, m.source_url,
                       m.section, m.evidence_text, m.is_target
                FROM work_report_metrics m JOIN work_reports r ON r.id = m.report_id
                WHERE m.region_code=? AND m.year=? AND m.metric_key=? AND m.is_target=0
            """, (row["region_code"], row["year"], row["metric_key"])))
        finally:
            con2.close()
        numbers = [_normalized(v["value"], v["unit"]) for v in values]
        # 归一后仍然不同才算冲突。同一个量的不同单位表述（5.67万亿元 vs
        # 56708.71亿元）不该被报成冲突。
        if len(values) < 2 or all(_same_value(numbers[0], other) for other in numbers[1:]):
            continue
        spread = (max(numbers) - min(numbers)) / max(abs(max(numbers)), abs(min(numbers)), 1.0)
        out.append({
            "region": row["region_name"],
            "year": row["year"],
            "metric": row["metric_name"],
            "metric_key": row["metric_key"],
            "values": [
                {
                    "value": v["value"],
                    "unit": v["unit"],
                    # 归一后的值，方便直接比大小
                    "normalized": _normalized(v["value"], v["unit"]),
                    "provenance": v["provenance"],
                    "provenance_label": PROVENANCE_LABEL.get(v["provenance"], v["provenance"]),
                    "caliber": v["caliber"],
                    "source_url": v["source_url"],
                    "section": v["section"],
                    # 原文出处。冲突不看原文就无法判断是修订、还是误匹配。
                    "evidence_text": v["evidence_text"],
                }
                for v in values
            ],
            "spread_pct": round(spread * 100, 1),
        })
    return out


def coverage_by_provenance(db_path: Path | str = DB_PATH) -> dict[str, Any]:
    """按来源统计每个 (地区, 年份, 指标) 是否齐备——缺口补齐的进度看板。"""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        by_prov = {
            row[0]: row[1]
            for row in con.execute(
                "SELECT provenance, COUNT(*) FROM work_report_metrics GROUP BY provenance")
        }
        by_key = list(con.execute("""
            SELECT m.metric_key, m.metric_name,
                   COUNT(DISTINCT m.region_code || '-' || m.year) AS cells,
                   SUM(CASE WHEN m.provenance='statistical_communique' THEN 1 ELSE 0 END) AS filled
            FROM work_report_metrics m WHERE m.is_target=0
            GROUP BY m.metric_key, m.metric_name ORDER BY cells DESC
        """))
    except sqlite3.Error:
        return {}
    finally:
        con.close()
    return {
        "by_provenance": {PROVENANCE_LABEL.get(k, k): v for k, v in by_prov.items()},
        "by_metric": [
            {"metric_key": r[0], "metric_name": r[1], "cells": r[2], "from_communique": r[3]}
            for r in by_key
        ],
    }
