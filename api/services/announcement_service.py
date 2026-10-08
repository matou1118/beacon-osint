from __future__ import annotations

import csv
import io
import os
import time
from typing import Any, Mapping

from sqlalchemy import func, or_, select
from sqlalchemy.orm import defer

from storage.db import Database
from storage.models import Announcement, UserSubscription
from privacy import redact_sensitive
from rules.engine import hard_filter_passes, text_matches_keywords

from .match_service import MatchService


def _values(value: str | None) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _record(row: Announcement) -> dict[str, Any]:
    extra = dict(row.extra or {})
    return {
        "id": str(row.id),
        "title": row.title,
        "issuing_body": extra.get("issuing_body") or row.source_id,
        "gov_level": row.gov_level,
        "region": row.region_county or row.region_city or row.region_province,
        "region_province": row.region_province,
        "region_city": row.region_city,
        "region_county": row.region_county,
        "category_l1": row.category_l1,
        "category_l2": row.category_l2,
        "target_group": row.target_group,
        "target_industry": row.target_industry,
        "industry_focus": bool(row.industry_focus),
        "industry_tags": list(row.industry_tags or []),
        "amount": row.amount,
        "deadline": row.deadline,
        "key_points": [redact_sensitive(item) for item in (row.key_points or [])],
        "source_url": row.source_url,
        "publish_date": row.publish_date,
        "media_type": row.media_type,
        "media_tags": list(row.media_tags or []),
        "media_cluster_id": row.media_cluster_id,
        "media_heat": row.media_heat,
        "corroboration_status": row.corroboration_status,
        "corroboration_count": row.corroboration_count,
        "corroboration_reason": row.corroboration_reason,
        "corroborating_sources": list(row.corroborating_sources or []),
    }


def _subscription_profile(row: UserSubscription) -> dict[str, Any]:
    return {
        "levels": list(row.gov_levels or []),
        "region": row.region_county or row.region_city or row.region_province,
        "categories": list(row.categories or []),
        "target_group": row.target_group,
        "target_industry": row.target_industry,
        "keywords": list(row.keywords or []),
        "exclude_review": True,
    }


def _filtered_scope(
    db: Database,
    user_id: str | None,
    gov_level: str | None,
    region: str | None,
    category: str | None,
    category_l2: str | None,
    target_group: str | None,
    target_industry: str | None,
    keywords: list[str] | None,
    subscription_id: int | None,
) -> tuple[list[Any], UserSubscription | None, dict[str, Any], list[str]]:
    """SQL 条件 + 订阅解析 + 评分 profile —— 取数方式（分页/全量）由调用方决定。

    列表只取当前页，统计/导出要全量字典，但筛选口径必须同一份，所以条件构建只写这一遍。
    """
    levels = _values(gov_level)
    categories = _values(category)
    # 搜索词按空白拆开：「养老 补贴」= 两个词，命中任一即保留。
    terms = [part for item in (keywords or []) for part in str(item).split() if part.strip()]
    with db.session() as session:
        subscription = None
        if subscription_id is not None:
            if not user_id:
                raise PermissionError("subscription_id requires authentication")
            subscription = session.scalar(select(UserSubscription).where(UserSubscription.id == subscription_id, UserSubscription.user_id == user_id))
            if subscription is None:
                raise LookupError("subscription not found")
        conditions: list[Any] = [Announcement.needs_review.is_(False), Announcement.archived_at.is_(None)]
        if subscription is not None:
            levels = list(subscription.gov_levels or [])
            categories = list(subscription.categories or [])
            region = subscription.region_county or subscription.region_city or subscription.region_province
        if levels:
            conditions.append(Announcement.gov_level.in_(levels))
        if categories:
            conditions.append(Announcement.category_l1.in_(categories))
        industries = _values(category_l2)
        if industries:
            conditions.append(Announcement.category_l2.in_(industries))
        if region:
            pattern = f"%{region}%"
            conditions.append(or_(Announcement.region_province.ilike(pattern), Announcement.region_city.ilike(pattern), Announcement.region_county.ilike(pattern)))
        # 人群/行业和关键词一样，从前只进评分 profile —— 检索页勾了等于没勾。
        if target_group and target_group.strip():
            conditions.append(Announcement.target_group.ilike(f"%{target_group.strip()}%"))
        if target_industry and target_industry.strip():
            conditions.append(Announcement.target_industry.ilike(f"%{target_industry.strip()}%"))
    profile_inputs = {
        "levels": levels,
        "region": region,
        "categories": categories,
        "target_group": target_group,
        "target_industry": target_industry,
        "keywords": terms,
        "exclude_review": True,
    }
    return conditions, subscription, profile_inputs, terms


def _load_filtered(
    db: Database,
    user_id: str | None,
    gov_level: str | None,
    region: str | None,
    category: str | None,
    category_l2: str | None,
    target_group: str | None,
    target_industry: str | None,
    keywords: list[str] | None,
    subscription_id: int | None,
) -> tuple[list[dict[str, Any]], UserSubscription | None, dict[str, Any]]:
    """全量字典取数 —— 统计/导出用；列表不走这里，列表走 SQL 分页。"""
    conditions, subscription, profile_inputs, terms = _filtered_scope(
        db, user_id, gov_level, region, category, category_l2, target_group, target_industry, keywords, subscription_id
    )
    # content 是每行数 KB 的大列，这条全量路径全程没人读它
    # （关键词/评分走的是 record 字典，本来就没有 content）—— defer 掉，SQL 减半
    with db.session() as session:
        rows = session.scalars(
            select(Announcement)
            .where(*conditions)
            .order_by(Announcement.publish_date.desc(), Announcement.id.desc())
            .options(defer(Announcement.content))
        ).all()
        records = [_record(row) for row in rows]
    # 关键词只参与评分是不够的：搜索页要的是「只看命中的」。
    # 过滤放在取数之后、评分之前，与规则引擎同一套文本口径。
    if terms:
        records = [record for record in records if text_matches_keywords(record, terms)]
    return records, subscription, profile_inputs


# 关键词扫描阶段只需要的列：文本（title/分类，content 从来不在 record 字典里）
# + hard_filter 判定字段 —— 扫瘦列而不是整行，是带关键词路径的主要开销来源。
_SCAN_COLUMNS = [
    Announcement.id,
    Announcement.publish_date,
    Announcement.title,
    Announcement.category_l1,
    Announcement.category_l2,
    Announcement.gov_level,
    Announcement.media_type,
    Announcement.region_province,
    Announcement.region_city,
    Announcement.region_county,
    Announcement.industry_keys,
    Announcement.industry_focus,
    Announcement.admission_sub_category,
    Announcement.institution_type,
    Announcement.admission_type,
    Announcement.employment_guarantee,
    Announcement.needs_review,
]


def query_announcements(
    db: Database,
    match_service: MatchService,
    user_id: str | None,
    gov_level: str | None,
    region: str | None,
    category: str | None,
    category_l2: str | None,
    target_group: str | None,
    target_industry: str | None,
    keywords: list[str] | None,
    subscription_id: int | None,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    conditions, subscription, profile_inputs, terms = _filtered_scope(
        db, user_id, gov_level, region, category, category_l2, target_group, target_industry, keywords, subscription_id
    )
    profile = _subscription_profile(subscription) if subscription is not None else profile_inputs
    start = (page - 1) * page_size
    with db.session() as session:
        if terms:
            # 关键词过滤必须扫全量，但只扫瘦列；命中 hard_filter 后得到有序 id 序列，
            # 再按当前页这 40 个 id 取整行。评分同样只跑这 40 条（排序/分页不看分）。
            scan = session.execute(
                select(*_SCAN_COLUMNS)
                .where(*conditions)
                .order_by(Announcement.publish_date.desc(), Announcement.id.desc())
            ).all()
            ids: list[int] = []
            for row in scan:
                slim_record = dict(row._mapping)
                if text_matches_keywords(slim_record, terms) and hard_filter_passes(slim_record, profile, match_service.config):
                    ids.append(row.id)
            total = len(ids)
            page_ids = ids[start : start + page_size]
            page_rows = (
                session.execute(select(Announcement.__table__).where(Announcement.id.in_(page_ids))).all() if page_ids else []
            )
            by_id = {row.id: row for row in page_rows}
            records = [_record(by_id[identifier]) for identifier in page_ids]
        else:
            # 无关键词的浏览：COUNT + LIMIT 一页，不再全库物化 1.3 万行 —— 列表快慢的分水岭。
            # 这批 profile 下 hard_filter 与 SQL 条件等价（层级/分类/地域/已审核 SQL 都已过滤），
            # 所以总数直接用 COUNT；下面对本页的复核只是保险。
            total = int(session.scalar(select(func.count(Announcement.id)).where(*conditions)) or 0)
            rows = session.execute(
                select(Announcement.__table__)
                .where(*conditions)
                .order_by(Announcement.publish_date.desc(), Announcement.id.desc())
                .limit(page_size)
                .offset(start)
            ).all()
            records = []
            for row in rows:
                record = _record(row)
                if hard_filter_passes(record, profile, match_service.config):
                    records.append(record)
    items: list[dict[str, Any]] = []
    for record in records:
        matched = match_service.match(record, profile)
        if matched is not None:
            items.append(matched)
    return {"total": total, "page": page, "page_size": page_size, "items": items}


def _count_by(records: list[dict[str, Any]], key: str, *, limit: int | None = None) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for record in records:
        value = record.get(key) or ""
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    rows = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    if limit is not None:
        rows = rows[:limit]
    return [{"name": name, "value": value} for name, value in rows]


# 全量聚合是全表扫描（~5s），同一套筛选在短窗口内直接复用结果。
# 默认关闭（0），启动时设 BEACON_STATS_CACHE_SECONDS>0 才生效 —— 测试不设就不受缓存影响。
_STATS_TTL = float(os.getenv("BEACON_STATS_CACHE_SECONDS", "0"))
_stats_cache: dict[tuple, tuple[float, dict[str, Any]]] = {}


def stats_announcements(
    db: Database,
    user_id: str | None,
    gov_level: str | None,
    region: str | None,
    category: str | None,
    category_l2: str | None,
    target_group: str | None,
    target_industry: str | None,
    keywords: list[str] | None,
) -> dict[str, Any]:
    """当前筛选条件下的全量汇总 —— 检索页图表用，只翻一页画不出全库分布。"""
    cache_key = (user_id, gov_level, region, category, category_l2, target_group, target_industry, tuple(keywords or ()))
    if _STATS_TTL > 0:
        hit = _stats_cache.get(cache_key)
        if hit is not None and time.monotonic() - hit[0] < _STATS_TTL:
            return hit[1]
    records, _subscription, _profile = _load_filtered(
        db, user_id, gov_level, region, category, category_l2, target_group, target_industry, keywords, None
    )
    months: dict[str, int] = {}
    for record in records:
        month = str(record.get("publish_date") or "")[:7]
        if len(month) == 7:
            months[month] = months.get(month, 0) + 1
    result = {
        "total": len(records),
        "category_l1": _count_by(records, "category_l1"),
        "category_l2": _count_by(records, "category_l2", limit=12),
        "gov_level": _count_by(records, "gov_level"),
        "province": _count_by(records, "region_province", limit=10),
        # 趋势要按时间轴，不按热度
        "month": [{"name": name, "value": months[name]} for name in sorted(months)],
    }
    if _STATS_TTL > 0:
        if len(_stats_cache) >= 64:
            _stats_cache.clear()  # ponytail: 全清而非 LRU —— key 数受筛选组合限制，永远很小
        _stats_cache[cache_key] = (time.monotonic(), result)
    return result


_EXPORT_COLUMNS = [
    ("标题", "title"),
    ("行政层级", "gov_level"),
    ("省份", "region_province"),
    ("城市", "region_city"),
    ("区县", "region_county"),
    ("一级分类", "category_l1"),
    ("二级分类", "category_l2"),
    ("发布日期", "publish_date"),
    ("截止日期", "deadline"),
    ("金额", "amount"),
    ("签发机构", "issuing_body"),
    ("来源链接", "source_url"),
]


def _csv_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    # CSV 公式注入：以 =+-@ 开头的单元格在 Excel 里会被当公式执行，数据来自抓取不可信
    return f"'{text}" if text[:1] in "=+-@" else text


def export_announcements_csv(
    db: Database,
    user_id: str | None,
    gov_level: str | None,
    region: str | None,
    category: str | None,
    category_l2: str | None,
    target_group: str | None,
    target_industry: str | None,
    keywords: list[str] | None,
) -> str:
    """当前筛选的全量 CSV —— 检索页「导出」；UTF-8 BOM 让 Excel 直接认中文。"""
    records, _subscription, _profile = _load_filtered(
        db, user_id, gov_level, region, category, category_l2, target_group, target_industry, keywords, None
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([header for header, _ in _EXPORT_COLUMNS])
    for record in records:
        writer.writerow([_csv_cell(record.get(key)) for _, key in _EXPORT_COLUMNS])
    return "\ufeff" + buffer.getvalue()


def query_announcement_detail(db: Database, announcement_id: str, include_review: bool = False) -> dict[str, Any]:
    try:
        identifier = int(announcement_id)
    except ValueError as exc:
        raise LookupError("announcement not found") from exc
    with db.session() as session:
        row = session.get(Announcement, identifier)
        if row is None or (row.needs_review and not include_review) or row.archived_at is not None:
            raise LookupError("announcement not found")
        result = _record(row)
        result["raw_text"] = redact_sensitive(row.content)
        result["content_hash"] = row.content_hash
        result["needs_review"] = bool(row.needs_review)
        return result


def query_regions(db: Database) -> dict[str, Any]:
    with db.session() as session:
        provinces = set(session.scalars(select(Announcement.region_province).where(Announcement.region_province.is_not(None), Announcement.region_province != "")).all())
        cities: dict[str, set[str]] = {}
        counties: dict[str, set[str]] = {}
        for province, city, county in session.execute(select(Announcement.region_province, Announcement.region_city, Announcement.region_county)).all():
            if province:
                cities.setdefault(str(province), set())
                if city:
                    cities[str(province)].add(str(city))
            if city:
                counties.setdefault(str(city), set())
                if county:
                    counties[str(city)].add(str(county))
    return {
        "provinces": sorted(provinces),
        "cities": {key: sorted(value) for key, value in sorted(cities.items())},
        "counties": {key: sorted(value) for key, value in sorted(counties.items())},
    }
