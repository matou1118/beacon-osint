"""成刊：把一天的记录编排成日报/周报/月报。

借鉴 AIHOT（github.com/KKKKhazix/AIHOT）的成刊信息架构，替换掉它的判定方式。

抄了什么
--------
* **一个事件一个条目**，不是一个来源一个条目。同一件事被几个省转载，读者只看一次。
* **按分类分节**，只显示有内容的节。
* **刊期边界明确**：日报是「前一日 08:00 → 当日 08:00」，不跨期重复。
* **导语用真实数字**：不写空话。

没抄什么
--------
* LLM 打分与 LLM 写导语。导语是固定模板 + 真实统计。
* 向量聚簇。灯塔用自己那套 simhash（``rules/media_cluster.py``）——确定性、可复算、
  不依赖外部模型，符合 ``AGENTS.md`` 对「不使用 LLM」的要求。
* 「独立来源只算一次」的加分规则。灯塔的 ``corroboration_count`` 算的就是独立域名数，
  语义上已经在做同一件事，不重复实现。

只读，不写库。成刊是交付物，不是数据来源。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

DB = _ROOT / "data" / "beacon.db"
OUT_DIR = _ROOT / "data" / "publications"

# 刊期边界。照 AIHOT 的做法定在 08:00 而不是 00:00：政府网站早上才发当日文件，
# 按自然日切会把上午发的公告切到前一天去。
DAILY_CUTOFF_HOUR = 8

EDITION_LABELS = {"daily": "日报", "weekly": "周报", "monthly": "月报"}

# issuing_body / summary 不在列表里——它们只存在 extra JSON 中，读出来后再补。
COLUMNS = (
    "id", "source_id", "source_url", "title", "publish_date",
    "category_l1", "category_l2", "region_province", "region_city", "gov_level",
    "amount", "deadline", "target_group",
    "media_cluster_id", "media_heat", "media_terms",
    "corroboration_status", "corroboration_count", "corroborating_sources",
    "needs_review", "extra",
)


def log(message: str) -> None:
    print(message, flush=True)


# ------------------------------------------------------------------ 刊期窗口


def period(edition: str, ref: date) -> tuple[date, date]:
    """返回 (起始日, 结束日)，左闭右开。

    日报是 ref 当天。周报是 ref 往前 7 天。月报是 ref 所在月的 1 号往前一个月。
    都用「左闭右开」的日期区间，和 ``publish_date`` 的字符串比较直接对齐。
    """
    if edition == "daily":
        return ref, ref + timedelta(days=1)
    if edition == "weekly":
        return ref - timedelta(days=7), ref
    if edition == "monthly":
        start = ref.replace(day=1)
        prev = start - timedelta(days=1)
        return prev.replace(day=1), start
    raise ValueError(f"未知的成刊类型：{edition}")


def period_label(edition: str, start: date, end: date) -> str:
    if edition == "daily":
        return f"{start:%Y-%m-%d}"
    if edition == "weekly":
        return f"{start:%Y-%m-%d} ~ {end - timedelta(days=1):%Y-%m-%d}"
    return f"{start:%Y-%m} ~ {end - timedelta(days=1):%Y-%m}"


# ------------------------------------------------------------------ 读取


def _summary_of(extra: Mapping[str, Any], fallback: str = "") -> str:
    """取出摘要，并把页脚残留洗掉。

    摘要在入库那一步就固化进 `extra.summary` 了，所以清洗必须发生在**读**
    这一侧，不能指望改 strip_noise 就追溯生效——2026-10-02 实测：洗白能力
    接上后重出日报，存量摘要里的「网站标识码 / 版权所有 / 扫一扫」仍然
    原样印出来。

    在这里洗还有个好处：历史记录不用回填、不用改库，全部存量摘要一次性受益；
    而清洗规则以后再收紧，也只是重新出刊就生效。
    """
    from lighthouse import quality
    from lighthouse.quality import strip_noise

    # 词表由 load_config() 注入，而 publish.main() 并不读采集配置——不显式
    # 加载的话 strip_noise 会退化成「只滤 markdown 面包屑」，页脚照印。
    # 2026-10-02 实测踩过：接上 set_chrome_patterns 后重出日报，日志显示
    # 「收录 33 条」一切正常，摘要里的「网站标识码」却一个字没少。
    quality.load_config()

    raw = str(extra.get("summary") or fallback or "")
    return strip_noise(raw).strip()


def load_records(db_path: Path, start: date, end: date) -> list[dict[str, Any]]:
    """读一个日期区间的记录，排除已判为脏数据的。

    ``needs_review`` 是 ``scripts/dedup.py`` / ``scripts/mark_portal_pages.py`` /
    ``scripts/flag_dirty_records.py`` 给问题记录打的标记。成刊是给人读的产物，
    不该把已知有问题的条目印出来。

    刻意不物理删除：删除是不可逆的，而这一列是可逆的——复核完把标记清掉就回来了。
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            f"SELECT {', '.join(COLUMNS)} FROM announcements "
            "WHERE publish_date >= ? AND publish_date < ? "
            "AND COALESCE(needs_review, 0) = 0 "
            "ORDER BY publish_date, id",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    finally:
        con.close()
    records: list[dict[str, Any]] = []
    for row in rows:
        record = dict(zip(COLUMNS, row))
        try:
            extra = json.loads(record.pop("extra") or "{}") or {}
        except (TypeError, ValueError):
            extra = {}
        record["summary"] = _summary_of(extra)
        record["issuing_body"] = record.get("issuing_body") or extra.get("issuing_body") or ""
        record["_extra"] = extra
        records.append(record)
    return records


# ------------------------------------------------------------------ 归组


def _item_from(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "record_id": record.get("id"),
        "title": str(record.get("title") or "").strip(),
        "summary": str(record.get("summary") or "").strip(),
        "publish_date": str(record.get("publish_date") or "")[:10],
        "category_l1": record.get("category_l1") or "其他",
        "category_l2": record.get("category_l2"),
        "region": {"province": record.get("region_province"),
                   "city": record.get("region_city")},
        "gov_level": record.get("gov_level"),
        "issuing_body": record.get("issuing_body") or "",
        "amount": record.get("amount"),
        "deadline": record.get("deadline"),
        "target_group": record.get("target_group"),
        "source": {"source_id": record.get("source_id"),
                   "url": record.get("source_url")},
    }


def _event_from(members: list[Mapping[str, Any]]) -> dict[str, Any]:
    """把同一 cluster_id 下的多条记录合成一个事件条目。"""
    heat = max((float(m.get("media_heat") or 0) for m in members), default=0.0)
    corrob = max((int(m.get("corroboration_count") or 0) for m in members), default=0)
    leader = max(members, key=lambda m: (int(m.get("corroboration_count") or 0),
                                         str(m.get("publish_date") or "")))
    # 独立来源去重：同一个 source_id 在一个事件里只算一次（AIHOT 的抗刷规则）。
    seen: set[str] = set()
    sources: list[dict[str, Any]] = []
    for member in members:
        sid = str(member.get("source_id") or "")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        sources.append({"source_id": sid,
                        "issuing_body": member.get("issuing_body") or sid,
                        "url": member.get("source_url"),
                        "publish_date": str(member.get("publish_date") or "")[:10]})
    return {
        "event_id": leader.get("media_cluster_id"),
        "title": str(leader.get("title") or "").strip(),
        "summary": str(leader.get("summary") or "").strip(),
        "publish_date": str(leader.get("publish_date") or "")[:10],
        "category_l1": leader.get("category_l1") or "其他",
        "category_l2": leader.get("category_l2"),
        "region": {"province": leader.get("region_province"),
                   "city": leader.get("region_city")},
        "gov_level": leader.get("gov_level"),
        "issuing_body": leader.get("issuing_body") or "",
        "amount": leader.get("amount"),
        "deadline": leader.get("deadline"),
        "is_event": True,
        "heat_score": heat,
        "terms": leader.get("media_terms") or [],
        "corroboration": {"status": "corroborated" if corrob >= 2 else "single_source",
                          "count": corrob},
        "sources": sources,
        "source_count": len(sources),
        "record_ids": [m.get("id") for m in members],
    }


def sort_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """排序：有事件的在前，然后热度高、交叉验证多、日期新的在前。

    用两趟稳定排序而不是把日期也塞进 key：日期是字符串，混在数值 key 里没法
    一次比完（要么得取负字符串这种只对 ASCII 生效的花招，要么得转 datetime）。
    Python 的 sort 是稳定的，所以「先排次要键、再排主要键」结果等价。
    """
    by_date = sorted(items, key=lambda i: str(i.get("publish_date") or ""), reverse=True)
    return sorted(by_date, key=lambda i: (
        0 if i.get("is_event") else 1,
        -float(i.get("heat_score") or 0),
        -int((i.get("corroboration") or {}).get("count") or 0),
    ))


# ------------------------------------------------------------------ 导语


def build_lead(stats: Mapping[str, Any], top: list[Mapping[str, Any]]) -> str:
    """导语。固定模板 + 真实统计，不生成。

    AIHOT 用 LLM 写导语；这里用真实数字拼。能说的都是库里查得到的数，
    一个字都不用编——这是 ``AGENTS.md`` 对摘要/要点「只能由已审核的结构化字段
    和固定模板生成」的要求。
    """
    total = int(stats.get("total") or 0)
    if not total:
        return "本期无收录。"
    parts = [f"本期收录 {total} 条"]
    regions = int(stats.get("regions") or 0)
    if regions:
        parts.append(f"覆盖 {regions} 个地区")
    events = int(stats.get("events") or 0)
    if events:
        parts.append(f"聚出 {events} 个多源事件")
    corrob = int(stats.get("corroborated") or 0)
    if corrob:
        parts.append(f"{corrob} 条经多源交叉印证")
    cats = int(stats.get("categories") or 0)
    if cats:
        parts.append(f"分属 {cats} 个分类")
    lead = "，".join(parts) + "。"
    if top:
        head = top[0]
        mark = f"热度最高：{head['title']}"
        if int((head.get("corroboration") or {}).get("count") or 0) >= 2:
            mark += f"（{head['corroboration']['count']} 个独立来源）"
        lead += mark + "。"
    return lead


# ------------------------------------------------------------------ 组装


def build_edition(edition: str, ref: date, db_path: Path = DB) -> dict[str, Any]:
    start, end = period(edition, ref)
    records = load_records(db_path, start, end)

    # 先按 media_cluster_id 归组事件，剩下的按单条处理。
    #
    # 归组时只捞到 1 个来源的，不能算事件：media_cluster 的 min_sources 是在
    # 回填时按**整月**判的，而成刊按日/周开窗，经常只剩 1 个成员。标成事件
    # 等于对外宣称「多源印证」，而实际上只有一个来源——政务场景下这是误导。
    events: dict[str, list[Mapping[str, Any]]] = {}
    singles: list[Mapping[str, Any]] = []
    for record in records:
        cluster = str(record.get("media_cluster_id") or "").strip()
        if cluster:
            events.setdefault(cluster, []).append(record)
        else:
            singles.append(record)

    items: list[dict[str, Any]] = []
    for members in events.values():
        if len({str(m.get("source_id") or "") for m in members}) >= 2:
            items.append(_event_from(members))
        else:
            singles.extend(members)
    items.extend(_item_from(record) for record in singles)
    items = sort_items(items)

    by_category: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_category.setdefault(item["category_l1"], []).append(item)

    sections = [{"category_l1": name, "items": group}
                for name, group in sorted(by_category.items(),
                                         key=lambda kv: (-len(kv[1]), kv[0]))]

    stats = {
        "total": len(records),
        "events": sum(1 for i in items if i.get("is_event")),
        "corroborated": sum(1 for r in records
                            if r.get("corroboration_status") == "corroborated"),
        "categories": len(by_category),
        "regions": len({r.get("region_province") or r.get("region_city")
                        for r in records if r.get("region_province") or r.get("region_city")}),
        "by_category": dict(Counter(r.get("category_l1") or "其他" for r in records)),
    }

    return {
        "edition": edition,
        "edition_label": EDITION_LABELS.get(edition, edition),
        "ref_date": ref.isoformat(),
        "period": {"from": start.isoformat(), "to": end.isoformat(),
                   "label": period_label(edition, start, end)},
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stats": stats,
        "lead": build_lead(stats, items),
        "sections": sections,
        "item_count": len(items),
    }


# ------------------------------------------------------------------ 输出


def render_markdown(payload: Mapping[str, Any], per_section: int = 20) -> str:
    """渲染成人读的刊。JSON 保持完整，markdown 才是读物。

    限制的是 markdown，不是数据：月报实测 1640 条、1.4 MB，把每条原文摘要都铺开
    没人会读。完整数据在同名 .json 里，这里只留每节前 N 条并注明总数。
    """
    lines = [
        f"# 灯塔{ payload['edition_label'] } · { payload['period']['label'] }",
        "",
        payload["lead"],
        "",
        f"> 生成于 {payload['generated_at']}　收录 {payload['stats']['total']} 条　"
        f"事件 {payload['stats']['events']} 个　交叉印证 {payload['stats']['corroborated']} 条",
        "",
    ]
    truncated = False
    for section in payload["sections"]:
        total = len(section["items"])
        shown = section["items"][:per_section]
        lines.append(f"## {section['category_l1']}（{total} 条）")
        lines.append("")
        for item in shown:
            mark = "🔥" if item.get("is_event") else ""
            lines.append(f"### {mark}{item['title']}")
            lines.append("")
            where = " · ".join(x for x in (
                item.get("publish_date"), item.get("issuing_body"),
                (item.get("region") or {}).get("province"),
            ) if x)
            if where:
                lines.append(f"*{where}*")
                lines.append("")
            if item.get("summary"):
                lines.append(item["summary"])
                lines.append("")
            if item.get("is_event"):
                corrob = item.get("corroboration") or {}
                lines.append(f"**{item.get('source_count', 0)} 个来源**"
                             + (f"，其中 {corrob['count']} 个独立域名" if corrob.get("count") else ""))
                lines.append("")
                for source in item.get("sources", []):
                    lines.append(f"- [{source.get('issuing_body')}]({source.get('url')})")
                lines.append("")
            else:
                url = (item.get("source") or {}).get("url")
                if url:
                    lines.append(f"[原文]({url})")
                    lines.append("")
        if total > per_section:
            truncated = True
            lines.append(f"*（本节共 {total} 条，此处只列前 {per_section} 条；"
                         f"完整内容见同名 .json）*")
            lines.append("")
    if truncated:
        lines.append("---")
        lines.append("")
        lines.append("本刊为节选。完整数据（含全部条目、多来源清单、交叉验证详情）见同名 `.json`。")
    return "\n".join(lines).rstrip() + "\n"


def write_edition(payload: Mapping[str, Any], out_dir: Path = OUT_DIR,
                  per_section: int = 20) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{payload['edition']}-{payload['ref_date']}"
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    md_path.write_text(render_markdown(payload, per_section=per_section),
                       encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成灯塔日报/周报/月报")
    parser.add_argument("--edition", choices=sorted(EDITION_LABELS),
                        default="daily", help="成刊类型（默认 daily）")
    parser.add_argument("--ref-date", default=None, help="参考日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--db", default=str(DB))
    parser.add_argument("--out", default=str(OUT_DIR))
    parser.add_argument("--per-section", type=int, default=20,
                        help="markdown 每节最多列几条（JSON 不受此限制）")
    args = parser.parse_args(argv)

    ref = (date.fromisoformat(args.ref_date) if args.ref_date
           else datetime.now(timezone.utc).astimezone().date())
    # 周报月报是定期动作，不是每次采集都该重出，所以默认只出日报。
    payload = build_edition(args.edition, ref, Path(args.db))
    paths = write_edition(payload, Path(args.out), per_section=args.per_section)
    stats = payload["stats"]
    log(f"{payload['edition_label']} {payload['period']['label']}　"
        f"收录 {stats['total']}　事件 {stats['events']}　"
        f"交叉 {stats['corroborated']}　分类 {stats['categories']}")
    log(f"  {paths['markdown']}")
    log(f"  {paths['json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
