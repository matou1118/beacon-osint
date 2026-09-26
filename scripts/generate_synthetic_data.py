"""Synthetic announcement generator for pipeline testing.

Deliberately deterministic (seeded) and rule-derived: every title is built from
the same keywords config/category_rules.yaml uses, so the category rules are
expected to recover the intended label. That makes precision measurable --
a generator that invented unrelated titles would only prove the classifier is
confused.

Output goes to a separate DB (default data/synthetic.db) so production data
stays clean. Nothing here touches the network.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

# Callers read this program's stdout as JSON (tests/test_synthetic_data.py).
# On Windows the child encodes stdout with the console codepage, so Chinese
# titles came out as GBK and json.loads() failed on the caller's UTF-8 decode.
# Pin stdout to UTF-8 so the payload is readable from any parent process.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):  # pragma: no cover - replaced stream
        pass

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "config" / "category_rules.yaml"
CATEGORIES = ROOT / "config" / "categories.yaml"
DEFAULT_OUT = ROOT / "data" / "synthetic.db"

PROVINCES = (
    ("北京市", "北京市", None, "110000"),
    ("上海市", "上海市", None, "310000"),
    ("广东省", "广州市", "越秀区", "440104"),
    ("江苏省", "南京市", "鼓楼区", "320106"),
    ("浙江省", "杭州市", "西湖区", "330106"),
    ("山东省", "济南市", "历下区", "370102"),
    ("河南省", "郑州市", "金水区", "410105"),
    ("四川省", "成都市", "锦江区", "510104"),
    ("湖北省", "武汉市", "武昌区", "420106"),
    ("湖南省", "长沙市", "岳麓区", "430104"),
    ("陕西省", "西安市", "雁塔区", "610113"),
    ("辽宁省", "沈阳市", "和平区", "210102"),
)

LEVELS = (
    ("国家部委", "national", None),
    ("省级", "province", None),
    ("市级", "city", None),
    ("县级", "county", None),
)

NATIONAL_BODIES = (
    "国家发展和改革委员会", "财政部", "人力资源和社会保障部", "教育部",
    "住房和城乡建设部", "农业农村部", "工业和信息化部", "市场监督管理总局",
)

BODIES = {
    "province": "省{prov_short}人民政府", "city": "市{prov_short}市人民政府",
    "county": "区人民政府", "national": "",
}

INDUSTRIES = {
    "新能源": ["新能源", "光伏", "风电", "储能", "动力电池"],
    "人工智能": ["人工智能", "大模型", "算力", "智能制造"],
    "高技术制造": ["高端装备", "工业母机", "专精特新"],
    "数字经济": ["数字经济", "5G", "工业互联网", "数据要素"],
    "职业教育": ["职业教育", "产教融合", "技能人才"],
}

AMOUNT_UNITS = ("万元", "亿元", "元")
GROUPS = ("企业", "中小企业", "高新技术企业", "个体工商户", "社会组织", "青年人才", "农村经营主体")


def load_rules() -> dict[str, Any]:
    with RULES.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def province_short(name: str) -> str:
    return name.removesuffix("省").removesuffix("市")


def build_templates(rules: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(category_l1, category_l2, keyword) from the rule table.

    The keyword always belongs to the subcategory being labelled, so the stored
    label and the title always agree and accuracy measurement stays valid.
    """
    return [
        (str(category), str(subcategory), str(keyword))
        for category, spec in (rules.get("categories") or {}).items()
        for subcategory, keywords in ((spec or {}).get("subcategories") or {}).items()
        for keyword in keywords or []
    ]


def make_title(rng: random.Random, category: str, subcategory: str, keyword: str, region: str, issuer: str) -> str:
    forms = {
        "招商": [f"关于{region}开展{keyword}合作的通知", f"{region}{keyword}项目招商公告"],
        "行业政策": [f"关于印发{keyword}实施方案的通知", f"{region}{keyword}工作指导意见"],
        "行业补贴": [f"关于开展{keyword}申报工作的通知", f"{region}{keyword}资金申报指南"],
        "公招信息": [f"{issuer}关于{keyword}人员招录的公告", f"{region}{keyword}人员公开招录公告"],
        "招标信息": [f"{region}{keyword}项目竞争性磋商公告", f"关于{region}{keyword}采购的公告"],
        "商业机会": [f"关于{region}{keyword}项目的通知", f"{region}{keyword}信息公告"],
        "招聘信息": [f"{issuer}关于{keyword}的公告", f"{region}{keyword}岗位招聘公告"],
        "活动通知": [f"关于举办{keyword}活动的通知", f"{region}{keyword}报名通知"],
        "人才政策": [f"关于加强{keyword}工作的实施意见", f"{region}{keyword}实施办法"],
        "养老与社区": [f"关于{region}{keyword}服务工作的通知", f"关于开展{keyword}试点的通知"],
        "招生信息": [f"{region}{keyword}招生工作通知", f"关于{keyword}招生的公告"],
    }
    return rng.choice(forms.get(category, [f"关于{region}{keyword}的通知"]))


def make_body(rng: random.Random, title: str, industry: str, tags: list[str], region: str) -> str:
    group = rng.choice(GROUPS)
    amount = rng.choice((50, 100, 300, 500, 1000, 2000))
    unit = rng.choice(AMOUNT_UNITS)
    deadline = date.today() + timedelta(days=rng.randint(10, 60))
    sentences = [
        f"为贯彻落实相关工作部署，现将有关事项通知如下。",
        f"本次{industry}领域受理主体为{group}，受理截止时间为{deadline.isoformat()}。",
        f"支持标准为{amount}{unit}，具体以相关指南为准。",
        f"重点支持方向包括{'、'.join(tags)}等方向。",
        f"受理单位为{region}相关主管部门，联系电话 028-80000000。",
    ]
    return "".join(sentences)


SCHEMA = """
CREATE TABLE IF NOT EXISTS announcements (
    id INTEGER PRIMARY KEY, source_id TEXT, source_url TEXT, title TEXT,
    publish_date TEXT, content TEXT, content_hash TEXT, gov_level TEXT,
    jurisdiction_code TEXT, region_province TEXT, region_city TEXT,
    region_county TEXT, category_l1 TEXT, category_l2 TEXT, amount TEXT,
    deadline TEXT, target_group TEXT, target_industry TEXT, key_points TEXT,
    needs_review INTEGER, is_synthetic INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_syn_l1 ON announcements(category_l1);
CREATE INDEX IF NOT EXISTS ix_syn_l2 ON announcements(category_l2);
CREATE INDEX IF NOT EXISTS ix_syn_hash ON announcements(content_hash);
"""


def generate(count: int, seed: int, out_db: Path) -> dict[str, Any]:
    rng = random.Random(seed)
    rules = load_rules()
    with CATEGORIES.open(encoding="utf-8") as handle:
        all_categories = list((yaml.safe_load(handle) or {}).get("l1") or [])
    templates = build_templates(rules)
    industries = list(INDUSTRIES.items())
    rows: list[tuple] = []
    seen_hashes: dict[str, int] = {}
    duplicates = 0
    # Walk the template list directly so every subcategory is covered before
    # any repeats. Stride-based indexing skipped entries at small counts, which
    # silently left whole subcategories unexercised.
    levels_total = len(LEVELS)
    provinces_total = len(PROVINCES)
    templates_total = len(templates)
    industries_total = len(industries)
    national_index = 0

    for index in range(count):
        if index % 3 == 0:
            # One record in three is national so regional levels stay the majority.
            province, city, county, code = PROVINCES[(index // 3) % provinces_total]
            level, region_key, _ = LEVELS[0]
        else:
            national_index += 1
            level, region_key, _ = LEVELS[1 + (national_index % (levels_total - 1))]
            province, city, county, code = PROVINCES[(index // 3) % provinces_total]
        category, subcategory, keyword = templates[index % templates_total]
        industry, tags = industries[(index // templates_total + index) % industries_total]
        if level == "国家部委":
            issuer = NATIONAL_BODIES[index % len(NATIONAL_BODIES)]
            region = "全国"
            row_province = row_city = row_county = None
            row_code = "100000"
        else:
            issuer = BODIES[region_key].format(prov_short=province_short(province))
            region = county or city or province
            row_province, row_city, row_county = province, city, county
            row_code = code
        title = make_title(rng, category, subcategory, keyword, region, issuer)
        body = make_body(rng, title, industry, tags, region)
        digest = hashlib.sha256(f"{title}|{body}".encode("utf-8")).hexdigest()
        if digest in seen_hashes:
            duplicates += 1
        seen_hashes[digest] = seen_hashes.get(digest, 0) + 1
        points = [part for part in body.split("。") if len(part) >= 10][:3]
        rows.append((
            f"syn_{index:06d}", f"https://synthetic.invalid/{index:06d}", title,
            (date.today() - timedelta(days=index % 365)).isoformat(), body, digest,
            level, row_code, row_province, row_city, row_county,
            category, subcategory, None, None, rng.choice(GROUPS), industry,
            json.dumps(points, ensure_ascii=False), 0,
        ))

    out_db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(out_db)
    con.executescript(SCHEMA)
    con.executemany(
        "INSERT OR REPLACE INTO announcements (source_id,source_url,title,publish_date,content,"
        "content_hash,gov_level,jurisdiction_code,region_province,region_city,region_county,"
        "category_l1,category_l2,amount,deadline,target_group,target_industry,key_points,needs_review) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    con.commit()
    l1 = {name: sum(1 for row in rows if row[11] == name) for name in all_categories}
    l2 = {row[12] for row in rows}
    return {
        "db": str(out_db),
        "records": len(rows),
        "unique_hashes": len(seen_hashes),
        "duplicate_hashes": duplicates,
        "categories_l1_covered": len([k for k, v in l1.items() if v]),
        "categories_l1_total": len(all_categories),
        "categories_l2_covered": len(l2),
        "levels": sorted({row[6] for row in rows}),
        "dates_span": f"{min(r[3] for r in rows)}..{max(r[3] for r in rows)}",
        "l1_counts": {k: v for k, v in l1.items() if v},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=20260925)
    parser.add_argument("--db", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    print(json.dumps(generate(args.count, args.seed, args.db), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
