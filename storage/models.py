from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Announcement(Base):
    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    publish_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # 纯正文指纹，用于跨 URL 判同一份文件；content_hash 含 url，是身份标识。
    content_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    gov_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    jurisdiction_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    region_province: Mapped[str | None] = mapped_column(String(64), nullable=True)
    region_city: Mapped[str | None] = mapped_column(String(64), nullable=True)
    region_county: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category_l1: Mapped[str | None] = mapped_column(String(32), nullable=True)
    category_l2: Mapped[str | None] = mapped_column(String(128), nullable=True)
    amount: Mapped[str | None] = mapped_column(String(128), nullable=True)
    deadline: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_group: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_industry: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_points: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    institution_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    institution_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    admission_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    admission_sub_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enrollment_count: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tuition_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    employment_guarantee: Mapped[str | None] = mapped_column(Text, nullable=True)
    retention_class: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    focus_tracked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    focus_tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    focus_reasons: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    project_tracked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    project_tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    project_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    industry_focus: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    industry_keys: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    industry_tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    guidance_source: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    guidance_year: Mapped[str | None] = mapped_column(String(8), nullable=True)
    content_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    content_quality: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    media_tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    media_cluster_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    media_heat: Mapped[float | None] = mapped_column(Float, nullable=True)
    media_terms: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    corroboration_status: Mapped[str] = mapped_column(String(32), nullable=False, default="single_source")
    corroboration_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    corroboration_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    corroborating_sources: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    matches: Mapped[list["MatchResult"]] = relationship(back_populates="announcement")

    __table_args__ = (
        UniqueConstraint("source_id", "source_url", name="uq_announcement_source_url"),
        Index("ix_announcement_source_hash", "source_id", "content_hash"),
        Index("ix_announcement_category_date", "category_l1", "publish_date"),
        Index("ix_announcement_admission", "admission_sub_category", "institution_type", "admission_type"),
        Index("ix_announcement_level_region", "gov_level", "region_province", "region_city", "region_county"),
        Index("ix_announcement_jurisdiction", "jurisdiction_code"),
        Index("ix_announcement_focus", "focus_tracked", "publish_date"),
        Index("ix_announcement_project", "project_tracked", "project_stage", "publish_date"),
        Index("ix_announcement_industry", "industry_focus", "guidance_year"),
        Index("ix_announcement_review", "needs_review", "content_source"),
        Index("ix_announcement_corroboration", "corroboration_status", "corroboration_count"),
        Index("ix_announcement_retention", "retention_until", "archived_at"),
    )


class SourceSyncState(Base):
    __tablename__ = "source_sync_state"

    source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    cadence_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")

    __table_args__ = (
        Index("ix_source_sync_due", "status", "next_due_at"),
    )


class UserSubscription(Base):
    __tablename__ = "user_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    gov_levels: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    region_province: Mapped[str | None] = mapped_column(String(64), nullable=True)
    region_city: Mapped[str | None] = mapped_column(String(64), nullable=True)
    region_county: Mapped[str | None] = mapped_column(String(64), nullable=True)
    categories: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    target_group: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_industry: Mapped[str | None] = mapped_column(Text, nullable=True)
    institution_preference: Mapped[str | None] = mapped_column(Text, nullable=True)
    keywords: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_subscription_user", "user_id"),
        Index("ix_subscription_region", "region_province", "region_city", "region_county"),
    )


class PushPreference(Base):
    __tablename__ = "push_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("user_subscriptions.id"), nullable=False, unique=True)
    push_frequency: Mapped[str] = mapped_column(String(16), nullable=False, default="daily")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    announcement_id: Mapped[int] = mapped_column(ForeignKey("announcements.id"), nullable=False)
    useful: Mapped[bool] = mapped_column(Boolean, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "announcement_id", name="uq_feedback_user_announcement"),
        Index("ix_feedback_announcement", "announcement_id"),
    )


class MatchResult(Base):
    __tablename__ = "match_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    announcement_id: Mapped[int] = mapped_column(ForeignKey("announcements.id"), nullable=False)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("user_subscriptions.id"), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reasons: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    announcement: Mapped[Announcement] = relationship(back_populates="matches")
    subscription: Mapped[UserSubscription] = relationship()

    __table_args__ = (
        UniqueConstraint("announcement_id", "subscription_id", name="uq_match_announcement_subscription"),
        Index("ix_match_subscription_score", "subscription_id", "score"),
        Index("ix_match_announcement", "announcement_id"),
    )


class NotificationLog(Base):
    __tablename__ = "notification_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    frequency: Mapped[str] = mapped_column(String(16), nullable=False)
    period_key: Mapped[str] = mapped_column(String(16), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("source_id", "content_hash", "channel", "period_key", name="uq_notification_period"),
        Index("ix_notification_period", "channel", "period_key"),
    )


class GraphNode(Base):
    __tablename__ = "graph_nodes"

    id: Mapped[str] = mapped_column(String(256), primary_key=True)
    node_type: Mapped[str] = mapped_column(String(32), nullable=False)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    attributes: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_graph_node_type", "node_type"),
        Index("ix_graph_node_name", "canonical_name"),
    )


class GraphEvidence(Base):
    __tablename__ = "graph_evidence"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    announcement_id: Mapped[int] = mapped_column(ForeignKey("announcements.id"), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    field_path: Mapped[str] = mapped_column(String(128), nullable=False)
    value_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index("ix_graph_evidence_announcement", "announcement_id"),
        Index("ix_graph_evidence_hash", "content_hash"),
    )


class GraphEdge(Base):
    __tablename__ = "graph_edges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    edge_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    edge_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_node_id: Mapped[str] = mapped_column(ForeignKey("graph_nodes.id"), nullable=False)
    target_node_id: Mapped[str] = mapped_column(ForeignKey("graph_nodes.id"), nullable=False)
    source_announcement_id: Mapped[int] = mapped_column(ForeignKey("announcements.id"), nullable=False)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("graph_evidence.id"), nullable=False)
    attributes: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    extractor_version: Mapped[str] = mapped_column(String(64), nullable=False, default="structural-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_graph_edge_source", "source_node_id", "edge_type"),
        Index("ix_graph_edge_target", "target_node_id", "edge_type"),
        Index("ix_graph_edge_announcement", "source_announcement_id"),
        Index("ix_graph_edge_review", "needs_review"),
    )


class HeatTopic(Base):
    __tablename__ = "heat_topics"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    report_date: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    category_l1: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category_l2: Mapped[str | None] = mapped_column(String(128), nullable=True)
    heat_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_names: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    latest_publish: Mapped[str | None] = mapped_column(String(64), nullable=True)
    article_urls: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    terms: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    heat_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_heat_topic_date_score", "report_date", "heat_score"),
    )


class PolicyTopic(Base):
    """对比主题：一期字段 = Announcement 现有列的通用 Schema，二期限定主题专属 Schema。"""

    __tablename__ = "policy_topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    keywords: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    field_schema: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_policy_topic_status", "status"),
    )


class TopicDocLink(Base):
    """主题↔公告的三级关联：国家部委→省级→市县级，置信度决定 auto/pending。"""

    __tablename__ = "topic_doc_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("policy_topics.id"), nullable=False)
    announcement_id: Mapped[int] = mapped_column(ForeignKey("announcements.id"), nullable=False)
    gov_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("topic_id", "announcement_id", name="uq_topic_doc_link"),
        Index("ix_topic_doc_link_status", "topic_id", "status", "confidence"),
        Index("ix_topic_doc_link_announcement", "announcement_id"),
    )


class FieldExtraction(Base):
    """字段提取留痕：value 为自动提取值，revised_value 为人工修正值，均留原文摘录。"""

    __tablename__ = "field_extractions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    announcement_id: Mapped[int] = mapped_column(ForeignKey("announcements.id"), nullable=False)
    topic_id: Mapped[int] = mapped_column(ForeignKey("policy_topics.id"), nullable=False)
    field_key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evidence_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    method: Mapped[str] = mapped_column(String(16), nullable=False, default="rule")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="auto")
    revised_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("announcement_id", "topic_id", "field_key", name="uq_field_extraction"),
        Index("ix_field_extraction_status", "topic_id", "status", "confidence"),
        Index("ix_field_extraction_announcement", "announcement_id"),
    )


class Attachment(Base):
    """附件文本提取留痕：只存提取后的文本，不存二进制文件。

    二进制文件下载到临时目录，提取文本后立即删除。
    需要原文时用 source_url 重新下载。
    """

    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    announcement_id: Mapped[int] = mapped_column(ForeignKey("announcements.id"), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    file_type: Mapped[str] = mapped_column(String(16), nullable=False)  # pdf/docx/xlsx/doc
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_length: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    extract_status: Mapped[str] = mapped_column(String(16), nullable=False, default="success")  # success/failed/skipped
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("announcement_id", "url_hash", name="uq_attachment"),
        Index("ix_attachment_announcement", "announcement_id"),
    )


class TopicSubscription(Base):
    """主题订阅：关注某主题的指定省份，变化时生成站内通知。"""

    __tablename__ = "topic_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, default="local")
    topic_id: Mapped[int] = mapped_column(ForeignKey("policy_topics.id"), nullable=False)
    regions: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    notice_types: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "topic_id", name="uq_topic_subscription_user_topic"),
        Index("ix_topic_subscription_topic", "topic_id", "enabled"),
    )


class UserNotice(Base):
    """站内通知：订阅触发的变化提醒，read 标记已读。"""

    __tablename__ = "user_notices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, default="local")
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("policy_topics.id"), nullable=True)
    notice_type: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index("ix_user_notice_user", "user_id", "read", "created_at"),
    )


class WorkReport(Base):
    """政府工作报告本体。

    与公告分表存放，理由不是整洁而是度量：一份报告 2-4 万字，混进
    announcements 会让 median_content_chars、under_200_chars_rate、
    chrome_residue_rate 全部失真，那几个指标正是用来判断公告质量的。

    year 从标题文本解析而不是 publish_date——每年报告的发布日不同
    （上海 2026 年报告发布于 02-25），且归档页常年不更新。
    """

    __tablename__ = "work_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    region_code: Mapped[str] = mapped_column(String(32), nullable=False)
    region_name: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[str] = mapped_column(String(32), nullable=False, default="市级")
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    report_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # [{"key": "review", "title": "一、2025年工作回顾", "chars": 8123}, ...]
    sections: Mapped[list | None] = mapped_column(JSON, nullable=True)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_work_reports_region_year", "region_code", "year"),
        Index("ix_work_reports_year", "year"),
    )


class WorkReportMetric(Base):
    """从报告里抽出的可比指标。专题分析的燃料。

    is_target 区分「上年回顾」段（上年实际完成值）与「本年目标」段
    （本年目标值）。跨年对比的正确性依赖它：报告 N 的目标要跟报告 N-1
    的实际比，而不是同年两个目标互比。

    caliber 存口径原文表述。���住人口 != 户籍人口、地区生产总值 != 生产总值，
    不记口径的跨地区对比是假的。口径不同的值不参与自动对比。
    """

    __tablename__ = "work_report_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("work_reports.id"), nullable=False)
    region_code: Mapped[str] = mapped_column(String(32), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    topic: Mapped[str] = mapped_column(String(32), nullable=False)
    metric_key: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(96), nullable=False)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(24), nullable=True)
    growth_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    growth_unit: Mapped[str | None] = mapped_column(String(24), nullable=True)
    is_target: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    caliber: Mapped[str | None] = mapped_column(String(96), nullable=True)
    section: Mapped[str | None] = mapped_column(String(32), nullable=True)
    evidence_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 证据等级。政府工作报告早年只报增速不报总量（上海 2009-2015 全部只写
    # "生产总值增长X%"），缺口要靠统计公报等一手权威来源补。补进来的值和报告
    # 原文抽出的值证据强度不同，混在一起就毁了口径追踪，所以来源必须是一等
    # 字段而不是备注：
    #   report_text            从报告正文抽取，有原文出处
    #   statistical_communique 从统计局年度公报抽取，权威一手来源
    #   manual                 人工核定
    # 同一 (region, year, metric) 出现两个不同值时不自动取舍，交给
    # resolve_conflicts() 报出来，由人决定。
    provenance: Mapped[str] = mapped_column(String(32), nullable=False, default="report_text")
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_publisher: Mapped[str | None] = mapped_column(String(96), nullable=True)
    extract_version: Mapped[str] = mapped_column(String(24), nullable=False, default="metrics-v2")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index("ix_wrm_metric_year", "metric_key", "year"),
        Index("ix_wrm_region_metric", "region_code", "metric_key", "year"),
        Index("ix_wrm_report", "report_id"),
        Index("ix_wrm_topic", "topic"),
        Index("ix_wrm_provenance", "provenance"),
    )


class WorkReportTopic(Base):
    """专题命中：某份报告里出现某专题的次数。专题看板用来定位原文段落。"""

    __tablename__ = "work_report_topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("work_reports.id"), nullable=False)
    topic: Mapped[str] = mapped_column(String(32), nullable=False)
    region_code: Mapped[str] = mapped_column(String(32), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 命中段落原文，供专题看板展示与人工核对
    hits: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index("ix_wrt_topic_year", "topic", "year"),
        Index("ix_wrt_report", "report_id"),
    )
