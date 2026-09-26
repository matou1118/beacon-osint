# admin_relations_view.py — 关联确认
# pending 列表 + 置信度条 + 确认/驳回

import streamlit as st
from sqlalchemy import select
from storage.db import Database
from storage.models import Announcement, PolicyTopic, TopicDocLink

def render(db_path: str) -> None:
    db = Database(db_path)
    try:
        with db.session() as session:
            topics = list(session.scalars(select(PolicyTopic).where(PolicyTopic.status == "active")).all())
            links = list(session.execute(
                select(TopicDocLink, Announcement.title, PolicyTopic.name)
                .join(Announcement, TopicDocLink.announcement_id == Announcement.id)
                .join(PolicyTopic, TopicDocLink.topic_id == PolicyTopic.id)
                .where(TopicDocLink.status == "pending")
                .order_by(TopicDocLink.confidence.desc())
            ).all())
    finally:
        db.close()

    st.subheader("待确认关联")
    if not links:
        st.info("无待审关联。")
        return

    for link, title, topic_name in links:
        with st.expander(f"{topic_name} ↔ {title}（置信度 {link.confidence}）"):
            col1, col2, col3 = st.columns(3)
            with col1:
                st.write(f"**政府层级：** {link.gov_level or '未知'}")
            with col2:
                st.write(f"**证据：** {link.evidence or {}}")
            with col3:
                if st.button("确认", key=f"confirm_{link.id}"):
                    db = Database(db_path)
                    try:
                        with db.session() as s:
                            l = s.get(TopicDocLink, link.id)
                            if l:
                                l.status = "confirmed"
                        st.rerun()
                    finally:
                        db.close()
                if st.button("驳回", key=f"reject_{link.id}"):
                    db = Database(db_path)
                    try:
                        with db.session() as s:
                            l = s.get(TopicDocLink, link.id)
                            if l:
                                l.status = "rejected"
                        st.rerun()
                    finally:
                        db.close()
