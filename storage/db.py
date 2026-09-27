from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from sqlalchemy import create_engine, delete, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .models import Announcement, Attachment, Base, Feedback, FieldExtraction, GraphEdge, GraphEvidence, GraphNode, HeatTopic, MatchResult, NotificationLog, PolicyTopic, PushPreference, SourceSyncState, TopicDocLink, TopicSubscription, UserNotice, UserSubscription, utcnow


def _as_datetime(value: Any, default: datetime | None = None) -> datetime | None:
    if value is None:
        return default
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return default
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return default


def compute_content_hash(record: Mapping[str, Any]) -> str:
    payload = {
        "title": record.get("title", ""),
        "publish_date": record.get("publish_date", ""),
        "url": record.get("url") or record.get("source_url", ""),
        "content": record.get("content", ""),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class Database:
    def __init__(self, path: str = "data/beacon.db") -> None:
        self.path = path
        if path != ":memory:":
            database_path = Path(path)
            database_path.parent.mkdir(parents=True, exist_ok=True)
            url = f"sqlite:///{database_path.resolve()}"
        else:
            url = "sqlite://"
        engine_options = {"future": True, "connect_args": {"check_same_thread": False}}
        if path == ":memory:":
            engine_options["poolclass"] = StaticPool
        self.engine = create_engine(url, **engine_options)
        if path != ":memory:":
            self._harden_sqlite_for_concurrent_writes()
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, class_=Session)
        Base.metadata.create_all(self.engine)
        self._ensure_sqlite_columns()

    def _harden_sqlite_for_concurrent_writes(self) -> None:
        """Let the collector write from several source threads at once.

        The batch runner collects sources in parallel, so several threads reach
        mark_source_result() while others are still reading. Under the default
        rollback journal a writer blocks every reader and a busy writer raises
        "database is locked" outright. WAL allows one writer alongside many
        readers, and a 30s busy timeout makes the remaining writer-writer overlap
        wait rather than fail. synchronous=NORMAL is the usual companion to WAL:
        durable across process crashes, and it does not fsync on every commit.
        """

        @event.listens_for(self.engine, "connect")
        def _set_pragmas(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.execute("PRAGMA synchronous=NORMAL")
            finally:
                cursor.close()

    def _ensure_sqlite_columns(self) -> None:
        if self.path == ":memory:":
            return
        with self.engine.begin() as connection:
            result = connection.exec_driver_sql("PRAGMA table_info(user_subscriptions)")
            columns = {str(row[1]) for row in result}
            if "gov_levels" not in columns:
                connection.exec_driver_sql("ALTER TABLE user_subscriptions ADD COLUMN gov_levels JSON")
            if "enabled" not in columns:
                connection.exec_driver_sql("ALTER TABLE user_subscriptions ADD COLUMN enabled BOOLEAN NOT NULL DEFAULT 1")
            announcement_columns = {str(row[1]) for row in connection.exec_driver_sql("PRAGMA table_info(announcements)")}
            if "corroboration_status" not in announcement_columns:
                connection.exec_driver_sql("ALTER TABLE announcements ADD COLUMN corroboration_status VARCHAR(32) NOT NULL DEFAULT 'single_source'")
            if "corroboration_count" not in announcement_columns:
                connection.exec_driver_sql("ALTER TABLE announcements ADD COLUMN corroboration_count INTEGER NOT NULL DEFAULT 1")
            if "corroboration_reason" not in announcement_columns:
                connection.exec_driver_sql("ALTER TABLE announcements ADD COLUMN corroboration_reason TEXT")
            if "media_type" not in announcement_columns:
                connection.exec_driver_sql("ALTER TABLE announcements ADD COLUMN media_type VARCHAR(32)")
            if "media_tags" not in announcement_columns:
                connection.exec_driver_sql("ALTER TABLE announcements ADD COLUMN media_tags JSON")
            if "media_cluster_id" not in announcement_columns:
                connection.exec_driver_sql("ALTER TABLE announcements ADD COLUMN media_cluster_id VARCHAR(64)")
            if "media_heat" not in announcement_columns:
                connection.exec_driver_sql("ALTER TABLE announcements ADD COLUMN media_heat FLOAT")
            if "media_terms" not in announcement_columns:
                connection.exec_driver_sql("ALTER TABLE announcements ADD COLUMN media_terms JSON")
            if "corroborating_sources" not in announcement_columns:
                connection.exec_driver_sql("ALTER TABLE announcements ADD COLUMN corroborating_sources JSON")
            # 内容指纹：纯正文的内容级哈希，用来判"同一份文件"。
            # 与 content_hash 分工不同——content_hash 把 url 也算进去，身份标识用；
            # 同一份文件挂在两个 URL 下时它永远不同，去不了重。实测全库
            # content_hash 只认出 26 条重复，正文指纹认出 64 条。
            if "content_fingerprint" not in announcement_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE announcements ADD COLUMN content_fingerprint VARCHAR(64)")
            # 指标来源标记。create_all() 只建新表，不会给已存在的表加列，所以
            # 老库升级必须走 ALTER。provenance 是一等字段而非备注：报告原文抽的
            # 值和统计公报补的值证据强度不同，混在一起就毁了口径追踪。
            metric_columns = {str(row[1]) for row in connection.exec_driver_sql("PRAGMA table_info(work_report_metrics)")}
            if metric_columns and "provenance" not in metric_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE work_report_metrics ADD COLUMN provenance VARCHAR(32) NOT NULL DEFAULT 'report_text'")
            if metric_columns and "source_url" not in metric_columns:
                connection.exec_driver_sql("ALTER TABLE work_report_metrics ADD COLUMN source_url TEXT")
            if metric_columns and "source_publisher" not in metric_columns:
                connection.exec_driver_sql("ALTER TABLE work_report_metrics ADD COLUMN source_publisher VARCHAR(96)")

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def upsert_announcement(self, record: Mapping[str, Any]) -> str:
        source_id = str(record.get("source_id") or "")
        source_url = str(record.get("source_url") or record.get("url") or "")
        if not source_id or not source_url:
            raise ValueError("source_id and source_url are required")
        content_hash = str(record.get("content_hash") or compute_content_hash(record))
        now = utcnow()
        with self.session() as session:
            existing = session.scalar(
                select(Announcement).where(
                    Announcement.source_id == source_id,
                    Announcement.source_url == source_url,
                )
            )
            if existing is None:
                announcement = Announcement(
                    source_id=source_id,
                    source_url=source_url,
                    title=str(record.get("title") or ""),
                    publish_date=record.get("publish_date") or record.get("date"),
                    content=str(record.get("content") or ""),
                    content_hash=content_hash,
                    content_fingerprint=record.get("content_fingerprint") or None,
                    gov_level=record.get("gov_level"),
                    jurisdiction_code=record.get("jurisdiction_code"),
                    region_province=record.get("region_province"),
                    region_city=record.get("region_city"),
                    region_county=record.get("region_county"),
                    category_l1=record.get("category_l1"),
                    category_l2=record.get("category_l2"),
                    amount=record.get("amount"),
                    deadline=record.get("deadline"),
                    target_group=record.get("target_group"),
                    target_industry=record.get("target_industry"),
                    key_points=record.get("key_points"),
                    institution_name=record.get("institution_name"),
                    institution_type=record.get("institution_type"),
                    admission_type=record.get("admission_type"),
                    admission_sub_category=record.get("admission_sub_category"),
                    enrollment_count=record.get("enrollment_count"),
                    tuition_info=record.get("tuition_info"),
                    employment_guarantee=record.get("employment_guarantee"),
                    retention_class=record.get("retention_class", "active"),
                    retention_until=_as_datetime(record.get("retention_until")),
                    focus_tracked=bool(record.get("focus_tracked", False)),
                    focus_tags=record.get("focus_tags"),
                    focus_reasons=record.get("focus_reasons"),
                    project_tracked=bool(record.get("project_tracked", False)),
                    project_tags=record.get("project_tags"),
                    project_stage=record.get("project_stage"),
                    industry_focus=bool(record.get("industry_focus", False)),
                    industry_keys=record.get("industry_keys"),
                    industry_tags=record.get("industry_tags"),
                    guidance_source=bool(record.get("guidance_source", False)),
                    guidance_year=record.get("guidance_year"),
                     content_source=record.get("content_source"),
                     content_quality=record.get("content_quality"),
                     media_type=record.get("media_type"),
                     media_tags=record.get("media_tags"),
                     media_cluster_id=record.get("media_cluster_id"),
                     media_heat=record.get("media_heat"),
                     media_terms=record.get("media_terms"),
                     corroboration_status=str(record.get("corroboration_status") or "single_source"),
                     corroboration_count=int(record.get("corroboration_count") or 1),
                     corroboration_reason=record.get("corroboration_reason"),
                     corroborating_sources=record.get("corroborating_sources") or [],
                     needs_review=bool(record.get("needs_review", False)),
                    archived_at=None,
                     extra={
                         "source_type": record.get("source_type"),
                         "source_priority": record.get("source_priority"),
                         "cadence_tier": record.get("cadence_tier"),
                         "focus_level": record.get("focus_level"),
                         "project_role": record.get("project_role"),
                         "level": record.get("level"),
                         "issuing_body": record.get("issuing_body"),
                         "summary": record.get("summary"),
                         "summary_drop_reason": record.get("summary_drop_reason"),
                         "summary_repairs": record.get("summary_repairs") or [],
                         "extraction_tags": record.get("extraction_tags") or [],
                         "attachment_links": record.get("attachment_links") or [],
                         "attachment_count": record.get("attachment_count", 0),
                     },
                    fetched_at=_as_datetime(record.get("fetched_at"), now) or now,
                    last_seen_at=now,
                    created_at=now,
                    updated_at=now,
                )
                session.add(announcement)
                return "inserted"
            existing.last_seen_at = now
            existing.focus_tracked = bool(record.get("focus_tracked", existing.focus_tracked))
            existing.focus_tags = record.get("focus_tags", existing.focus_tags)
            existing.focus_reasons = record.get("focus_reasons", existing.focus_reasons)
            existing.project_tracked = bool(record.get("project_tracked", existing.project_tracked))
            existing.project_tags = record.get("project_tags", existing.project_tags)
            existing.project_stage = record.get("project_stage", existing.project_stage)
            existing.industry_focus = bool(record.get("industry_focus", existing.industry_focus))
            existing.industry_keys = record.get("industry_keys", existing.industry_keys)
            existing.industry_tags = record.get("industry_tags", existing.industry_tags)
            existing.guidance_source = bool(record.get("guidance_source", existing.guidance_source))
            existing.guidance_year = record.get("guidance_year", existing.guidance_year)
            existing.content_source = record.get("content_source", existing.content_source)
            existing.content_quality = record.get("content_quality", existing.content_quality)
            existing.media_type = record.get("media_type", existing.media_type)
            existing.media_tags = record.get("media_tags", existing.media_tags)
            existing.media_cluster_id = record.get("media_cluster_id", existing.media_cluster_id)
            existing.media_heat = record.get("media_heat", existing.media_heat)
            existing.media_terms = record.get("media_terms", existing.media_terms)
            existing.corroboration_status = record.get("corroboration_status", existing.corroboration_status)
            existing.corroboration_count = int(record.get("corroboration_count", existing.corroboration_count) or 0)
            existing.corroboration_reason = record.get("corroboration_reason", existing.corroboration_reason)
            existing.corroborating_sources = record.get("corroborating_sources", existing.corroborating_sources)
            existing.needs_review = bool(record.get("needs_review", existing.needs_review))
            if existing.content_hash == content_hash:
                return "unchanged"
            existing.title = str(record.get("title") or "")
            existing.publish_date = record.get("publish_date") or record.get("date")
            existing.content = str(record.get("content") or "")
            existing.content_hash = content_hash
            if record.get("content_fingerprint"):
                existing.content_fingerprint = record["content_fingerprint"]
            existing.gov_level = record.get("gov_level")
            existing.jurisdiction_code = record.get("jurisdiction_code")
            existing.region_province = record.get("region_province")
            existing.region_city = record.get("region_city")
            existing.region_county = record.get("region_county")
            existing.category_l1 = record.get("category_l1")
            existing.category_l2 = record.get("category_l2")
            existing.amount = record.get("amount")
            existing.deadline = record.get("deadline")
            existing.target_group = record.get("target_group")
            existing.target_industry = record.get("target_industry")
            existing.key_points = record.get("key_points", existing.key_points)
            existing.institution_name = record.get("institution_name")
            existing.institution_type = record.get("institution_type")
            existing.admission_type = record.get("admission_type")
            existing.admission_sub_category = record.get("admission_sub_category")
            existing.enrollment_count = record.get("enrollment_count")
            existing.tuition_info = record.get("tuition_info")
            existing.employment_guarantee = record.get("employment_guarantee")
            existing.retention_class = record.get("retention_class") or existing.retention_class
            if "retention_until" in record:
                existing.retention_until = _as_datetime(record.get("retention_until"))
            existing.extra = {
                "source_type": record.get("source_type"),
                "source_priority": record.get("source_priority"),
                "cadence_tier": record.get("cadence_tier"),
                "focus_level": record.get("focus_level"),
                "project_role": record.get("project_role"),
                "level": record.get("level"),
                "issuing_body": record.get("issuing_body"),
                "summary": record.get("summary"),
                "summary_drop_reason": record.get("summary_drop_reason"),
                "summary_repairs": record.get("summary_repairs") or [],
                "extraction_tags": record.get("extraction_tags") or [],
                "attachment_links": record.get("attachment_links") or [],
                "attachment_count": record.get("attachment_count", 0),
            }
            existing.fetched_at = _as_datetime(record.get("fetched_at"), now) or now
            existing.updated_at = now
            return "updated"

    def upsert_announcements(self, records: list[Mapping[str, Any]]) -> dict[str, int]:
        result = {"inserted": 0, "updated": 0, "unchanged": 0}
        for record in records:
            result[self.upsert_announcement(record)] += 1
        return result

    def source_due(self, source_id: str, now: datetime | None = None) -> bool:
        current = _as_datetime(now, utcnow()) or utcnow()
        with self.session() as session:
            state = session.get(SourceSyncState, source_id)
            if state is None or state.next_due_at is None:
                return True
            due = state.next_due_at
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
            return due <= current

    def mark_source_result(
        self,
        source_id: str,
        cadence_tier: str,
        interval_seconds: int,
        success: bool,
        error: str | None = None,
        now: datetime | None = None,
    ) -> None:
        current = _as_datetime(now, utcnow()) or utcnow()
        interval = max(1, int(interval_seconds))
        with self.session() as session:
            state = session.get(SourceSyncState, source_id)
            if state is None:
                state = SourceSyncState(
                    source_id=source_id,
                    cadence_tier=cadence_tier,
                    interval_seconds=interval,
                    consecutive_failures=0,
                )
                session.add(state)
            state.cadence_tier = cadence_tier
            state.interval_seconds = interval
            state.last_attempt_at = current
            if success:
                state.last_success_at = current
                state.next_due_at = current + timedelta(seconds=interval)
                state.last_error = None
                state.consecutive_failures = 0
                state.status = "ok"
            else:
                state.consecutive_failures += 1
                retry_seconds = min(86400, 3600 * (2 ** min(state.consecutive_failures - 1, 5)))
                state.next_due_at = current + timedelta(seconds=retry_seconds)
                state.last_error = (error or "unknown error")[:2000]
                state.status = "error"

    def purge_expired(self, now: datetime | None = None) -> dict[str, int]:
        current = _as_datetime(now, utcnow()) or utcnow()
        with self.session() as session:
            ids = list(
                session.scalars(
                    select(Announcement.id).where(
                        Announcement.retention_until.is_not(None),
                        Announcement.retention_until < current,
                    )
                )
            )
            if not ids:
                return {"deleted": 0}
            session.execute(delete(MatchResult).where(MatchResult.announcement_id.in_(ids)))
            deleted = session.execute(delete(Announcement).where(Announcement.id.in_(ids))).rowcount
            return {"deleted": int(deleted or 0)}

    def notification_sent(self, source_id: str, content_hash: str, channel: str, period_key: str) -> bool:
        with self.session() as session:
            return session.scalar(
                select(NotificationLog.id).where(
                    NotificationLog.source_id == source_id,
                    NotificationLog.content_hash == content_hash,
                    NotificationLog.channel == channel,
                    NotificationLog.period_key == period_key,
                )
            ) is not None

    def mark_notification(
        self,
        source_id: str,
        content_hash: str,
        channel: str,
        frequency: str,
        period_key: str,
        now: datetime | None = None,
    ) -> None:
        current = _as_datetime(now, utcnow()) or utcnow()
        with self.session() as session:
            session.add(
                NotificationLog(
                    source_id=source_id,
                    content_hash=content_hash,
                    channel=channel,
                    frequency=frequency,
                    period_key=period_key,
                    sent_at=current,
                )
            )

    def count_announcements(self) -> int:
        with self.session() as session:
            return int(session.scalar(select(func.count()).select_from(Announcement)) or 0)

    def list_announcements(self, limit: int = 100) -> list[Announcement]:
        with self.session() as session:
            return list(session.query(Announcement).order_by(Announcement.id.desc()).limit(limit).all())

    def upsert_heat_topics(self, topics: list[Mapping[str, Any]], report_date: str) -> int:
        with self.session() as session:
            for topic in topics:
                cluster_id = str(topic.get("cluster_id") or "")
                if not cluster_id:
                    continue
                row = session.get(HeatTopic, cluster_id)
                if row is None:
                    row = HeatTopic(id=cluster_id, report_date=report_date, title=str(topic.get("title") or "未命名话题"))
                    session.add(row)
                row.report_date = report_date
                row.title = str(topic.get("title") or "未命名话题")
                row.category_l1 = topic.get("category_l1")
                row.category_l2 = topic.get("category_l2")
                row.heat_score = float(topic.get("heat_score") or 0)
                row.source_count = int(topic.get("source_count") or 0)
                row.source_names = list(topic.get("source_names") or [])
                row.latest_publish = topic.get("latest_publish")
                row.article_urls = list(topic.get("article_urls") or [])
                row.terms = list(topic.get("terms") or [])
                row.heat_reason = topic.get("heat_reason")
                row.summary = topic.get("summary")
            return len(topics)

    def list_heat_topics(self, report_date: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self.session() as session:
            query = select(HeatTopic)
            if report_date:
                query = query.where(HeatTopic.report_date == report_date)
            query = query.order_by(HeatTopic.heat_score.desc(), HeatTopic.latest_publish.desc()).limit(max(1, min(limit, 200)))
            rows = list(session.scalars(query).all())
            return [{"cluster_id": row.id, "title": row.title, "category_l1": row.category_l1, "category_l2": row.category_l2, "heat_score": row.heat_score, "source_count": row.source_count, "source_names": list(row.source_names or []), "latest_publish": row.latest_publish, "article_urls": list(row.article_urls or []), "heat_reason": row.heat_reason, "summary": row.summary} for row in rows]

    def get_heat_topic(self, cluster_id: str) -> dict[str, Any] | None:
        with self.session() as session:
            row = session.get(HeatTopic, cluster_id)
            if row is None:
                return None
            return {"cluster_id": row.id, "title": row.title, "category_l1": row.category_l1, "category_l2": row.category_l2, "heat_score": row.heat_score, "source_count": row.source_count, "source_names": list(row.source_names or []), "latest_publish": row.latest_publish, "article_urls": list(row.article_urls or []), "heat_reason": row.heat_reason, "summary": row.summary}

    # ── 字段抽取留痕 ──
    # record 字段 → topic field_key 的映射表。
    # policy_topics.field_schema 定义了关注哪些字段，这里把采集管线已抽取
    # 的值写入 field_extractions，让对比/印证有结构化数据可用。
    FIELD_KEY_MAP: dict[str, str] = {
        "amount": "subsidy_amount",
        "target_group": "target_group",
        "deadline": "deadline",
        "issuing_body": "institution",
        "region_province": "region",
        "region": "region",
        "key_points": "key_measures",
    }

    def save_field_extractions(
        self,
        records: list[Mapping[str, Any]],
        topic_id: int = 1,
    ) -> dict[str, int]:
        """把已抽取的字段写入 field_extractions 表。

        enrich_records 已经把 amount/deadline/issuing_body 等抽取到 record dict，
        但只 merge 到 announcements 列。这里额外留痕到 field_extractions，
        让对比引擎和印证引擎有结构化数据可查。
        """
        result = {"inserted": 0, "updated": 0, "skipped": 0}
        with self.session() as session:
            for record in records:
                source_id = str(record.get("source_id") or "")
                source_url = str(record.get("source_url") or record.get("url") or "")
                if not source_id or not source_url:
                    result["skipped"] += 1
                    continue
                ann = session.scalar(
                    select(Announcement).where(
                        Announcement.source_id == source_id,
                        Announcement.source_url == source_url,
                    )
                )
                if ann is None:
                    result["skipped"] += 1
                    continue
                for record_key, field_key in self.FIELD_KEY_MAP.items():
                    value = record.get(record_key)
                    if value is None or value == "" or value == []:
                        continue
                    # key_points 是 list，转 JSON 存
                    if isinstance(value, list):
                        value_text = json.dumps(value, ensure_ascii=False)
                    else:
                        value_text = str(value)
                    evidence = str(record.get("content") or "")[:200]
                    existing = session.scalar(
                        select(FieldExtraction).where(
                            FieldExtraction.announcement_id == ann.id,
                            FieldExtraction.topic_id == topic_id,
                            FieldExtraction.field_key == field_key,
                        )
                    )
                    if existing is None:
                        session.add(FieldExtraction(
                            announcement_id=ann.id,
                            topic_id=topic_id,
                            field_key=field_key,
                            value=value_text,
                            confidence=80,
                            evidence_text=evidence,
                            method="rule",
                            status="auto",
                        ))
                        result["inserted"] += 1
                    else:
                        existing.value = value_text
                        existing.evidence_text = evidence
                        existing.updated_at = utcnow()
                        result["updated"] += 1
        return result

    def save_attachments(self, records: list[Mapping[str, Any]]) -> dict[str, int]:
        """把附件提取的文本存入 attachments 表。

        只存文本，不存二进制文件。需要原文时用 source_url 重新下载。
        """
        result = {"inserted": 0, "updated": 0, "skipped": 0}
        with self.session() as session:
            for record in records:
                texts = record.get("attachment_texts") or []
                if not texts:
                    continue
                source_id = str(record.get("source_id") or "")
                source_url = str(record.get("source_url") or "")
                ann = session.scalar(
                    select(Announcement).where(
                        Announcement.source_id == source_id,
                        Announcement.source_url == source_url,
                    )
                )
                if ann is None:
                    result["skipped"] += 1
                    continue
                for t in texts:
                    url = t.get("url", "")
                    url_hash = t.get("url_hash", "")
                    file_type = t.get("file_type", "")
                    text = t.get("text", "")
                    existing = session.scalar(
                        select(Attachment).where(
                            Attachment.announcement_id == ann.id,
                            Attachment.url_hash == url_hash,
                        )
                    )
                    if existing is None:
                        session.add(Attachment(
                            announcement_id=ann.id,
                            source_url=url,
                            url_hash=url_hash,
                            file_type=file_type,
                            extracted_text=text,
                            text_length=len(text),
                            extract_status="success" if text else "failed",
                        ))
                        result["inserted"] += 1
                    else:
                        existing.extracted_text = text
                        existing.text_length = len(text)
                        existing.extract_status = "success" if text else "failed"
                        result["updated"] += 1
        return result

    def close(self) -> None:
        self.engine.dispose()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()
