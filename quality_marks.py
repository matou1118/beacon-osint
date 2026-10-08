"""质量标记与数据新鲜度 —— 需求书 §5.1 / §5.2 的接口实现。

本轮先把接口按需求书签名落在视图侧，业务流方函数到达后替换同名实现即可。
遵循 §2.1 的红线：只调用 `compare_topics` 的既有判定函数与 `Database` 类，
不新写针对 beacon.db 的 SQL。

对外接口：
    data_freshness(db_path="data/beacon.db") -> dict
    quality_flags(db_path="data/beacon.db", filter_by=None, region=None) -> dict

另暴露给视图复用的纯函数（对已加载的 record dict 直接计算，避免二次读库）：
    flag_record(record) -> list[str]
    flag_counts(records) -> dict[str, int]
    filter_records(records, flags=None, region=None) -> list
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable

from compare_topics import fetch_announcements, is_portal_page, parse_amount

# MAX_SUMMARY_CHARS 在管线里是死常量（从未生效），这里按需求书 §1.3 的 600 字口径判定。
MAX_SUMMARY_CHARS = 600
JUNK_CATEGORIES = {"", "其他", "未分类", "None"}

FLAG_ORDER = ["portal_page", "summary_over_length", "uncategorized", "bogus_amount"]
FLAG_LABELS = {
    "portal_page": "门户页/栏目页",
    "summary_over_length": f"摘要超长（>{MAX_SUMMARY_CHARS}字）",
    "uncategorized": "未分类（其他/空）",
    "bogus_amount": "金额异常（年份/裸数字）",
}

_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
_BARE_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)?$")

# §5.2 明确指名的门户页特征串：分页控件与站点页脚标识。栏目页正文里出现这些
# 标记，是列表页被当作正文抓进来的直接证据。但真公文页脚同样带「网站标识码」，
# 所以内容标记只在正文很短时才作数：栏目页抓进来时正文只剩导航与页脚
# （<2000 字），真公文的正文长度远超这个量级。
_PORTAL_CHROME_RE = re.compile(r"上一页|下一页|共\s*\d+\s*条|网站标识码")
_PORTAL_CHROME_MAX_CHARS = 2000

# 数据新鲜度阈值：最新一次成功采集超过该小时数即视为陈旧（需求书 §1.4 的「标黄」）。
STALE_HOURS = 48
# 最近新增趋势的窗口。
RECENT_WINDOW_DAYS = 7


def _as_dt(value: Any) -> datetime | None:
    """SQLite 存的是 UTC 朴素时间字符串；统一按 UTC 解释。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def summary_of(record: dict[str, Any]) -> str:
    """摘要优先取视图层已展平的字段，其次回落到 extra.summary。"""
    if record.get("summary"):
        return str(record["summary"])
    extra = record.get("extra")
    if isinstance(extra, dict):
        return str(extra.get("summary") or "")
    return ""


def _is_bogus_amount(raw: Any) -> bool:
    """保守判定：能解析成带单位的正常金额就放过，否则年份/裸数字算垃圾值。"""
    text = str(raw or "").strip()
    if not text:
        return False
    if parse_amount(text) is not None:
        return False
    if _YEAR_RE.match(text):
        return True
    return bool(_BARE_NUMBER_RE.match(text))


def is_portal_or_section(record: dict[str, Any]) -> bool:
    """标题判定优先；标题看不出门户特征时再看正文里的分页/页脚标记（限短正文）。"""
    if is_portal_page(record):
        return True
    content = str(record.get("content") or "")
    if len(content) > _PORTAL_CHROME_MAX_CHARS:
        return False
    return bool(_PORTAL_CHROME_RE.search(content))


def flag_record(record: dict[str, Any]) -> list[str]:
    """单条记录的质量标记，顺序与 FLAG_ORDER 一致。"""
    flags: list[str] = []
    if is_portal_or_section(record):
        flags.append("portal_page")
    if len(summary_of(record)) > MAX_SUMMARY_CHARS:
        flags.append("summary_over_length")
    if str(record.get("category_l1") or "") in JUNK_CATEGORIES:
        flags.append("uncategorized")
    if _is_bogus_amount(record.get("amount")):
        flags.append("bogus_amount")
    return flags


def flag_counts(records: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = {name: 0 for name in FLAG_ORDER}
    counts["clean"] = 0
    counts["total"] = 0
    for record in records:
        counts["total"] += 1
        flags = flag_record(record)
        if not flags:
            counts["clean"] += 1
        for name in flags:
            counts[name] += 1
    return counts


def filter_records(
    records: Iterable[dict[str, Any]],
    flags: Iterable[str] | None = None,
    region: str | None = None,
) -> list[dict[str, Any]]:
    """按标记筛记录：flags 为空表示不限；region 按省/市/县名子串匹配。"""
    wanted = [f for f in (flags or []) if f in FLAG_ORDER]
    result: list[dict[str, Any]] = []
    for record in records:
        if region:
            haystack = " ".join(
                str(record.get(key) or "")
                for key in ("region_province", "region_city", "region_county")
            )
            if region not in haystack:
                continue
        if wanted:
            if not set(flag_record(record)).intersection(wanted):
                continue
        result.append(record)
    return result


def quality_flags(
    db_path: str = "data/beacon.db",
    filter_by: Iterable[str] | None = None,
    region: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """§5.2：全库质量标记统计 + 可选筛选。

    返回 counts（各标记命中数）/ triggers（触发记录明细，供下钻）/ filtered
    （filter_by / region 命中记录）。库为空时 counts 全零，视图据此渲染空态。
    """
    records = fetch_announcements(db_path)
    counts = flag_counts(records)
    triggers: dict[str, list[dict[str, Any]]] = {name: [] for name in FLAG_ORDER}
    for record in records:
        for name in flag_record(record):
            if len(triggers[name]) < limit:
                triggers[name].append(
                    {
                        "id": record.get("id"),
                        "title": record.get("title"),
                        "source_id": record.get("source_id"),
                        "region": record.get("region_province") or "",
                    }
                )
    filtered = filter_records(records, filter_by, region) if (filter_by or region) else []
    return {
        "total": counts["total"],
        "counts": counts,
        "triggers": triggers,
        "filtered": filtered[:limit],
        "filtered_total": len(filtered),
    }


def data_freshness(db_path: str = "data/beacon.db", stale_hours: int = STALE_HOURS) -> dict[str, Any]:
    """§5.1：最新入库时间 / 最新成功采集时间 / 距今天数 / 是否陈旧。

    只调 Database 类读取，不新增 SQL。
    """
    from storage.db import Database
    from storage.models import SourceSyncState

    now = datetime.now(timezone.utc)
    last_success: datetime | None = None
    last_ingest: datetime | None = None
    latest_publish = ""
    recent = 0
    window_start = now.timestamp() - RECENT_WINDOW_DAYS * 86400

    with Database(db_path) as database:
        with database.session() as session:
            for state in session.query(SourceSyncState).all():
                ts = _as_dt(state.last_success_at)
                if ts and (last_success is None or ts > last_success):
                    last_success = ts
        for item in database.list_announcements(limit=500):
            ts = _as_dt(getattr(item, "last_seen_at", None)) or _as_dt(getattr(item, "fetched_at", None))
            if ts and (last_ingest is None or ts > last_ingest):
                last_ingest = ts
            if ts and ts.timestamp() >= window_start:
                recent += 1
            publish = str(getattr(item, "publish_date", "") or "")
            if publish and publish > latest_publish:
                latest_publish = publish

    hours_since = None
    if last_ingest is not None:
        hours_since = round((now - last_ingest).total_seconds() / 3600, 1)
    days_since_success = None
    if last_success is not None:
        days_since_success = round((now - last_success).total_seconds() / 86400, 2)

    return {
        "last_ingest_at": last_ingest.isoformat() if last_ingest else None,
        "last_success_at": last_success.isoformat() if last_success else None,
        "hours_since_ingest": hours_since,
        "days_since_success": days_since_success,
        "latest_publish_date": latest_publish or None,
        "recent_count": recent,
        "recent_days": RECENT_WINDOW_DAYS,
        "stale_hours": stale_hours,
        # 没有任何成功采集时间也算陈旧：说明同步状态里没落过一条成功记录。
        "stale": last_success is None
        or days_since_success is None
        or days_since_success * 24 > stale_hours,
    }
