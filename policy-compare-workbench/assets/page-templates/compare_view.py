# compare_view.py — 对比工作台
# 主题选择 + 地区多选 → HTML 对比表，六种差异状态

import streamlit as st
from sqlalchemy import select
from storage.db import Database
from storage.models import Announcement, FieldExtraction, PolicyTopic, TopicDocLink
from compare_engine import build_matrix, to_csv_rows

def render(db_path: str) -> None:
    db = Database(db_path)
    try:
        with db.session() as session:
            topics = list(session.scalars(select(PolicyTopic).where(PolicyTopic.status == "active")).all())
    finally:
        db.close()

    if not topics:
        st.info("暂无对比主题，请先在 seed_compare.py 中种子化。")
        return

    topic_names = [t.name for t in topics]
    selected = st.sidebar.selectbox("选择主题", topic_names, key="cmp_topic")
    topic = next(t for t in topics if t.name == selected)

    # 地区多选
    with st.sidebar:
        st.subheader("地区筛选")
        # 从现有链接中提取地区
        db = Database(db_path)
        try:
            with db.session() as session:
                regions = list(session.execute(
                    select(Announcement.region_province)
                    .join(TopicDocLink, Announcement.id == TopicDocLink.announcement_id)
                    .where(TopicDocLink.topic_id == topic.id, TopicDocLink.status.in_(("auto", "confirmed")))
                    .group_by(Announcement.region_province)
                ).all())
                region_list = [r[0] for r in regions if r[0]]
        finally:
            db.close()
        selected_regions = st.multiselect("地区", region_list, default=region_list, key="cmp_regions")

    # 构建对比矩阵
    db = Database(db_path)
    try:
        with db.session() as session:
            links = list(session.scalars(
                select(TopicDocLink).where(
                    TopicDocLink.topic_id == topic.id,
                    TopicDocLink.status.in_(("auto", "confirmed"))
                )
            ).all())
            ann_ids = [l.announcement_id for l in links]
            if not ann_ids:
                st.info("该主题暂无关联公告。")
                return
            anns = list(session.scalars(select(Announcement).where(Announcement.id.in_(ann_ids))).all())
            extractions = list(session.scalars(
                select(FieldExtraction).where(
                    FieldExtraction.topic_id == topic.id,
                    FieldExtraction.announcement_id.in_(ann_ids)
                )
            ).all())
            field_schema = list(topic.field_schema or [])
            matrix = build_matrix(anns, extractions, field_schema, expected_regions=selected_regions or None)
    finally:
        db.close()

    # 统计条
    counts = matrix.get("counts", {})
    cols = st.columns(5)
    with cols[0]:
        st.metric("一致", counts.get("same", 0))
    with cols[1]:
        st.metric("差异", counts.get("diff", 0), delta=None)
    with cols[2]:
        st.metric("未出台", counts.get("missing", 0))
    with cols[3]:
        st.metric("待确认", counts.get("pending", 0))
    with cols[4]:
        st.metric("已废止", counts.get("revoked", 0))

    # 对比表
    rows = matrix.get("rows", [])
    if not rows:
        st.info("无数据。")
        return

    # 渲染 HTML 表格（六态：diff/same/missing/pending/revoked/extra）
    html = render_matrix_html(matrix, [a.title for a in anns])
    st.markdown(html, unsafe_allow_html=True)

    # 导出
    csv_rows = to_csv_rows(matrix)
    st.download_button(
        "导出 CSV",
        data="\n".join(",".join(str(c) for c in row) for row in csv_rows).encode("utf-8-sig"),
        file_name=f"compare_{selected}.csv",
        mime="text/csv",
    )

def render_matrix_html(matrix: dict, titles: list[str]) -> str:
    """渲染六态对比表 HTML。"""
    rows = matrix.get("rows", [])
    if not rows:
        return "<p>无数据</p>"

    # 表头
    header_cells = "".join(f"<th>{t}</th>" for t in ["字段"] + titles)
    header = f"<tr>{header_cells}</tr>"

    # 表体
    body = []
    for row in rows:
        key = row.get("key", "")
        label = row.get("label", key)
        cells = [f"<td><strong>{label}</strong></td>"]
        for cell in row.get("cells", []):
            state = cell.get("state", "same")
            value = cell.get("value", "")
            if state == "diff":
                cells.append(f'<td class="diff" title="{value}">{value}</td>')
            elif state == "missing":
                cells.append(f'<td class="missing">未出台</td>')
            elif state == "pending":
                cells.append(f'<td class="pending" title="待确认">{value}</td>')
            elif state == "revoked":
                cells.append(f'<td class="revoked" title="已废止">{value}</td>')
            elif state == "extra":
                cells.append(f'<td class="extra" title="附加条件">{value} <span class="badge">附</span></td>')
            else:
                cells.append(f'<td class="same">{value}</td>')
        body.append(f"<tr>{''.join(cells)}</tr>")

    return f"""
    <style>
    .compare-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    .compare-table th, .compare-table td {{ border: 1px solid #ddd; padding: 6px 8px; vertical-align: top; }}
    .compare-table th {{ background: #f5f5f5; font-weight: 600; }}
    .diff {{ background: #fff3cd; color: #856404; font-weight: 500; }}
    .same {{ color: #6c757d; }}
    .missing {{ color: #adb5bd; font-style: italic; }}
    .pending {{ background: #e7f1ff; color: #084298; border: 1px dashed #0d6efd; }}
    .revoked {{ text-decoration: line-through; color: #dc3545; }}
    .extra {{ background: #f3e8ff; color: #6f42c1; }}
    .badge {{ display: inline-block; background: #6f42c1; color: #fff; font-size: 10px; padding: 0 4px; border-radius: 3px; margin-left: 4px; }}
    </style>
    <table class="compare-table">{header}{"".join(body)}</table>
    """
