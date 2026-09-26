# subscription_view.py — 订阅中心
# 订阅表单 + 列表 + 未读通知

import streamlit as st
from sqlalchemy import select
from storage.db import Database
from storage.models import PolicyTopic, TopicSubscription, UserNotice
from compare_digest import list_subscriptions, list_notices, mark_notice_read, mark_all_read, remove_subscription, save_subscription, set_subscription_enabled

def render(db_path: str) -> None:
    st.subheader("订阅中心")

    # 未读角标
    unread = list_notices(db_path, unread_only=True)
    if unread:
        st.sidebar.metric("未读通知", len(unread))

    # 订阅表单
    with st.form("subscribe_form"):
        db = Database(db_path)
        try:
            with db.session() as session:
                topics = list(session.scalars(select(PolicyTopic).where(PolicyTopic.status == "active")).all())
        finally:
            db.close()
        topic_names = [t.name for t in topics]
        selected_topic = st.selectbox("选择主题", topic_names)
        regions_input = st.text_input("关注地区（逗号分隔，如 北京,上海）", value="")
        notice_types = st.multiselect(
            "通知类型",
            ["new_links", "field_changes"],
            default=["new_links", "field_changes"],
        )
        submitted = st.form_submit_button("订阅")
        if submitted and selected_topic:
            topic = next(t for t in topics if t.name == selected_topic)
            regions = [r.strip() for r in regions_input.split(",") if r.strip()]
            save_subscription(db_path, topic.id, regions, notice_types)
            st.success(f"已订阅 {selected_topic}")
            st.rerun()

    # 订阅列表
    st.subheader("我的订阅")
    subs = list_subscriptions(db_path)
    if not subs:
        st.info("暂无订阅。")
    else:
        for sub in subs:
            with st.expander(f"{sub['topic_name']}（{len(sub['regions'])} 个地区）"):
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("启用/禁用", key=f"toggle_{sub['id']}"):
                        set_subscription_enabled(db_path, sub["id"], not sub["enabled"])
                        st.rerun()
                with col2:
                    if st.button("取消订阅", key=f"unsub_{sub['id']}"):
                        remove_subscription(db_path, sub["id"])
                        st.rerun()

    # 通知列表
    st.subheader("通知")
    notices = list_notices(db_path)
    if not notices:
        st.info("暂无通知。")
    else:
        if st.button("全部已读"):
            mark_all_read(db_path)
            st.rerun()
        for notice in notices:
            with st.expander(f"{notice['topic_name']} · {notice['created_at']}", expanded=not notice["read"]):
                st.write(notice["message"])
                if not notice["read"]:
                    if st.button("标为已读", key=f"read_{notice['id']}"):
                        mark_notice_read(db_path, notice["id"])
                        st.rerun()
