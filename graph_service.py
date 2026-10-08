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


# ── 来源名称映射 ──────────────────────────────────────────────
_SOURCE_NAME_MAP: dict[str, str] | None = None


def _load_source_name_map(path: str = "config/sources.yaml") -> dict[str, str]:
    """从 sources.yaml 加载 source_id -> source_name 映射"""
    global _SOURCE_NAME_MAP
    if _SOURCE_NAME_MAP is not None:
        return _SOURCE_NAME_MAP
    mapping: dict[str, str] = {}
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        for s in data.get("sources", []):
            sid = s.get("id") or s.get("source_id")
            sname = s.get("name") or s.get("source_name")
            if sid and sname:
                mapping[sid] = sname
    except Exception:
        pass
    _SOURCE_NAME_MAP = mapping
    return mapping


# ── 分类规范化 ──────────────────────────────────────────────
_CATEGORY_NORMALIZE: dict[str, str] = {
    "公示公告": "其他",
    "政策发布": "行业政策",
    "政策法规": "行业政策",
    "新闻动态": "其他",
    "规划计划": "行业政策",
    "行政审批": "其他",
    "公务员考试": "公招信息",
    "政策解读": "行业政策",
    "非公告体裁": "其他",
    "军队招录": "招生信息",
}


def _normalize_category(value: Any) -> str:
    """将非标准 category_l1 映射到标准枚举"""
    text = _text(value)
    return _CATEGORY_NORMALIZE.get(text, text)


# ── 机构名清洗 ──────────────────────────────────────────────
_ORG_SUFFIX_RE = re.compile(
    r"(政府信息公开目录|政务公开|信息公开目录|门户网站|官方网站|首页)$"
)


def _clean_org_name(name: str) -> str:
    """去除机构名末尾的无意义后缀"""
    cleaned = _text(name)
    cleaned = _ORG_SUFFIX_RE.sub("", cleaned)
    return cleaned or name


# ── 分类规范化 ──────────────────────────────────────────────
_CATEGORY_NORMALIZE: dict[str, str] = {
    "公示公告": "其他",
    "政策发布": "行业政策",
    "政策法规": "行业政策",
    "新闻动态": "其他",
    "规划计划": "行业政策",
    "行政审批": "其他",
    "公务员考试": "公招信息",
    "政策解读": "行业政策",
    "非公告体裁": "其他",
    "军队招录": "招生信息",
}


# ── 机构名清洗 ──────────────────────────────────────────────
_ORG_SUFFIX_RE = re.compile(
    r"(政府信息公开目录|政务公开|信息公开目录|门户网站|官方网站|首页)$"
)


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

    # source 节点：用 sources.yaml 的可读名称，fallback 到 issuing_body（清洗后缀）
    src_map = _load_source_name_map()
    issuing_body = _text(extra.get("issuing_body"))
    source_display = src_map.get(announcement.source_id) or _clean_org_name(issuing_body) or announcement.source_id
    source_id = _node_id("source", announcement.source_id)
    _upsert_node(session, source_id, "source", source_display, {"source_id": announcement.source_id, "url": announcement.source_url})
    _upsert_edge(session, announcement, announcement_id, "PUBLISHED_BY", source_id, "source_id", announcement.source_id, extractor_version=extractor_version)

    # organization 节点：清洗机构名，缺失时跳过（不再回退到 source_id）
    if issuing_body:
        org_name = _clean_org_name(issuing_body)
        organization_id = _node_id("organization", org_name)
        _upsert_node(session, organization_id, "organization", org_name, {})
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
        # l1 规范化到标准枚举，l2 保持原值
        display_value = _normalize_category(value) if level == "l1" else _text(value)
        if not display_value:
            continue
        node_id = _node_id("category", f"{level}:{display_value}")
        category_nodes[level] = node_id
        _upsert_node(session, node_id, "category", display_value, {"level": level})
        _upsert_edge(session, announcement, announcement_id, "CLASSIFIED_AS", node_id, f"category_{level}", display_value, extractor_version=extractor_version)
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


def _cleanup_orphan_nodes(session: Any) -> int:
    """删除没有任何边连接的孤立节点，返回删除数"""
    connected_ids = set()
    for row in session.execute(select(GraphEdge.source_node_id)).all():
        connected_ids.add(row[0])
    for row in session.execute(select(GraphEdge.target_node_id)).all():
        connected_ids.add(row[0])
    all_nodes = list(session.scalars(select(GraphNode)).all())
    orphans = [n for n in all_nodes if n.id not in connected_ids]
    for n in orphans:
        session.delete(n)
    return len(orphans)


def _sync_semantic_edges(session: Any, announcements: list[Announcement], extractor_version: str) -> int:
    """在公告之间创建语义关联边：
    - SAME_PROGRAM: 共享同一 project_tag（专项关联）
    - CO_REGION_CATEGORY: 同省+同分类（地区政策聚类）
    每条公告最多 5 条语义边，避免边爆炸。
    """
    edge_count = 0
    max_semantic_per_ann = 5

    def _make_semantic_edge(ann: Announcement, other: Announcement, edge_type: str, attrs: dict, confidence: int) -> bool:
        nonlocal edge_count
        ann_node = f"announcement:{ann.id}"
        other_node = f"announcement:{other.id}"
        edge_key = hashlib.sha256(
            f"semantic:{ann.id}:{other.id}:{edge_type}:{attrs}".encode()
        ).hexdigest()
        existing = session.scalar(select(GraphEdge).where(GraphEdge.edge_key == edge_key))
        if existing is not None:
            return False
        # 创建 evidence（NOT NULL 约束）
        evidence = _upsert_evidence(session, ann, f"semantic.{edge_type}", str(attrs))
        edge = GraphEdge(
            edge_key=edge_key,
            edge_type=edge_type,
            source_node_id=ann_node,
            target_node_id=other_node,
            source_announcement_id=ann.id,
            evidence_id=evidence.id,
            attributes=attrs,
            confidence=confidence,
            needs_review=False,
            extractor_version=extractor_version,
        )
        session.add(edge)
        edge_count += 1
        return True

    # 1. SAME_PROGRAM: 按 project_tag 分组
    tag_groups: dict[str, list[Announcement]] = {}
    for ann in announcements:
        for tag in ann.project_tags or []:
            if tag:
                tag_groups.setdefault(tag, []).append(ann)

    for tag, group in tag_groups.items():
        if len(group) < 2:
            continue
        group_sorted = sorted(group, key=lambda a: (a.publish_date or "", a.id))
        for i, ann in enumerate(group_sorted):
            if edge_count > 50000:
                break
            connected = 0
            for j in range(i + 1, len(group_sorted)):
                if connected >= max_semantic_per_ann:
                    break
                if _make_semantic_edge(ann, group_sorted[j], "SAME_PROGRAM", {"shared_tag": tag}, 80):
                    connected += 1

    # 2. CO_REGION_CATEGORY: 同省+同分类，按时间近邻连接
    region_cat_groups: dict[str, list[Announcement]] = {}
    for ann in announcements:
        if ann.region_province and ann.category_l1:
            cat = _normalize_category(ann.category_l1)
            key = f"{ann.region_province}:{cat}"
            region_cat_groups.setdefault(key, []).append(ann)

    for key, group in region_cat_groups.items():
        if len(group) < 2 or len(group) > 200:
            continue
        group_sorted = sorted(group, key=lambda a: (a.publish_date or "", a.id))
        for i, ann in enumerate(group_sorted):
            if edge_count > 80000:
                break
            connected = 0
            for j in range(i + 1, min(i + 4, len(group_sorted))):
                if connected >= 2:
                    break
                if _make_semantic_edge(ann, group_sorted[j], "CO_REGION_CATEGORY", {"region_category": key}, 60):
                    connected += 1

    return edge_count


def rebuild_graph(db: Database, config_path: str = "config/graph.yaml") -> dict[str, int]:
    config = load_graph_config(config_path)
    if not config.get("enabled", True):
        return {"announcements": 0, "nodes": 0, "edges": 0, "evidence": 0}
    extractor_version = str(config.get("extractor_version") or "structural-v1")
    with db.session() as session:
        # 全量清理：删除所有旧边、证据、节点，避免已删除公告的残留数据
        session.execute(delete(GraphEdge))
        session.execute(delete(GraphEvidence))
        session.execute(delete(GraphNode))
        session.flush()

        announcements = list(session.scalars(select(Announcement).order_by(Announcement.id)).all())
        for announcement in announcements:
            _sync_announcement(session, announcement, extractor_version)
        session.flush()

        # 语义关联边
        semantic_count = _sync_semantic_edges(session, announcements, extractor_version)
        session.flush()

        # 清理孤立节点（demo 残留等）
        orphan_count = _cleanup_orphan_nodes(session)
        session.flush()

        nodes = int(session.scalar(select(func.count()).select_from(GraphNode)) or 0)
        edges = int(session.scalar(select(func.count()).select_from(GraphEdge)) or 0)
        evidence = int(session.scalar(select(func.count()).select_from(GraphEvidence)) or 0)
    return {"announcements": len(announcements), "nodes": nodes, "edges": edges, "evidence": evidence, "semantic_edges": semantic_count, "orphans_removed": orphan_count}


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
        # edge_key 含 announcement.id —— 同一对节点每条公告都建一条边。
        # 探索视图看的是「这个节点连着谁」，2422 条重复的 PART_OF 没法读。
        seen: set[tuple[str, str, str]] = set()
        items: list[dict[str, Any]] = []
        for edge, direction in [(edge, "outgoing") for edge in outgoing] + [(edge, "incoming") for edge in incoming]:
            other_id = edge.target_node_id if direction == "outgoing" else edge.source_node_id
            key = (edge.edge_type, direction, other_id)
            if key in seen:
                continue
            seen.add(key)
            items.append(item(edge, direction))
        return {"node": {"id": node.id, "type": node.node_type, "name": node.canonical_name, "attributes": node.attributes or {}}, "neighbors": items}


def graph_search(db: Database, keyword: str = "", limit: int = 20) -> list[dict[str, Any]]:
    """按名称找节点 —— 图谱页没有全量节点列表，搜索是唯一的入口。

    空关键词返回按名称排序的前 N 个，让用户至少能开始点。
    """
    with db.session() as session:
        query = select(GraphNode)
        text = (keyword or "").strip()
        if text:
            query = query.where(GraphNode.canonical_name.like(f"%{text}%"))
        # 18 条空标题记录建出的无名节点 canonical_name='' —— 按长度排序会全排最前，
        # 默认列表就成了垃圾清单，直接不给看。
        query = query.where(func.trim(GraphNode.canonical_name) != "")
        # 短名优先：机构/地域节点名短，公告标题动辄几十字 —— 按名称字母序
        # 排会让公告把实体节点挤出前 20，搜索就白做了。
        query = query.order_by(func.length(GraphNode.canonical_name), GraphNode.canonical_name)
        rows = list(session.scalars(query.limit(max(1, min(limit, 50)))).all())
        return [{"id": row.id, "type": row.node_type, "name": row.canonical_name} for row in rows]


def graph_sample(db: Database, max_nodes: int = 250, max_edges: int = 400) -> dict[str, Any]:
    """返回用于可视化的节点和边样本（按度数排序取 top-N）。

    优先展示语义边（SAME_PROGRAM / CO_REGION_CATEGORY），
    因为它们比结构边（PUBLISHED_BY 等）更有分析价值。

    Returns:
        {"nodes": [{"id","name","type","value"}], "edges": [{"source","target","type"}], "categories": [...]}
    """
    # 语义边优先展示
    _SEMANTIC_TYPES = {"SAME_PROGRAM", "CO_REGION_CATEGORY"}
    # 边类型 → 线样式
    _EDGE_STYLE = {
        "SAME_PROGRAM": {"lineStyle": {"type": "solid", "color": "#6c5ce7", "width": 2}},
        "CO_REGION_CATEGORY": {"lineStyle": {"type": "dashed", "color": "#a29bfe", "width": 1.5}},
        "PUBLISHED_BY": {"lineStyle": {"type": "solid", "color": "#74b9ff", "width": 1}},
        "ISSUED_BY": {"lineStyle": {"type": "solid", "color": "#55efc4", "width": 1}},
        "CLASSIFIED_AS": {"lineStyle": {"type": "solid", "color": "#ffeaa7", "width": 1}},
        "LOCATED_IN": {"lineStyle": {"type": "dotted", "color": "#dfe6e9", "width": 1}},
        "PART_OF": {"lineStyle": {"type": "dotted", "color": "#b2bec3", "width": 0.8}},
        "MENTIONS": {"lineStyle": {"type": "dashed", "color": "#fab1a0", "width": 1}},
        "TARGETS": {"lineStyle": {"type": "dashed", "color": "#fd79a8", "width": 1}},
    }

    with db.session() as session:
        # 采样策略：非 announcement 节点全取（source+org+category+region ≈ 384），
        # announcement 节点按度数取 top-N
        non_ann_nodes = list(session.scalars(
            select(GraphNode).where(GraphNode.node_type != "announcement")
        ).all())
        ann_nodes = list(session.scalars(
            select(GraphNode).where(GraphNode.node_type == "announcement").limit(max_nodes * 3)
        ).all())
        all_nodes = non_ann_nodes + ann_nodes
        if not all_nodes:
            return {"nodes": [], "edges": [], "categories": []}

        node_ids = {n.id for n in all_nodes}
        # 全量取边（45k 条不慢），用于准确计算度数
        all_edges = list(
            session.scalars(
                select(GraphEdge)
                .where(GraphEdge.source_node_id.in_(node_ids))
                .where(GraphEdge.target_node_id.in_(node_ids))
            ).all()
        )

        # 按度数排序
        degree: dict[str, int] = {}
        for edge in all_edges:
            degree[edge.source_node_id] = degree.get(edge.source_node_id, 0) + 1
            degree[edge.target_node_id] = degree.get(edge.target_node_id, 0) + 1

        # 采样策略：按类型限额，确保图谱展示结构骨架 + 高价值公告
        # source: 全取 | organization: top 30 | category: top 20 | region: 全取
        # announcement: 剩余名额按度数取
        _TYPE_LIMITS = {"source": 999, "organization": 30, "category": 20, "region": 999, "announcement": 0}

        non_ann_by_type: dict[str, list] = {}
        ann_list = []
        for n in all_nodes:
            if n.node_type == "announcement":
                ann_list.append(n)
            else:
                non_ann_by_type.setdefault(n.node_type, []).append(n)

        sorted_nodes = []
        non_ann_count = 0
        for ntype, nodes in non_ann_by_type.items():
            limit = _TYPE_LIMITS.get(ntype, 20)
            # 按度数排序取 top
            nodes_sorted = sorted(nodes, key=lambda n: degree.get(n.id, 0), reverse=True)[:limit]
            sorted_nodes.extend(nodes_sorted)
            non_ann_count += len(nodes_sorted)

        # announcement 填剩余名额
        ann_slots = max(0, max_nodes - non_ann_count)
        ann_sorted = sorted(ann_list, key=lambda n: degree.get(n.id, 0), reverse=True)
        sorted_nodes.extend(ann_sorted[:ann_slots])
        kept_ids = {n.id for n in sorted_nodes}
        kept_edges_all = [e for e in all_edges if e.source_node_id in kept_ids and e.target_node_id in kept_ids]

        # 优先保留语义边，其次结构边（PART_OF 优先级最低）
        _LOW_PRIORITY = {"PART_OF"}
        semantic_edges = [e for e in kept_edges_all if e.edge_type in _SEMANTIC_TYPES]
        structural_edges = [e for e in kept_edges_all if e.edge_type not in _SEMANTIC_TYPES and e.edge_type not in _LOW_PRIORITY]
        low_priority_edges = [e for e in kept_edges_all if e.edge_type in _LOW_PRIORITY]
        kept_edges = (semantic_edges + structural_edges + low_priority_edges)[:max_edges]

        # 节点类型分类
        type_set = sorted({n.node_type for n in sorted_nodes})
        categories = [{"name": t} for t in type_set]
        type_index = {t: i for i, t in enumerate(type_set)}

        nodes = [
            {
                "id": n.id,
                "name": n.canonical_name[:30],  # 30 字符，够看标题前半段
                "category": type_index.get(n.node_type, 0),
                "value": degree.get(n.id, 1),
                "symbolSize": min(35, 5 + degree.get(n.id, 1) * 2),
            }
            for n in sorted_nodes
        ]
        edges = [
            {
                "source": e.source_node_id,
                "target": e.target_node_id,
                "value": e.edge_type,
                "lineStyle": _EDGE_STYLE.get(e.edge_type, {}).get("lineStyle", {}),
            }
            for e in kept_edges
        ]

        return {"nodes": nodes, "edges": edges, "categories": categories}


