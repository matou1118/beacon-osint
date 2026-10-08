"""订阅中心 — topic subscriptions with in-app change notices.

订阅某主题的指定省份（留空=全部地区），勾选关注的变化类型；每日调度
任务把新关联/字段变化写成站内通知，本页负责订阅管理与通知阅读。
"""
from __future__ import annotations

import streamlit as st

from compare_digest import (
    NOTICE_TYPES,
    list_notices,
    list_subscriptions,
    mark_all_read,
    mark_notice_read,
    remove_subscription,
    save_subscription,
    set_subscription_enabled,
    unread_count,
)
from compare_view import list_topics

NOTICE_TYPE_LABEL = {"new_links": "新关联文件", "field_changes": "字段变化", "digest": "每日汇总"}


def render(db_path: str = "data/beacon.db") -> None:
    topics = list_topics(db_path)
    if not topics:
        st.info("暂无对比主题可订阅。先运行 `python seed_compare.py` 生成种子主题。")
        return

    left, right = st.columns([1.1, 1.9])
    with left:
        st.subheader("新建订阅")
        topic_names = [t["name"] for t in topics]
        chosen_topic = st.selectbox("对比主题", topic_names)
        topic = topics[topic_names.index(chosen_topic)]
        all_regions = sorted({d["region"] for d in _regions(db_path, topic["id"])})
        chosen_regions = st.multiselect("关注地区（留空=全部）", all_regions)
        chosen_types = st.multiselect(
            "通知类型",
            list(NOTICE_TYPES),
            default=list(NOTICE_TYPES),
            format_func=lambda t: NOTICE_TYPE_LABEL.get(t, t),
        )
        if st.button("保存订阅", type="primary", width="stretch"):
            sub_id = save_subscription(db_path, topic["id"], chosen_regions, chosen_types)
            st.success(f"已保存订阅 #{sub_id}。")
            st.rerun()

        st.subheader("我的订阅")
        subs = list_subscriptions(db_path)
        if not subs:
            st.caption("还没有订阅。")
        for sub in subs:
            with st.container(border=True):
                state = "🟢" if sub["enabled"] else "⚪"
                regions_text = "、".join(sub["regions"]) or "全部地区"
                types_text = "、".join(NOTICE_TYPE_LABEL.get(t, t) for t in sub["notice_types"]) or "全部类型"
                st.markdown(f"{state} **{sub['topic_name']}**")
                st.caption(f"{regions_text} · {types_text}")
                act1, act2 = st.columns(2)
                with act1:
                    toggle_label = "暂停" if sub["enabled"] else "恢复"
                    if st.button(toggle_label, key=f"tog-{sub['id']}", width="stretch"):
                        set_subscription_enabled(db_path, sub["id"], not sub["enabled"])
                        st.rerun()
                with act2:
                    if st.button("删除", key=f"del-{sub['id']}", width="stretch"):
                        remove_subscription(db_path, sub["id"])
                        st.rerun()

    with right:
        unread = unread_count(db_path)
        head_left, head_right = st.columns([3, 1.2])
        with head_left:
            st.subheader(f"站内通知（未读 {unread}）")
        with head_right:
            if unread and st.button("全部已读", width="stretch"):
                mark_all_read(db_path)
                st.rerun()
        only_unread = st.toggle("只看未读", value=False)
        notices = list_notices(db_path, unread_only=only_unread)
        if not notices:
            st.info("暂无通知。每日任务发现新关联或字段变化时会在这里提醒你。")
        for notice in notices:
            with st.container(border=True):
                dot = "🔵" if not notice["read"] else "⚪"
                st.markdown(f"{dot} {notice['message']}")
                st.caption(f"{NOTICE_TYPE_LABEL.get(notice['notice_type'], notice['notice_type'])} · {notice['created_at'][:16].replace('T', ' ')}")
                if not notice["read"] and st.button("标为已读", key=f"read-{notice['id']}"):
                    mark_notice_read(db_path, notice["id"])
                    st.rerun()


def _regions(db_path: str, topic_id: int) -> list[dict]:
    from compare_view import load_topic_docs

    return load_topic_docs(db_path, topic_id)
