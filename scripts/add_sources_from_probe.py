"""把 `propose_source_config.py` 的提案落成 sources.yaml 里的可采集源。

为什么要脚本而不是手改
----------------------
一个源条目约 35 个字段（auth_scope / jurisdiction_code / retention /
playwright_* …），4 个源 × 35 个字段手打必然打错，而且错在 YAML 里要等
采集跑到那一个源才暴露。这里只做**追加**：不动 145 个既有条目的任何一行
（AGENTS.md「保住 145 个人工登记字段零丢失」），追加前先备份。

用法
    python scripts/add_sources_from_probe.py            # 预览
    python scripts/add_sources_from_probe.py --apply    # 追加落盘

选择器/详情正则来自 `data/candidates/<id>.json`（propose 的输出），本脚本
不重新探测——提案已经带了置信度，那是人工决定要不要收的地方。
"""
from __future__ import annotations

import argparse
import datetime
import shutil
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "config" / "sources.yaml"
CAND = ROOT / "data" / "candidates"

STAMP = "2026-10-06"

#: 要新增的源。只填**源本身的事实**（名字/区划/域名），选择器从提案取。
#: 复用时加一行即可；别在这里塞格式相关的东西。
SPEC: list[dict[str, str]] = [
    {"id": "xa_gov", "name": "西安市人民政府", "province": "陕西省",
     "city": "西安市", "code": "610000", "domain": "xa.gov.cn"},
    {"id": "ly_gov", "name": "洛阳市人民政府", "province": "河南省",
     "city": "洛阳市", "code": "410000", "domain": "ly.gov.cn"},
    {"id": "wh_gov", "name": "武汉市人民政府", "province": "湖北省",
     "city": "武汉市", "code": "420000", "domain": "wuhan.gov.cn"},
    {"id": "nb_gov", "name": "宁波市人民政府", "province": "浙江省",
     "city": "宁波市", "code": "330000", "domain": "ningbo.gov.cn"},
]

FUNCTIONS = ["市政府", "政务信息", "政策文件"]


def build(spec: dict[str, str]) -> dict[str, Any]:
    prop = yaml.safe_load((CAND / f"{spec['id']}.json").read_text(encoding="utf-8"))
    base = f"https://{spec['domain']}/"
    if prop.get("list_selector") is None:
        raise SystemExit(f"{spec['id']}: 提案没有 list_selector，不能加（会采到全站导航）")
    url = prop.get("url") or base
    detail_ratio = float(prop.get("detail_ratio") or 0)
    doc_score = float(prop.get("doc_score") or 0)
    if detail_ratio < 0.3 or doc_score < 0.1:
        # 门槛取自这次 8 个候选的实测分布：低分那几个（zz_gov 详情占比 0、
        # sz_gov 0.097）抽出来的是栏目导航，加进来只会烧回访预算。
        raise SystemExit(f"{spec['id']}: detail_ratio={detail_ratio} / "
                         f"doc_score={doc_score} 低于门槛，不收")
    return {
        "id": spec["id"],
        "name": spec["name"],
        "level": "municipal",
        "gov_level": "市级",
        "type": "archive",
        "source_type": "government",
        "access_method": "static_html",
        "auth_type": "none",
        "auth_scope": "栏目列表页与公告详情页（只读抓取，无登录无提交）",
        "domain": spec["domain"],
        "url": url,
        "base_url": base,
        "region_province": spec["province"],
        "region_city": spec["city"],
        "jurisdiction_code": spec["code"],
        "functions": FUNCTIONS,
        "cadence_tier": "daily",
        "cadence_seconds": 86400,
        "priority": "P1",
        "focus_level": "standard",
        "retention_class": "active",
        "retention_days": 365,
        "transient_retention_days": 30,
        "status": "enabled",
        "enabled": True,
        "check_robots": True,
        "robots_checked_at": None,
        "terms_checked_at": None,
        "owner": "项目负责人",
        "list_selector": prop["list_selector"],
        "link_selector": prop.get("link_selector") or "a[href]",
        "detail_pattern": prop.get("detail_pattern"),
        "max_items": 120,
        # 首页标签容器，没有分页可翻；翻 3 页只会拿重复或 404。
        "archive_pages": 1,
        "archive_depth_reason": "首页标签容器，单页取满即止（提案无分页形态）",
        "render_mode": "static",
        "playwright_fallback": True,
        "playwright_wait_until": "networkidle",
        "playwright_wait_ms": 5000,
        "playwright_timeout_ms": 60000,
        "notes": (f"{STAMP} 扩源探测新增（propose_source_config 反推）："
                  f"详情链接占比 {detail_ratio:.2f}、正文分 {doc_score:.2f}、"
                  f"容器 {prop['list_selector']}。terms_checked_at 待人工复核。"),
        "probe_verified_at": STAMP,
        "enabled_at": STAMP,
        "enabled_by": "agent: 扩源提案自动落盘，待项目负责人复核 auth_scope 与条款",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="追加到 sources.yaml")
    args = ap.parse_args()

    raw = SOURCES.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    existing = {str(s.get("id")) for s in data["sources"]}

    blocks, new_entries = [], []
    for spec in SPEC:
        if spec["id"] in existing:
            print("跳过 %s：sources.yaml 里已有" % spec["id"])
            continue
        entry = build(spec)
        new_entries.append(entry)
        blocks.append(yaml.dump([entry], allow_unicode=True,
                                default_flow_style=False, sort_keys=False).rstrip())
        print("待增 %s  %-12s %s/%s  容器=%s" % (
            spec["id"], entry["name"], entry["region_province"],
            entry["region_city"], entry["list_selector"]))

    if not new_entries:
        print("没有新源要加。")
        return 0
    if not args.apply:
        print("\n（预览，未写入。加 --apply 落盘）")
        return 0

    backup = SOURCES.with_suffix(f".yaml.bak-add-{datetime.datetime.now():%Y%m%dT%H%M%S}")
    shutil.copy2(SOURCES, backup)
    with SOURCES.open("a", encoding="utf-8") as fh:
        fh.write("\n" + "\n".join(blocks) + "\n")

    # 落盘后立刻回读校验：YAML 语法错、字段丢失、范围越界都要在这里炸，
    # 不能等采集跑到那个源才发现。
    after = yaml.safe_load(SOURCES.read_text(encoding="utf-8"))["sources"]
    ids = [str(s.get("id")) for s in after]
    assert len(ids) == len(set(ids)), "重复 id！"
    assert len(after) == len(data["sources"]) + len(new_entries), "条目数不对"
    assert len(after) >= 145, "既有条目被吃了"
    print("\n已备份 %s，追加 %d 个源（%d -> %d）" % (
        backup.name, len(new_entries), len(data["sources"]), len(after)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())