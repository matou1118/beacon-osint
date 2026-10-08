"""采集后的统一后处理：交叉验证 → 聚类热度 → 写库。

**这里解决的是一个断口，不是一个新功能。**

``main.py`` 一直在跑这条链条（交叉验证、媒体聚簇、热度、抽取留痕、图谱），
但 ``lighthouse`` 链路绕过了它直接入库。后果实测过：

* ``announcements`` 9467 条，``corroboration_status`` 88% 是默认的 single_source
* ``heat_topics`` **0 行**——聚簇函数 ``media_cluster`` 一直在跑，但对政务数据
  永远返回空列表，所以那张表永远是空的
* ``attachments`` 264 行，只覆盖旧链路

``heat_topics`` 空的根因在 ``rules/media_cluster.py`` 的闸门：它只处理媒体记录，
而本项目 145/145 个源都是 government。闸门已放开，本模块负责把链条真正接上。

拆成两段是有意的：

* :func:`analyze` —— 纯内存，不碰库。可以单独跑、可以试算、可以喂 bench。
* :func:`persist` —— 写库。附件是网络操作，单独开关。

所以"接线"只调 analyze+persist，存量回填也能复用同一对函数。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from rules.corroboration import corroborate_records, corroboration_summary
from rules.media_cluster import cluster_media_records, heat_summary

_ROOT = Path(__file__).resolve().parents[1]
RULES_YAML = _ROOT / "config" / "rules.yaml"


def load_rules(path: str | None = None) -> dict[str, Any]:
    """读 ``config/rules.yaml``。

    刻意不并进 ``lighthouse.quality.load_config``——那个读的是
    ``web_collector_config.yaml``，采集器的路由和门禁。交叉验证和热度的判据在
    ``rules.yaml``，是主项目规则引擎那份。两个配置的归属不同，混在一起以后
    就分不清哪个参数管哪件事了。
    """
    import yaml

    with open(path or RULES_YAML, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def source_index(sources: Sequence[Mapping[str, Any]] | None) -> dict[str, Mapping[str, Any]]:
    """source_id → 源登记项。

    ``source_type`` 的权威来源是 ``config/sources.yaml``，不是记录本身——记录里
    这一字段历史上基本是空的（9467 条里 6982 条为 None），而交叉验证和热度
    都按它判定「这是不是一手官方源」。从库里回读的老记录要靠这个索引补回来。
    """
    index: dict[str, Mapping[str, Any]] = {}
    for source in sources or ():
        for key in ("id", "source_id", "slug"):
            value = str(source.get(key) or "")
            if value:
                index[value] = source
                break
    return index


def _attach_source_type(records: list[dict[str, Any]],
                        index: Mapping[str, Mapping[str, Any]]) -> int:
    """按 source_id 补 ``source_type``。返回补上的条数。"""
    filled = 0
    for record in records:
        if str(record.get("source_type") or "").strip():
            continue
        source = index.get(str(record.get("source_id") or ""))
        if source and source.get("source_type"):
            record["source_type"] = str(source["source_type"])
            filled += 1
    return filled


def analyze(records: list[dict[str, Any]], rules: Mapping[str, Any],
            now: datetime | None = None,
            sources: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """跑交叉验证和聚簇，把结果写回 record。不碰数据库。

    返回统计，便于日志和测试断言。
    """
    current = now or datetime.now(timezone.utc)
    filled = _attach_source_type(records, source_index(sources))

    checked = corroborate_records(records, rules)
    clusters = cluster_media_records(checked, rules, now=current)

    # 聚簇结果按 URL 回填到记录上，和 main.py 原有行为一致。
    by_url = {str(url): topic for topic in clusters
              for url in (topic.get("article_urls") or [])}
    for record in checked:
        topic = by_url.get(str(record.get("source_url") or ""))
        if topic is None:
            continue
        record["media_cluster_id"] = topic.get("cluster_id")
        record["media_heat"] = topic.get("heat_score")
        record["media_terms"] = topic.get("terms") or []

    return {
        "records": checked,
        "clusters": clusters,
        "corroboration": corroboration_summary(checked),
        "heat": heat_summary(clusters),
        "source_type_filled": filled,
    }


def persist(database: Any, records: list[dict[str, Any]],
            clusters: list[Mapping[str, Any]], now: datetime | None = None,
            with_extractions: bool = True,
            with_graph: bool = True) -> dict[str, Any]:
    """把 analyze 的结果写库。附件下载不在这里（那是网络操作，另行处理）。"""
    from graph_service import rebuild_graph

    current = now or datetime.now(timezone.utc)
    result: dict[str, Any] = {}
    database.upsert_heat_topics(clusters, current.date().isoformat())
    if with_extractions:
        result["field_extractions"] = database.save_field_extractions(records)
    if with_graph:
        try:
            result["graph"] = rebuild_graph(database)
        except Exception as exc:  # 图谱失败不该拖垮整轮采集
            result["graph"] = {"error": str(exc)}
    return result


def run_postprocess(database: Any, records: list[dict[str, Any]], rules: Mapping[str, Any],
                    now: datetime | None = None,
                    sources: Sequence[Mapping[str, Any]] | None = None,
                    with_extractions: bool = True,
                    with_graph: bool = True) -> dict[str, Any]:
    """analyze + persist，一步到位。采集收尾和存量回填共用这一对函数。"""
    outcome = analyze(records, rules, now=now, sources=sources)
    written = persist(database, outcome["records"], outcome["clusters"], now=now,
                      with_extractions=with_extractions, with_graph=with_graph)
    return {
        "records": len(outcome["records"]),
        "clusters": len(outcome["clusters"]),
        "corroboration": outcome["corroboration"],
        "heat": outcome["heat"],
        "source_type_filled": outcome["source_type_filled"],
        "written": written,
    }
