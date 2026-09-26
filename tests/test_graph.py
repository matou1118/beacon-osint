from __future__ import annotations

import unittest

from graph_service import graph_neighbors, graph_summary, rebuild_graph
from storage.db import Database
from storage.models import GraphEdge, GraphEvidence


class GraphTests(unittest.TestCase):
    def make_database(self) -> Database:
        database = Database(":memory:")
        database.upsert_announcement(
            {
                "source_id": "sc_hr",
                "source_url": "https://example.gov.cn/notice/1",
                "title": "人才补贴通知",
                "content": "面向企业发布人才补贴通知。",
                "issuing_body": "四川省人社厅",
                "gov_level": "省级",
                "region_province": "四川省",
                "region_city": "成都市",
                "category_l1": "行业补贴",
                "category_l2": "人才补贴",
                "target_group": "企业",
                "target_industry": "环保",
                "project_tags": ["示范项目"],
            }
        )
        return database

    def test_rebuild_is_idempotent_and_preserves_evidence(self) -> None:
        database = self.make_database()
        first = rebuild_graph(database)
        second = rebuild_graph(database)
        self.assertEqual(first, second)
        self.assertGreater(first["nodes"], 5)
        self.assertGreater(first["edges"], 5)
        with database.session() as session:
            self.assertEqual(session.query(GraphEdge).count(), first["edges"])
            self.assertEqual(session.query(GraphEvidence).count(), first["evidence"])
            evidence = session.query(GraphEvidence).first()
            self.assertEqual(evidence.source_url, "https://example.gov.cn/notice/1")
        database.close()

    def test_node_neighbors_include_structural_relation(self) -> None:
        database = self.make_database()
        rebuild_graph(database)
        data = graph_neighbors(database, "announcement:1")
        self.assertIsNotNone(data)
        self.assertIn("ISSUED_BY", {item["edge_type"] for item in data["neighbors"]})
        self.assertIn("LOCATED_IN", {item["edge_type"] for item in data["neighbors"]})
        database.close()

    def test_summary_counts_nodes_and_edges(self) -> None:
        database = self.make_database()
        rebuild_graph(database)
        summary = graph_summary(database)
        self.assertEqual(summary["nodes"], rebuild_graph(database)["nodes"])
        self.assertIn("announcement", summary["node_types"])
        database.close()


if __name__ == "__main__":
    unittest.main()
