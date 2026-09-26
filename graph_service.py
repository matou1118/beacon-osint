from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import delete, func, select

from storage.db import Database
from storage.models import Announcement, GraphEdge, GraphEvidence, GraphNode


def load_graph_config(path: str = "config/graph.yaml") -> dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("config/graph.yaml must contain a mapping")
    return data


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _node_id(node_type: str, value: Any) -> str:
    normalized = _text(value).casefold()
    digest = hashlib.sha256(f"{node_type}:{normalized}".encode("utf-8")).hexdigest()[:24]
    return f"{node_type}:{digest}"


def _evidence_id(announcement: Announcement, field_path: str, value: Any) -> str:
    payload = f"{announcement.id}:{announcement.content_hash}:{field_path}:{_text(value)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _upsert_node(session: Any, node_id: str, node_type: str, name: str, attributes: dict[str, Any]) -> GraphNode:
    node = session.get(GraphNode, node_id)
    if node is None:
        node = GraphNode(id=node_id, node_type=node_type, canonical_name=name, attributes=dict(attributes))
        session.add(node)
    else:
        node.node_type = node_type
        node.canonical_name = name
        node.attributes = dict(attributes)
        node.status = "active"
    return node


def _upsert_evidence(session: Any, announcement: Announcement, field_path: str, value: Any) -> GraphEvidence:
    evidence_id = _evidence_id(announcement, field_path, value)
    evidence = session.get(GraphEvidence, evidence_id)
    excerpt = _text(value)[:500]
    if evidence is None:
        evidence = GraphEvidence(id=evidence_id, announcement_id=announcement.id, source_url=announcement.source_url, content_hash=announcement.content_hash, field_path=field_path, value_excerpt=excerpt)
        session.add(evidence)
    else:
        evidence.source_url = announcement.source_url
        evidence.content_hash = announcement.content_hash
        evidence.value_excerpt = excerpt
    return evidence


def _upsert_edge(session: Any, announcement: Announcement, source_id: str, edge_type: str, target_id: str, field_path: str, value: Any, attributes: dict[str, Any] | None = None, extractor_version: str = "structural-v1") -> GraphEdge:
    evidence = _upsert_evidence(session, announcement, field_path, value)
    edge_key = hashlib.sha256(f"{announcement.id}:{source_id}:{edge_type}:{target_id}:{field_path}".encode("utf-8")).hexdigest()
    edge = session.scalar(select(GraphEdge).where(GraphEdge.edge_key == edge_key))
    if edge is None:
        edge = GraphEdge(edge_key=edge_key, edge_type=edge_type, source_node_id=source_id, target_node_id=target_id, source_announcement_id=announcement.id, evidence_id=evidence.id, attributes=dict(attributes or {}), confidence=100, needs_review=bool(announcement.needs_review), extractor_version=extractor_version)
        session.add(edge)
    else:
        edge.attributes = dict(attributes or {})
        edge.needs_review = bool(announcement.needs_review)
        edge.evidence_id = evidence.id
        edge.extractor_version = extractor_version
    return edge


def _sync_announcement(session: Any, announcement: Announcement, extractor_version: str) -> None:
    announcement_id = f"announcement:{announcement.id}"
    extra = dict(announcement.extra or {})
    _upsert_node(session, announcement_id, "announcement", announcement.title, {"source_id": announcement.source_id, "publish_date": announcement.publish_date, "gov_level": announcement.gov_level, "needs_review": announcement.needs_review, "amount": announcement.amount, "deadline": announcement.deadline})
    source_id = _node_id("source", announcement.source_id)
    _upsert_node(session, source_id, "source", announcement.source_id, {"url": announcement.source_url})
    _upsert_edge(session, announcement, announcement_id, "PUBLISHED_BY", source_id, "source_id", announcement.source_id, extractor_version=extractor_version)
    issuing_body = extra.get("issuing_body") or announcement.source_id
    organization_id = _node_id("organization", issuing_body)
    _upsert_node(session, organization_id, "organization", issuing_body, {})
    _upsert_edge(session, announcement, announcement_id, "ISSUED_BY", organization_id, "extra.issuing_body", issuing_body, extractor_version=extractor_version)
    region_values = [("province", announcement.region_province), ("city", announcement.region_city), ("county", announcement.region_county)]
    region_nodes: dict[str, str] = {}
    for level, value in region_values:
        if not value:
            continue
        node_id = _node_id("region", f"{level}:{value}")
        region_nodes[level] = node_id
        _upsert_node(session, node_id, "region", value, {"level": level})
        _upsert_edge(session, announcement, announcement_id, "LOCATED_IN", node_id, f"region_{level}", value, extractor_version=extractor_version)
    if "city" in region_nodes and "province" in region_nodes:
        _upsert_edge(session, announcement, region_nodes["city"], "PART_OF", region_nodes["province"], "region_city", announcement.region_city, extractor_version=extractor_version)
    if "county" in region_nodes and "city" in region_nodes:
        _upsert_edge(session, announcement, region_nodes["county"], "PART_OF", region_nodes["city"], "region_county", announcement.region_county, extractor_version=extractor_version)
    category_nodes: dict[str, str] = {}
    for level, value in (("l1", announcement.category_l1), ("l2", announcement.category_l2)):
        if not value:
            continue
        node_id = _node_id("category", f"{level}:{value}")
        category_nodes[level] = node_id
        _upsert_node(session, node_id, "category", value, {"level": level})
        _upsert_edge(session, announcement, announcement_id, "CLASSIFIED_AS", node_id, f"category_{level}", value, extractor_version=extractor_version)
    if "l1" in category_nodes and "l2" in category_nodes:
        _upsert_edge(session, announcement, category_nodes["l2"], "PART_OF", category_nodes["l1"], "category_l2", announcement.category_l2, extractor_version=extractor_version)
    for field, node_type, relation in (("target_group", "audience", "TARGETS"), ("target_industry", "industry", "TARGETS"), ("institution_name", "organization", "MENTIONS")):
        value = getattr(announcement, field, None)
        if not value:
            continue
        node_id = _node_id(node_type, value)
        _upsert_node(session, node_id, node_type, _text(value), {})
        _upsert_edge(session, announcement, announcement_id, relation, node_id, field, value, extractor_version=extractor_version)
    for value in announcement.project_tags or []:
        node_id = _node_id("project", value)
        _upsert_node(session, node_id, "project", _text(value), {"stage": announcement.project_stage})
        _upsert_edge(session, announcement, announcement_id, "MENTIONS", node_id, "project_tags", value, {"stage": announcement.project_stage}, extractor_version=extractor_version)


def _remove_announcement_edges(session: Any, announcement_id: int) -> None:
    edge_rows = list(session.scalars(select(GraphEdge).where(GraphEdge.source_announcement_id == announcement_id)).all())
    evidence_ids = [edge.evidence_id for edge in edge_rows]
    for edge in edge_rows:
        session.delete(edge)
    session.flush()
    if evidence_ids:
        session.execute(delete(GraphEvidence).where(GraphEvidence.id.in_(evidence_ids)))


def sync_announcement(db: Database, announcement_id: int, extractor_version: str = "structural-v1") -> None:
    with db.session() as session:
        announcement = session.get(Announcement, announcement_id)
        if announcement is None:
            return
        _remove_announcement_edges(session, announcement.id)
        _sync_announcement(session, announcement, extractor_version)


def rebuild_graph(db: Database, config_path: str = "config/graph.yaml") -> dict[str, int]:
    config = load_graph_config(config_path)
    if not config.get("enabled", True):
        return {"announcements": 0, "nodes": 0, "edges": 0, "evidence": 0}
    extractor_version = str(config.get("extractor_version") or "structural-v1")
    with db.session() as session:
        announcements = list(session.scalars(select(Announcement).order_by(Announcement.id)).all())
        for announcement in announcements:
            _remove_announcement_edges(session, announcement.id)
            _sync_announcement(session, announcement, extractor_version)
        session.flush()
        nodes = int(session.scalar(select(func.count()).select_from(GraphNode)) or 0)
        edges = int(session.scalar(select(func.count()).select_from(GraphEdge)) or 0)
        evidence = int(session.scalar(select(func.count()).select_from(GraphEvidence)) or 0)
    return {"announcements": len(announcements), "nodes": nodes, "edges": edges, "evidence": evidence}


def graph_summary(db: Database) -> dict[str, Any]:
    with db.session() as session:
        node_types = {str(node_type): int(count) for node_type, count in session.execute(select(GraphNode.node_type, func.count()).group_by(GraphNode.node_type)).all()}
        edge_types = {str(edge_type): int(count) for edge_type, count in session.execute(select(GraphEdge.edge_type, func.count()).group_by(GraphEdge.edge_type)).all()}
        return {"nodes": sum(node_types.values()), "edges": sum(edge_types.values()), "evidence": int(session.scalar(select(func.count()).select_from(GraphEvidence)) or 0), "needs_review": int(session.scalar(select(func.count()).select_from(GraphEdge).where(GraphEdge.needs_review.is_(True))) or 0), "node_types": node_types, "edge_types": edge_types}


def graph_neighbors(db: Database, node_id: str) -> dict[str, Any] | None:
    with db.session() as session:
        node = session.get(GraphNode, node_id)
        if node is None:
            return None
        outgoing = list(session.scalars(select(GraphEdge).where(GraphEdge.source_node_id == node_id)).all())
        incoming = list(session.scalars(select(GraphEdge).where(GraphEdge.target_node_id == node_id)).all())
        def item(edge: GraphEdge, direction: str) -> dict[str, Any]:
            other_id = edge.target_node_id if direction == "outgoing" else edge.source_node_id
            other = session.get(GraphNode, other_id)
            return {"edge_type": edge.edge_type, "direction": direction, "node": {"id": other.id, "type": other.node_type, "name": other.canonical_name} if other else {"id": other_id}, "needs_review": edge.needs_review}
        return {"node": {"id": node.id, "type": node.node_type, "name": node.canonical_name, "attributes": node.attributes or {}}, "neighbors": [item(edge, "outgoing") for edge in outgoing] + [item(edge, "incoming") for edge in incoming]}
