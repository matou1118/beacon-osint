# admin_fields_view.py — 字段复核
# 待复核字段 + 原文摘录 + 修正提交

import streamlit as st
from sqlalchemy import select
from storage.db import Database
from storage.models import Announcement, FieldExtraction, PolicyTopic
from policy_fields import FIELD_SCHEMA

FIELD_LABELS = {spec["field_key"]: spec["label"] for spec in FIELD_SCHEMA}

def render(db_path: str) -> None:
    db = Database(db_path)
    try:
        with db.session() as session:
            fields = list(session.execute(
                select(FieldExtraction, Announcement.title, PolicyTopic.name)
                .join(Announcement, FieldExtraction.announcement_id == Announcement.id)
                .join(PolicyTopic, FieldExtraction.topic_id == PolicyTopic.id)
                .where(FieldExtraction.status.in_(("auto", "pending")))
                .order_by(FieldExtraction.confidence.asc())
            ).all())
    finally:
        db.close()

    st.subheader("待复核字段")
    if not fields:
        st.info("无待复核字段。")
        return

    for field, title, topic_name in fields:
        label = FIELD_LABELS.get(field.field_key, field.field_key)
        with st.expander(f"{topic_name} · {label}（{title}）"):
            st.write(f"**提取值：** {field.value or '(空)'}")
            st.write(f"**置信度：** {field.confidence}")
            if field.evidence_text:
                st.write(f"**原文摘录：** {field.evidence_text}")
            revised = st.text_input("修正值", value=field.revised_value or field.value or "", key=f"revise_{field.id}")
            if st.button("提交修正", key=f"submit_{field.id}"):
                db = Database(db_path)
                try:
                    with db.session() as s:
                        f = s.get(FieldExtraction, field.id)
                        if f:
                            f.revised_value = revised
                            f.status = "revised"
                    st.rerun()
                finally:
                    db.close()
