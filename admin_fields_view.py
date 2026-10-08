"""字段复核工作台 — confirm or correct rule-extracted schema fields.

auto 字段直接进入对比表；pending（置信度不足）与人工修正（revised_value）
在这里处理。修正值立即生效于对比工作台，原始提取值永久留痕。
"""
from __future__ import annotations

from typing import Any

import streamlit as st
from sqlalchemy import func, select

from policy_fields import FIELD_SCHEMA
from storage.db import Database
from storage.models import Announcement, FieldExtraction, PolicyTopic

FIELD_LABELS = {spec["field_key"]: spec["label"] for spec in FIELD_SCHEMA}
FIELD_LABELS["key_measures"] = "关键举措"


def field_stats(db_path: str, topic_id: int | None = None) -> dict[str, int]:
    db = Database(db_path)
    try:
        with db.session() as session:
            query = select(FieldExtraction.status, func.count())
            if topic_id is not None:
                query = query.where(FieldExtraction.topic_id == topic_id)
            stats = {status: int(n) for status, n in session.execute(query.group_by(FieldExtraction.status)).all()}
            revised_query = select(func.count()).select_from(FieldExtraction).where(
                FieldExtraction.revised_value.is_not(None),
                FieldExtraction.revised_value != "",
            )
            if topic_id is not None:
                revised_query = revised_query.where(FieldExtraction.topic_id == topic_id)
            stats["revised"] = int(session.scalar(revised_query) or 0)
            return {key: stats.get(key, 0) for key in ("auto", "pending", "revised")}
    finally:
        db.close()


def list_extractions(
    db_path: str,
    status: str | None = None,
    topic_id: int | None = None,
    only_revised: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    db = Database(db_path)
    try:
        with db.session() as session:
            query = (
                select(FieldExtraction, Announcement.title, Announcement.region_province, PolicyTopic.name)
                .join(Announcement, FieldExtraction.announcement_id == Announcement.id)
                .join(PolicyTopic, FieldExtraction.topic_id == PolicyTopic.id)
                .order_by(FieldExtraction.confidence.asc(), FieldExtraction.id.desc())
                .limit(limit)
                .offset(offset)
            )
            if status is not None:
                query = query.where(FieldExtraction.status == status)
            if topic_id is not None:
                query = query.where(FieldExtraction.topic_id == topic_id)
            if only_revised:
                query = query.where(
                    FieldExtraction.revised_value.is_not(None), FieldExtraction.revised_value != ""
                )
            rows = list(session.execute(query).all())
            result: list[dict[str, Any]] = []
            for item, title, region, topic_name in rows:
                result.append(
                    {
                        "id": item.id,
                        "topic_id": item.topic_id,
                        "topic_name": topic_name,
                        "announcement_id": item.announcement_id,
                        "title": title,
                        "region": region or "未知",
                        "field_key": item.field_key,
                        "field_label": FIELD_LABELS.get(item.field_key, item.field_key),
                        "value": item.value,
                        "revised_value": item.revised_value,
                        "confidence": int(item.confidence or 0),
                        "evidence_text": item.evidence_text,
                        "status": item.status,
                    }
                )
            return result
    finally:
        db.close()


def revise_extraction(db_path: str, extraction_id: int, revised_value: str, reviewer: str = "本地管理员") -> bool:
    db = Database(db_path)
    try:
        with db.session() as session:
            item = session.get(FieldExtraction, extraction_id)
            if item is None:
                return False
            item.revised_value = revised_value.strip() or None
            item.reviewed_by = reviewer
            return True
    finally:
        db.close()


def clear_revision(db_path: str, extraction_id: int) -> bool:
    db = Database(db_path)
    try:
        with db.session() as session:
            item = session.get(FieldExtraction, extraction_id)
            if item is None:
                return False
            item.revised_value = None
            return True
    finally:
        db.close()


def count_extractions(
    db_path: str,
    status: str | None = None,
    topic_id: int | None = None,
    only_revised: bool = False,
) -> int:
    """统计符合条件的提取记录总数，用于分页。"""
    db = Database(db_path)
    try:
        with db.session() as session:
            query = select(func.count()).select_from(FieldExtraction)
            if status is not None:
                query = query.where(FieldExtraction.status == status)
            if topic_id is not None:
                query = query.where(FieldExtraction.topic_id == topic_id)
            if only_revised:
                query = query.where(
                    FieldExtraction.revised_value.is_not(None), FieldExtraction.revised_value != ""
                )
            return int(session.scalar(query) or 0)
    finally:
        db.close()


def render(db_path: str = "data/beacon.db") -> None:
    st.caption("原始提取值永久留痕；修正值立即生效于对比工作台。置信度不足的字段会先进入待确认列。")

    topics = [("-- 全部主题 --", None)] + [(t["name"], t["id"]) for t in _all_topics(db_path)]
    choice = st.selectbox("主题筛选", [name for name, _ in topics], format_func=lambda name: name)
    topic_id = dict(topics)[choice]
    stats = field_stats(db_path, topic_id)
    cols = st.columns(3)
    for col, (key, label) in zip(cols, (("auto", "自动提取"), ("pending", "待确认"), ("revised", "已人工修正"))):
        with col:
            st.metric(label, stats[key])

    tab_pending, tab_revised, tab_auto = st.tabs(["待确认", "已修正", "自动提取"])
    PAGE_SIZE = 200
    for tab, status, only_revised in ((tab_pending, "pending", False), (tab_revised, None, True), (tab_auto, "auto", False)):
        with tab:
            total_count = count_extractions(db_path, status=status, topic_id=topic_id, only_revised=only_revised)
            if total_count == 0:
                st.info("该分类暂无字段。")
                continue
            # 分页控件
            num_pages = (total_count + PAGE_SIZE - 1) // PAGE_SIZE
            if num_pages > 1:
                page = st.number_input(
                    f"页码（共 {total_count} 条，{num_pages} 页）",
                    min_value=1, max_value=num_pages, value=1, step=1,
                    key=f"page-{status or 'revised'}-{topic_id}",
                )
                offset = (int(page) - 1) * PAGE_SIZE
                st.caption(f"显示第 {offset + 1}–{min(offset + PAGE_SIZE, total_count)} 条，共 {total_count} 条")
            else:
                offset = 0
                st.caption(f"共 {total_count} 条")
            items = list_extractions(db_path, status=status, topic_id=topic_id, only_revised=only_revised,
                                     limit=PAGE_SIZE, offset=offset)
            for item in items:
                with st.container(border=True):
                    effective = item["revised_value"] or item["value"] or "（空）"
                    st.markdown(
                        f"**{item['field_label']}** · {item['region']} · 置信度 {item['confidence']}"
                        + (" · ✍️ 已修正" if item["revised_value"] else "")
                    )
                    st.caption(f"{item['title']}")
                    st.markdown(f"当前生效值：{effective}")
                    st.caption(f"依据：{item['evidence_text'] or '无'}")
                    left, mid, right = st.columns([3, 1.2, 1.2])
                    with left:
                        new_value = st.text_input(
                            "修正值（留空即清除修正）",
                            value=item["revised_value"] or "",
                            key=f"rev-{item['id']}",
                        )
                    with mid:
                        if st.button("保存修正", key=f"save-{item['id']}", width="stretch"):
                            revise_extraction(db_path, item["id"], new_value)
                            st.success("已保存。")
                            st.rerun()
                    with right:
                        if item["revised_value"] and st.button("撤销修正", key=f"undo-{item['id']}", width="stretch"):
                            clear_revision(db_path, item["id"])
                            st.info("已撤销。")
                            st.rerun()


def _all_topics(db_path: str) -> list[dict[str, Any]]:
    from compare_view import list_topics

    return list_topics(db_path)
