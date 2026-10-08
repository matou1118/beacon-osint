"""成刊视图 — 日报 / 周报 / 月报的阅读与导出。

数据来自 ``data/publications/*.json``，由 ``lighthouse/publish.py`` 生成，
每轮采集有新入库时自动出一份（``lighthouse/overnight.py:publish_editions``）。

**这一页只读文件、不查库。** 刊是某一期的时间切片，读库拿到的是「现在」，
两者不是一回事：09-30 的日报应该永远只包含 09-30 那天的东西，不受之后
补采的历史数据影响。要看实时数据请去「公告检索」。

于是也提供了一个「即时预览」入口：直接对今天现算一份，不落盘，
用来在下一期出刊前看看今天有什么。两者在界面上明确分开，不混。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import streamlit as st

from lighthouse.publish import EDITION_LABELS, OUT_DIR, build_edition

EDITION_ORDER = ("daily", "weekly", "monthly")


def _load(edition: str) -> dict:
    """列出某一类的所有刊，新的在前。"""
    if not OUT_DIR.exists():
        return {}
    found: dict[str, dict] = {}
    for path in OUT_DIR.glob(f"{edition}-*.json"):
        try:
            found[path.stem[len(edition) + 1:]] = json.loads(
                path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return dict(sorted(found.items(), reverse=True))


def _metric_row(payload: dict) -> None:
    stats = payload.get("stats") or {}
    cells = [
        ("收录", stats.get("total", 0), "条"),
        ("多源事件", stats.get("events", 0), "个"),
        ("交叉印证", stats.get("corroborated", 0), "条"),
        ("覆盖地区", stats.get("regions", 0), "个"),
        ("分类", stats.get("categories", 0), "个"),
    ]
    html = "".join(
        f"<div class='metric'><div class='metric-label'>{label}</div>"
        f"<div class='metric-value'>{value}</div>"
        f"<div class='metric-note'>{unit}</div></div>"
        for label, value, unit in cells)
    st.markdown(f"<div class='metric-row'>{html}</div>", unsafe_allow_html=True)


def _item_card(item: dict) -> None:
    """一个条目。多源事件会把所有来源列出来，单条只给原文链接。"""
    is_event = bool(item.get("is_event"))
    title = str(item.get("title") or "").strip()
    with st.container(border=True):
        badge = "🔥 " if is_event else ""
        corrob = int((item.get("corroboration") or {}).get("count") or 0)
        mark = f"　`{corrob} 源印证`" if corrob >= 2 else ""
        st.markdown(f"{badge}**{title}**{mark}")
        where = " · ".join(x for x in (
            item.get("publish_date"), item.get("issuing_body"),
            (item.get("region") or {}).get("province"),
            (item.get("region") or {}).get("city"),
            item.get("gov_level"),
        ) if x)
        if where:
            st.caption(where)
        if item.get("is_event"):
            st.caption(f"热度 {item.get('heat_score', 0)}"
                       + (f" · 主题词 {'、'.join(item.get('terms') or [])}" if item.get("terms") else ""))
        if item.get("summary"):
            st.write(item["summary"])
        if item.get("amount") or item.get("deadline"):
            extra = " · ".join(x for x in (
                f"金额 {item['amount']}" if item.get("amount") else "",
                f"截止 {item['deadline']}" if item.get("deadline") else "",
            ) if x)
            if extra:
                st.caption(extra)
        sources = item.get("sources") or []
        if is_event and sources:
            cols = st.columns(min(len(sources), 4))
            for col, source in zip(cols, sources):
                with col:
                    label = str(source.get("issuing_body") or source.get("source_id") or "")
                    st.markdown(f"[{label}]({source.get('url')})", help=str(source.get("url")))
        else:
            url = (item.get("source") or {}).get("url")
            if url:
                st.markdown(f"[原文]({url})")


def _render_payload(payload: dict) -> None:
    period = (payload.get("period") or {}).get("label", "")
    st.markdown(f"### {payload.get('edition_label', '')} · {period}")
    st.markdown(f"> {payload.get('lead', '')}", unsafe_allow_html=False)
    _metric_row(payload)

    sections = payload.get("sections") or []
    if not sections:
        st.info("本期刊期内没有收录。")
        return

    by_category = {s.get("category_l1", "其他"): len(s.get("items") or [])
                   for s in sections}
    focus = st.multiselect("只看这些分类", sorted(by_category),
                           default=[], placeholder="不选 = 全部")
    search = st.text_input("在标题里找", "", placeholder="关键词，留空不过滤")
    per_section = st.slider("每节最多显示", 5, 60, 20, 5)

    for section in sections:
        name = section.get("category_l1", "其他")
        items = section.get("items") or []
        if focus and name not in focus:
            continue
        if search:
            items = [i for i in items if search in str(i.get("title") or "")]
            if not items:
                continue
        with st.expander(f"{name}（{len(items)} 条）", expanded=len(sections) <= 3):
            for item in items[:per_section]:
                _item_card(item)
            if len(items) > per_section:
                st.caption(f"本节共 {len(items)} 条，只显示前 {per_section} 条。"
                           f"完整内容见同名 .json。")


def _live_preview(edition: str) -> None:
    """即时预览：不落盘，直接对今天现算。"""
    try:
        payload = build_edition(edition, date.today())
    except Exception as exc:                                  # noqa: BLE001
        st.error(f"试算失败：{type(exc).__name__}: {exc}")
        return
    st.caption(f"对 {date.today():%Y-%m-%d} 现算，未落盘。下一期出刊后本预览会被正式刊取代。")
    _render_payload(payload)


def render(db_path: str = "data/beacon.db") -> None:
    st.markdown("<div class='section-label'>成刊</div>", unsafe_allow_html=True)
    st.markdown("### 日报 · 周报 · 月报")

    bar_left, bar_mid, bar_right = st.columns([1.4, 1.4, 1.2])
    with bar_left:
        edition = st.segmented_control(
            "成刊类型", list(EDITION_ORDER), default="daily",
            format_func=lambda k: EDITION_LABELS[k], key="pub_edition",
            label_visibility="collapsed")
        edition = edition or "daily"
    available = _load(edition)
    with bar_mid:
        chosen = st.selectbox(
            "选择期号", list(available) or ["（无）"],
            format_func=lambda k: available.get(k, {}).get("period", {}).get("label", k)
            if available else "（无）",
            key="pub_issue", label_visibility="collapsed")
    with bar_right:
        preview = st.toggle("即时预览", value=False, key="pub_preview",
                            help="对今天现算一份，不落盘")

    if preview:
        st.info("即时预览模式：不读已出刊的文件，直接按当前数据算。")
        _live_preview(edition)
        return

    if not available:
        st.markdown(
            "<div class='empty' style='padding:2rem;text-align:center'>还没有出刊</div>",
            unsafe_allow_html=True)
        st.markdown("""
        <div style='max-width:520px;margin:1rem auto;padding:1.5rem;border:1px solid var(--line);border-radius:.65rem;background:var(--panel-glass)'>
        <h4 style='margin:0 0 .8rem'>刊是怎么出的？</h4>
        <ol style='margin:0;padding-left:1.2rem;line-height:1.8;color:var(--ink);opacity:.8'>
        <li>每轮采集<strong>有新入库</strong>时，出当天的日报</li>
        <li>逢<strong>周一</strong>出上一周的周报，逢<strong>每月 1 号</strong>出上月月报</li>
        <li>零入库的轮次不重出——同一份文件改 14 次时间戳没有意义</li>
        </ol>
        <div style='margin-top:1rem;padding:.6rem;border-radius:.4rem;background:rgba(var(--accent_rgb),0.06);font-size:.85rem'>
        <strong>摘要零生成：</strong>每条摘要都是原文原句照抄，导语是固定模板拼真实统计，
        没有任何一个字由模型写。
        </div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("立即出一份日报", type="primary", width="stretch",
                     icon=":material/newspaper:"):
            try:
                payload = build_edition("daily", date.today())
            except Exception as exc:                          # noqa: BLE001
                st.error(f"出刊失败：{type(exc).__name__}: {exc}")
            else:
                from lighthouse.publish import write_edition

                paths = write_edition(payload)
                st.success(f"已出 {payload['stats']['total']} 条。")
                st.caption(paths["markdown"])
                st.rerun()
        return

    payload = available.get(chosen)
    if not payload:
        st.info("该期号读不出来，换一期试试。")
        return
    _render_payload(payload)

    st.divider()
    stem = f"{edition}-{payload.get('ref_date')}"
    dl1, dl2 = st.columns(2)
    with dl1:
        st.download_button(
            "下载 Markdown", (OUT_DIR / f"{stem}.md").read_text(encoding="utf-8"),
            file_name=f"{stem}.md", mime="text/markdown", width="stretch",
            icon=":material/download:")
    with dl2:
        st.download_button(
            "下载 JSON（完整数据）",
            (OUT_DIR / f"{stem}.json").read_text(encoding="utf-8"),
            file_name=f"{stem}.json", mime="application/json", width="stretch",
            icon=":material/data_object:")
    st.caption(f"生成于 {payload.get('generated_at', '')[:19].replace('T', ' ')}　"
               f"JSON 是机器契约，条目完整不截断；网页为阅读方便按节限量展示。")


__all__ = ["render", "EDITION_ORDER"]
