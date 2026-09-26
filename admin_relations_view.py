"""关联确认工作台 — review topic↔announcement links queued by the scorer.

待审（pending）链接逐条给出标题/地区/层级/置信度/命中证据，确认后自动
触发规则字段提取；驳回则永久排除（回填不会复活人工结论）。
"""
from __future__ import annotations

from typing import Any

import streamlit as st
from sqlalchemy import func, select

from seed_compare import extract_one
from storage.db import Database
from storage.models import Announcement, PolicyTopic, TopicDocLink

STATUS_LABEL = {"auto": "自动关联", "pending": "待审", "approved": "已确认", "rejected": "已驳回"}


def relation_stats(db_path: str) -> dict[str, int]:
    db = Database(db_path)
    try:
        with db.session() as session:
            rows = session.execute(
                select(TopicDocLink.status, func.count()).group_by(TopicDocLink.status)
            ).all()
            stats = {status: int(n) for status, n in rows}
            return {key: stats.get(key, 0) for key in ("auto", "pending", "approved", "rejected")}
    finally:
        db.close()


def list_links_by_status(db_path: str, status: str, topic_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
    db = Database(db_path)
    try:
        with db.session() as session:
            query = (
                select(TopicDocLink, Announcement, PolicyTopic.name)
                .join(Announcement, TopicDocLink.announcement_id == Announcement.id)
                .join(PolicyTopic, TopicDocLink.topic_id == PolicyTopic.id)
                .where(TopicDocLink.status == status)
                .order_by(TopicDocLink.confidence.desc())
                .limit(limit)
            )
            if topic_id is not None:
                query = query.where(TopicDocLink.topic_id == topic_id)
            rows = list(session.execute(query).all())
            result: list[dict[str, Any]] = []
            for link, ann, topic_name in rows:
                result.append(
                    {
                        "link_id": link.id,
                        "topic_id": link.topic_id,
                        "topic_name": topic_name,
                        "announcement_id": ann.id,
                        "title": ann.title,
                        "region": ann.region_province or "未知",
                        "gov_level": ann.gov_level or "未知",
                        "confidence": int(link.confidence or 0),
                        "evidence": dict(link.evidence or {}),
                        "publish_date": ann.publish_date,
                        "url": ann.source_url,
                    }
                )
            return result
    finally:
        db.close()


def decide_link(db_path: str, link_id: int, decision: str, reviewer: str = "本地管理员") -> int:
    """Set a link's reviewed status; approving triggers field extraction. Returns fields written."""
    if decision not in ("approved", "rejected"):
        raise ValueError(f"unknown decision: {decision}")
    db = Database(db_path)
    try:
        with db.session() as session:
            link = session.get(TopicDocLink, link_id)
            if link is None:
                return 0
            link.status = decision
            link.reviewed_by = reviewer
        if decision == "approved":
            return extract_one(db, link.announcement_id, link.topic_id)
        return 0
    finally:
        db.close()


def bulk_decide(db_path: str, decision: str, topic_id: int | None = None, reviewer: str = "本地管理员") -> int:
    """Apply one decision to every pending link (optionally scoped to a topic)."""
    db = Database(db_path)
    try:
        with db.session() as session:
            query = select(TopicDocLink).where(TopicDocLink.status == "pending")
            if topic_id is not None:
                query = query.where(TopicDocLink.topic_id == topic_id)
            links = list(session.scalars(query).all())
            pairs = [(link.announcement_id, link.topic_id) for link in links]
            for link in links:
                link.status = decision
                link.reviewed_by = reviewer
        written = 0
        if decision == "approved":
            for announcement_id, topic_id_ in pairs:
                written += extract_one(db, announcement_id, topic_id_)
        return written
    finally:
        db.close()


def _evidence_text(item: dict[str, Any]) -> str:
    evidence = item["evidence"]
    hits = list(evidence.get("title_hits") or []) + list(evidence.get("body_hits") or [])
    if not hits:
        return "无关键词命中记录"
    return "命中：" + "、".join(dict.fromkeys(hits))


def render(db_path: str = "data/beacon.db") -> None:
    st.caption("阈值规则：≥90 自动关联 · 60–90 进待审 · <60 丢弃。人工结论优先，回填不会覆盖。")

    stats = relation_stats(db_path)
    cols = st.columns(4)
    for col, (key, label) in zip(cols, (("pending", "待审"), ("auto", "自动关联"), ("approved", "已确认"), ("rejected", "已驳回"))):
        with col:
            st.metric(label, stats[key])

    topics = [("-- 全部主题 --", None)] + [(t["name"], t["id"]) for t in _all_topics(db_path)]
    # st.selectbox returns the chosen option, not a pair. Unpacking it into
    # (chosen, topic_id) raised "too many values to unpack" and took the whole
    # 关联确认 view down with it.
    chosen = st.selectbox("主题筛选", [name for name, _ in topics])
    topic_id = dict(topics)[chosen]

    tab_pending, tab_approved, tab_rejected = st.tabs(["待审确认", "已确认", "已驳回"])
    for tab, status, decision in (
        (tab_pending, "pending", "approved"),
        (tab_approved, "approved", "rejected"),
        (tab_rejected, "rejected", "approved"),
    ):
        with tab:
            items = list_links_by_status(db_path, status, topic_id)
            if not items:
                st.info("该分类暂无链接。")
                continue
            if status == "pending":
                if st.button("全部确认（当前筛选）", type="primary"):
                    written = bulk_decide(db_path, "approved", topic_id)
                    st.success(f"已批量确认，新提取字段 {written} 条。")
                    st.rerun()
            for item in items:
                with st.container(border=True):
                    left, right = st.columns([4.2, 1.6])
                    with left:
                        st.markdown(f"**{item['title']}**")
                        st.caption(
                            f"{item['topic_name']} · {item['region']} · {item['gov_level']} · "
                            f"置信度 {item['confidence']} · {item['publish_date'] or '日期未知'}"
                        )
                        st.caption(_evidence_text(item))
                    with right:
                        approve = st.button("✅ 确认", key=f"ok-{item['link_id']}", use_container_width=True)
                        reject = st.button("❌ 驳回", key=f"no-{item['link_id']}", use_container_width=True)
                        if item.get("url"):
                            st.link_button("查看原文", item["url"], use_container_width=True)
                    if approve:
                        written = decide_link(db_path, item["link_id"], "approved")
                        st.success(f"已确认，新提取字段 {written} 条。")
                        st.rerun()
                    if reject:
                        decide_link(db_path, item["link_id"], "rejected")
                        st.warning("已驳回。")
                        st.rerun()


def _all_topics(db_path: str) -> list[dict[str, Any]]:
    from compare_view import list_topics

    return list_topics(db_path)
