"""病因 → 证据 → 处方：把诊断结论变成可执行清单。

借鉴 Easel 的 ``skill-account-diagnosis``，它输出的是「病因 → 证据 → 处方方式」
这个结构——比「状态 + 建议」有用，因为每一条都能回答两个问题：
**凭什么这么说**（证据）、**具体做什么**（处方）。

BeaconOSINT 现有的 ``source_doctor.py`` 已经有 ``status/reason/hint/evidence``
四个字段，但那是**逐源**的诊断，回答「这个源怎么了」。这里补两个它没有的东西：

1. **处方要能排序**：按「有多少源同款病因」排，先修影响面最大的那个。
   逐源诊断会告诉你 7 个源都不出货，但你不知道该先修哪个。
2. **病因归并**：同样的病因在多个源上重复出现时，那不是7 个独立问题，
   而是**1 个问题表现为 7 处**——修法也不同（改共用的那个函数，
   而不是逐个源加例外）。这是逐源诊断看不见的结构。

用法
----
    python scripts/remedy.py
    python scripts/remedy.py --json
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DOCTOR_REPORT = ROOT / "data" / "runtime" / "lighthouse" / "source_doctor.json"
TIERS = ROOT / "data" / "runtime" / "lighthouse" / "source_tiers.json"
GAPS = ROOT / "data" / "runtime" / "lighthouse" / "coverage_gaps.json"


# 病因 -> (处方, 该病因的共性根因)
# 关键是每条处方要指出**改哪里**，而不是「检查这个源」。
REMEDY: dict[str, dict[str, str]] = {
    "never_produced": {
        "title": "启用后从未产出任何记录",
        "root_cause": "通常是列表页取不到链接（detail_pattern 不匹配该站新的"
                      "CMS 形态），或配置里的 URL 已不是列表页。",
        "remedy": "跑 `python -c \"import sys;sys.path.insert(0,'.');"
                  "from lighthouse.run import with_defaults;...\"` 验链接形态，"
                  "或用 `scripts/probe_candidates.py` 探测该站是否还活着。"
                  "若详情页链接在 JS 参数里（gov.cn 常见 onclick 形态），"
                  "需要给抽链器加该站形态——改 `collectors/html_list.py`，"
                  "不要逐个源加例外。",
    },
    "no_links": {
        "title": "列表页取不到公告链接",
        "root_cause": "多半是选择器或链接过滤把内容滤光了；也可能是页面要渲染。",
        "remedy": "先看 `source_tiers` 给的等级：T2 说明静态被 WAF 挡，"
                  "该配渲染兜底；T3 说明采到了但正文是栏目页，"
                  "该修链接形态而不是改选择器。",
    },
    "circuit_broken": {
        "title": "被熔断（长期零产出或连续失败）",
        "root_cause": "退避阶梯已经把它推到很长的间隔，"
                      "但没人真的去看它是不是彻底失效了。",
        "remedy": "跑 `scripts/probe_candidates.py` 单独探活。"
                  "真失效就改成 T5（30 天探一次），"
                  "只是暂时没更新的就降频到 T4（7 天）而不是熔断。",
    },
    "zero_yield_recent": {
        "title": "近期零产出",
        "root_cause": "可能只是还没到更新周期（政府源按天/周/月发文都有）。",
        "remedy": "别急着改配置。先用 `coverage_gap.py` 看这个主题是不是"
                  "全行业都零产出——是的话是信源问题，"
                  "只这一个源零产出可能只是它淡季。",
    },
    "gate_dropped": {
        "title": "采到了但被质量门禁全丢掉",
        "root_cause": "门禁阈值太严，或者这一批内容形态特殊（表格型公告、"
                      "图片版通知）。",
        "remedy": "用 `scripts/sample_audit.py --limit 20` 抽样人眼读——"
                  "**不要直接调阈值**。2026-10-03 实测三次凭统计量调阈值，"
                  "误杀 23.2%/10.4%/13.7%，因为基准本身是脏的。",
    },
}


def load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except ValueError:
        return {}


def _library_counts() -> dict[str, int]:
    """每个源在库里的记录数——判断「从未产出」的唯一可靠依据。

    踩过的坑：原来靠 ``source_doctor.json`` 的 ``evidence["records"]``
    判断，但那个字段**根本不存在**（实测 evidence 里只有 collectable /
    enabled / wait_hours）。``evidence.get("records", 0)`` 于是恒等于 0，
    于是 34 个有 23~264 条记录的源全被误判成「启用后从未产出」。

    Python 里猜错键名是**静默失败**：get 返回默认值，代码照跑，结论全错
    而不报错。所以「从未产出」这种硬结论必须查真实数据，不查别处的摘要。
    """
    db = ROOT / "data" / "beacon.db"
    if not db.exists():
        return {}
    import sqlite3
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return {str(sid): int(n) for sid, n in con.execute(
            "SELECT source_id, COUNT(*) FROM announcements GROUP BY source_id")}
    finally:
        con.close()


def collect_diagnoses() -> list[dict[str, Any]]:
    """把 source_doctor 的逐源结论归并成「病因」。

    source_doctor.json 的键是 ``results``（实测顶层 at / total / by_status /
    by_tier / collective / results）。之前这里写的是 ``diagnoses``，
    于是读到空列表、输出退化成「只有 1 类病因」而且不报错——猜错键名在
    Python 里就是静默返回空。所以：多列候选键 + 取不到就明确报错。
    """
    report = load(DOCTOR_REPORT)
    items = (report.get("results") or report.get("diagnoses")
             or report.get("sources") or [])
    if not items:
        raise SystemExit(
            "source_doctor.json 里没有诊断条目（顶层键: %s）。"
            "先跑 python scripts/source_doctor.py 生成报告。"
            % ", ".join(sorted(report)))

    counts = _library_counts()
    out: list[dict[str, Any]] = []
    for item in items:
        sid = str(item.get("source_id") or "")
        if not sid:
            continue
        status = str(item.get("status") or "")
        if status == "ok":
            continue
        records = counts.get(sid, 0)
        evidence = dict(item.get("evidence") or {})
        evidence["records"] = records          # 补上真实产出，后面归并要用

        if records == 0 and status in ("bad", "error"):
            cause = "never_produced"
        elif status in ("bad", "error"):
            cause = "no_links"
        elif status == "warn":
            cause = "zero_yield_recent"
        else:
            continue
        out.append({"source_id": sid, "cause": cause, "status": status,
                    "detail": item.get("reason", ""), "evidence": evidence})
    return out


def collect_tier_issues() -> list[dict[str, Any]]:
    tiers = load(TIERS).get("tiers") or {}
    out = []
    for sid, t in tiers.items():
        if t.get("tier") in ("T5", "T4"):
            out.append({"source_id": sid, "cause":
                        "circuit_broken" if t.get("tier") == "T5"
                        else "zero_yield_recent",
                        "status": t.get("tier"),
                        "detail": t.get("reason", ""),
                        "evidence": t.get("signals", {})})
    return out


def build() -> dict[str, Any]:
    items = collect_diagnoses() + collect_tier_issues()
    groups: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for it in items:
        groups[it["cause"]].append(it)
    # 排序：先看影响面（多少源同款病因），再看严重度
    ordered = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    prescriptions = []
    for cause, members in ordered:
        meta = REMEDY.get(cause, {
            "title": cause, "root_cause": "未归类的病因",
            "remedy": "需要人工判一次"})
        prescriptions.append({
            "cause": cause,
            "title": meta["title"],
            "affected_count": len(members),
            "sources": [m["source_id"] for m in members][:20],
            "root_cause": meta["root_cause"],
            "remedy": meta["remedy"],
        })
    return {
        "total_flagged": len(items),
        "distinct_causes": len(prescriptions),
        "prescriptions": prescriptions,
        "coverage_gaps": load(GAPS).get("gaps", [])[:5],
    }


def render(b: dict[str, Any]) -> str:
    lines = ["病因 → 证据 → 处方（%d 个源有问题，归为 %d 类）"
             % (b["total_flagged"], b["distinct_causes"]),
             "=" * 68]
    for i, p in enumerate(b["prescriptions"], 1):
        lines.append("")
        lines.append("%d. %s —— 波及 %d 个源"
                     % (i, p["title"], p["affected_count"]))
        lines.append("   病因: %s" % p["root_cause"])
        lines.append("   证据: %s" % ", ".join(p["sources"][:8])
                     + (" ..." if len(p["sources"]) > 8 else ""))
        lines.append("   处方: %s" % p["remedy"])
    if b["coverage_gaps"]:
        lines.append("")
        lines.append("-" * 68)
        lines.append("另有覆盖缺口（该采的没采，见 coverage_gap.py）:")
        for g in b["coverage_gaps"]:
            lines.append("  [%s] %s —— %s" % (g["severity"], g["dimension"],
                                              g["finding"]))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="病因 → 证据 → 处方")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    b = build()
    if args.json:
        print(json.dumps(b, ensure_ascii=False, indent=2))
    else:
        print(render(b))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())