"""对比工作台 — topic-scoped field-level comparison across regions.

Page module for the Streamlit workbench. Data-layer helpers are pure functions
(testable without Streamlit); the render function wires them into the UI.

视觉状态与静态稿一致：
    diff     琥珀差异高亮   mk-diff
    same     一致项折叠     mk-same
    missing  未出台灰斜体   mk-missing
    pending  待确认蓝虚线   mk-pending
    revoked  已废止划线     mk-revoked
    extra    附加条件紫角标 mk-extra（叠加标记）
"""
from __future__ import annotations

import html
import json
from typing import Any

import streamlit as st
from sqlalchemy import select

from compare_engine import build_matrix, to_csv_rows
from storage.db import Database
from storage.models import Announcement, FieldExtraction, PolicyTopic, TopicDocLink

DIFF_LABEL = {"diff": "差异", "same": "一致", "missing": "未出台", "pending": "待确认", "revoked": "已废止"}
DIFF_ORDER = ["diff", "same", "missing", "pending", "revoked"]


def list_topics(db_path: str) -> list[dict[str, Any]]:
    db = Database(db_path)
    try:
        with db.session() as session:
            rows = list(session.scalars(select(PolicyTopic).where(PolicyTopic.status == "active")).all())
            return [
                {
                    "id": row.id,
                    "name": row.name,
                    "category": row.category,
                    "keywords": list(row.keywords or []),
                    "field_schema": list(row.field_schema or []),
                }
                for row in rows
            ]
    finally:
        db.close()


def load_topic_docs(db_path: str, topic_id: int) -> list[dict[str, Any]]:
    """Linked docs with status auto/approved, one per region (keep highest confidence)."""
    db = Database(db_path)
    try:
        with db.session() as session:
            links = list(
                session.scalars(
                    select(TopicDocLink).where(
                        TopicDocLink.topic_id == topic_id,
                        TopicDocLink.status.in_(("auto", "approved")),
                    )
                ).all()
            )
            best: dict[str, dict[str, Any]] = {}
            for link in links:
                ann = session.get(Announcement, link.announcement_id)
                if ann is None:
                    continue
                region = str(ann.region_province or "未知")
                current = best.get(region)
                if current is None or link.confidence > current["confidence"]:
                    best[region] = {
                        "id": ann.id,
                        "region": region,
                        "title": ann.title,
                        "url": ann.source_url,
                        "published_at": ann.publish_date,
                        "retention_class": ann.retention_class,
                        "confidence": int(link.confidence or 0),
                    }
            return sorted(best.values(), key=lambda d: d["region"])
    finally:
        db.close()


def load_topic_extractions(db_path: str, topic_id: int) -> dict[int, dict[str, dict[str, Any]]]:
    """Extraction map for one topic; a reviewed revision overrides the raw value."""
    db = Database(db_path)
    try:
        with db.session() as session:
            rows = list(session.scalars(select(FieldExtraction).where(FieldExtraction.topic_id == topic_id)).all())
            result: dict[int, dict[str, dict[str, Any]]] = {}
            for row in rows:
                value = row.revised_value if row.revised_value not in (None, "") else row.value
                status = "auto" if row.revised_value not in (None, "") else row.status
                result.setdefault(row.announcement_id, {})[row.field_key] = {
                    "value": value,
                    "status": status,
                    "confidence": int(row.confidence or 0),
                    "evidence_text": row.evidence_text,
                }
            return result
    finally:
        db.close()


def matrix_css() -> str:
    return """
    <style>
    .mk-table {width:100%; border-collapse:separate; border-spacing:0; font-size:13px; border:1px solid var(--line,rgba(120,120,130,.22)); border-radius:12px; overflow:hidden;}
    .mk-table th {background:rgba(125,125,135,.08); font-weight:600; padding:9px 12px; text-align:left; white-space:nowrap; border-bottom:1px solid var(--line,rgba(120,120,130,.22));}
    .mk-table td {padding:9px 12px; border-bottom:1px solid var(--line,rgba(120,120,130,.14)); vertical-align:top;}
    .mk-table tr:last-child td {border-bottom:none;}
    .mk-field {font-weight:600; white-space:nowrap; background:rgba(125,125,135,.05);}
    .mk-field small {display:block; font-weight:400; opacity:.65;}
    .mk-diff {background:rgba(217,119,6,.14);}
    .mk-missing {color:rgba(130,130,140,.85); font-style:italic;}
    .mk-pending {border-left:3px dashed rgba(59,130,246,.75); background:rgba(59,130,246,.07);}
    .mk-revoked {text-decoration:line-through; color:rgba(130,130,140,.75);}
    .mk-same {color:rgba(110,110,120,.9);}
    .mk-extra {display:inline-block; margin-left:6px; padding:0 5px; border-radius:6px; font-size:11px; background:rgba(147,51,234,.16); color:#9333ea;}
    .mk-chip {display:inline-flex; align-items:center; gap:5px; padding:2px 10px; border-radius:999px; font-size:12px; margin-right:8px; border:1px solid var(--line,rgba(120,120,130,.25));}
    .mk-evidence {display:block; font-size:11px; opacity:.6; margin-top:3px;}
    </style>
    """


def render_matrix_html(matrix: dict[str, Any], titles: dict[int, str] | None = None) -> str:
    """Serialize the matrix as a styled HTML table (差异色 + 悬停依据)."""
    titles = titles or {}
    regions = matrix["regions"]
    header = "".join(f"<th>{html.escape(str(r))}</th>" for r in regions)
    body_rows: list[str] = []
    for row in matrix["rows"]:
        cells_html: list[str] = []
        for r in regions:
            cell = row["cells"][r]
            klass = f"mk-{cell['diff_type']}"
            extra = "<span class='mk-extra'>附</span>" if cell.get("extra") else ""
            value = html.escape(str(cell.get("value") or DIFF_LABEL[cell["diff_type"]]))
            evidence = html.escape(str(cell.get("evidence_text") or ""))
            ann_id = cell.get("announcement_id")
            title = titles.get(ann_id, "") if ann_id is not None else ""
            tip = f" title='{html.escape(title)}'" if title else ""
            cells_html.append(
                f"<td class='{klass}'{tip}>{value}{extra}<span class='mk-evidence'>{evidence}</span></td>"
            )
        label = html.escape(str(row["label"]))
        summary = html.escape(str(row["summary"]))
        body_rows.append(f"<tr><td class='mk-field'>{label}<small>{summary}</small></td>{''.join(cells_html)}</tr>")
    return f"<table class='mk-table'><thead><tr><th>字段</th>{header}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"


STATE_CLASS = {"diff": "mk-diff", "same": "mk-same", "missing": "mk-missing",
               "pending": "mk-pending", "revoked": "mk-revoked"}


def render_region_table(result: dict[str, Any]) -> str:
    """Region-per-row comparison for topics discovered in the real store.

    Reuses the .mk-* classes from matrix_css() so it looks like the rest of the
    workbench. Values are per-region rows rather than per-field columns because the
    discovered topics have no field_schema: the doc's matrix needs one, and
    inventing fields per topic would be guessing.
    """
    amount_state = STATE_CLASS.get(result["field_states"]["amount"], "mk-same")
    deadline_state = STATE_CLASS.get(result["field_states"]["deadline"], "mk-same")
    rows_html: list[str] = []
    for row in result["rows"]:
        if row.get("amount"):
            amount_cell = f"{row['amount'] / 10000:.0f}万元" if row["amount"] >= 10000 else f"{row['amount']:.0f}元"
            amount_class = amount_state
        elif row.get("amount_unverified"):
            amount_cell = f"待确认（原文提及 {row['amount_unverified'] / 10000:.0f}万元）"
            amount_class = "mk-pending"
        else:
            amount_cell = DIFF_LABEL["missing"]
            amount_class = "mk-missing"
        deadline = row.get("deadline") or DIFF_LABEL["missing"]
        deadline_class = deadline_state if row.get("deadline") else "mk-missing"
        link = (f"<a href='{html.escape(str(row['url']))}' target='_blank' rel='noopener'>原文</a>"
                if row.get("url") else "")
        title = html.escape(str(row.get("title") or ""))
        rows_html.append(
            f"<tr><td class='mk-field'>{html.escape(str(row['region']))}"
            f"<small>{html.escape(str(row.get('issuing_body') or ''))}</small></td>"
            f"<td>{html.escape(str(row.get('level') or ''))}</td>"
            f"<td class='{amount_class}'>{html.escape(amount_cell)}</td>"
            f"<td class='{deadline_class}'>{html.escape(str(deadline))}</td>"
            f"<td>{html.escape(str(row.get('publish_date') or '—'))}</td>"
            f"<td>{title}<span class='mk-evidence'>{link}</span></td></tr>"
        )
    return (
        "<table class='mk-table'><thead><tr><th>地区 / 发文单位</th><th>层级</th>"
        "<th>金额</th><th>时限</th><th>发布日期</th><th>标题 / 原文</th></tr></thead>"
        f"<tbody>{''.join(rows_html)}</tbody></table>"
    )


def render(db_path: str = "data/beacon.db") -> None:
    st.markdown(matrix_css(), unsafe_allow_html=True)
    mode = st.radio("对比数据来源", ["真实数据自动发现", "已登记主题"],
                    horizontal=True, key="compare_mode",
                    help="真实数据：从采集结果里按分类自动发现可对比主题；已登记主题：使用 policy_topics 中人工登记的主题")
    if mode == "真实数据自动发现":
        _render_discovered(db_path)
        return
    _render_registered(db_path)


def _render_discovered(db_path: str) -> None:
    from compare_topics import (
        build_comparison,
        build_topics,
        fetch_announcements,
        to_csv,
        to_excel,
        to_markdown,
    )

    records = fetch_announcements(db_path)
    if not records:
        st.info("暂无采集数据。先运行 `python scheduler.py --once` 采集一轮。")
        return
    topics = build_topics(records)
    portals = sum(1 for r in records if r.get("_portal"))
    st.caption(
        f"共 {len(records)} 条记录，已排除门户/栏目页 {portals} 条；"
        f"自动发现可对比主题 {len(topics)} 个，其中跨层级可比 "
        f"{sum(1 for t in topics if t['comparable'])} 个。金额只取公文类文件，"
        "新闻与访谈类原文的数字标为待确认，不参与差异判定。"
    )
    if not topics:
        st.info("当前数据还不足以形成对比主题（需要同一分类下至少 2 条记录）。")
        return

    names = [t["name"] for t in topics]
    labels = {
        t["name"]: f"{t['name']}　· {t['doc_count']}份 / {t['level_count']}层级 / {t['region_count']}地区"
                  + ("　[可比]" if t["comparable"] else "")
        for t in topics
    }
    chosen = st.selectbox("对比主题", names, format_func=lambda n: labels[n],
                          key="discovered_topic")
    topic = topics[names.index(chosen)]
    result = build_comparison(topic)

    chips = " ".join(
        f"<span class='mk-chip'>{label} <b>{html.escape(DIFF_LABEL[result['field_states'][key]])}</b></span>"
        for key, label in (("amount", "金额"), ("deadline", "时限"))
    )
    st.markdown(
        f"<div>{chips}"
        f"<span class='mk-chip'>覆盖地区 <b>{len(result['regions'])}</b></span>"
        f"<span class='mk-chip'>文件数 <b>{result['doc_count']}</b></span>"
        f"<span class='mk-chip'>金额区间 <b>{html.escape(result['amount_summary'])}</b></span></div>",
        unsafe_allow_html=True,
    )
    st.markdown(render_region_table(result), unsafe_allow_html=True)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.download_button("导出 CSV", data=to_csv(result).encode("utf-8-sig"),
                           file_name=f"对比_{chosen}.csv", mime="text/csv", width="stretch")
    with c2:
        st.download_button("导出 JSON", data=json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8"),
                           file_name=f"对比_{chosen}.json", mime="application/json", width="stretch")
    with c3:
        excel = to_excel(result)
        if excel is None:
            # Still a download_button so all four exports look and behave the same.
            st.download_button("导出 Excel（需 pip install openpyxl）", data=b"",
                               file_name=f"对比_{chosen}.xlsx", mime="application/octet-stream",
                               disabled=True, width="stretch",
                               help="CSV 与 JSON 已包含同样的数据；装 openpyxl 后此处可导出 xlsx。")
        else:
            st.download_button("导出 Excel", data=excel,
                               file_name=f"对比_{chosen}.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               width="stretch")
    with c4:
        st.download_button("导出 Markdown 模板", data=to_markdown(result).encode("utf-8"),
                           file_name=f"对比_{chosen}.md", mime="text/markdown", width="stretch")

    with st.expander("对比结果 JSON（接口结构模板）"):
        st.code(json.dumps(result, ensure_ascii=False, indent=2), language="json")


def _render_registered(db_path: str) -> None:
    topics = list_topics(db_path)
    if not topics:
        st.info("暂无对比主题。先运行 `python seed_compare.py` 生成种子主题。")
        return

    topic_names = [t["name"] for t in topics]
    default_index = topic_names.index("灵活就业人员社保补贴") if "灵活就业人员社保补贴" in topic_names else 0
    chosen = st.selectbox("对比主题", topic_names, index=default_index)
    topic = topics[topic_names.index(chosen)]

    docs = load_topic_docs(db_path, topic["id"])
    regions_with_doc = [d["region"] for d in docs]
    selected_regions = st.multiselect(
        "参与对比的地区",
        options=sorted(set(regions_with_doc)),
        default=sorted(set(regions_with_doc)),
        help="取消勾选即隐藏该地区列；未出台文件的地区会以灰斜体示出",
    )

    docs_selected = [d for d in docs if d["region"] in selected_regions]
    extractions = load_topic_extractions(db_path, topic["id"])
    schema = topic["field_schema"] or [{"field_key": "subsidy_amount", "label": "补贴标准"}]
    matrix = build_matrix(docs_selected, extractions, schema, expected_regions=selected_regions)

    counts = matrix["counts"]
    chips = " ".join(
        f"<span class='mk-chip'>{DIFF_LABEL[state]} <b>{counts[state]}</b></span>"
        for state in DIFF_ORDER
    )
    st.markdown(f"<div>{chips}<span class='mk-chip'>覆盖地区 <b>{len(docs_selected)}</b></span></div>", unsafe_allow_html=True)
    titles = {d["id"]: d["title"] for d in matrix["docs"] if d.get("id")}
    st.markdown(render_matrix_html(matrix, titles), unsafe_allow_html=True)

    left, right = st.columns(2)
    with left:
        csv_text = "\n".join(",".join(_quote(cell) for cell in line) for line in to_csv_rows(matrix))
        st.download_button("导出 CSV", data=csv_text.encode("utf-8-sig"),
                           file_name=f"对比_{chosen}.csv", mime="text/csv", width="stretch")
    with right:
        st.download_button("导出 JSON", data=json.dumps(matrix, ensure_ascii=False, indent=2).encode("utf-8"),
                           file_name=f"对比_{chosen}.json", mime="application/json", width="stretch")


def _quote(cell: str) -> str:
    if "," in cell or '"' in cell or "\n" in cell:
        return '"' + cell.replace('"', '""') + '"'
    return cell
