"""全库聚合统计 —— 给 H5 前端仪表盘用。

只读既有 announcements 表，不改任何规则/来源。口径与列表接口一致：
排除 needs_review 和已归档，否则页面上「收录信息」和分布图对不上。
行业门类口径复用 extractors.category_rules.industry_l2_names()（与
Streamlit 首页同一套），不另写白名单。
"""

from __future__ import annotations

from typing import Any

from extractors.category_rules import industry_l2_names
from storage.db import Database

_BASE = "needs_review = 0 AND archived_at IS NULL"


def _rows(connection: Any, sql: str, params: tuple = ()) -> list[tuple]:
    return list(connection.exec_driver_sql(sql, params).fetchall())


def query_stats(db: Database) -> dict[str, Any]:
    out: dict[str, Any] = {
        "total": 0,
        "corroborated": 0,
        "provinces": 0,
        "categories": 0,
        "cat_data": [],
        "ind_data": [],
        "prov_ind_data": [],
        "prov_data": [],
        "year_data": [],
        "gov_level": [],
        "day_data": [],
    }
    inds = industry_l2_names()
    placeholders = ",".join("?" for _ in inds)
    with db.engine.connect() as conn:
        row = _rows(
            conn,
            "SELECT COUNT(*), "
            "COALESCE(SUM(corroboration_status = 'corroborated'), 0), "
            "COUNT(DISTINCT NULLIF(TRIM(region_province), '')), "
            "COUNT(DISTINCT NULLIF(TRIM(category_l1), '')) "
            f"FROM announcements WHERE {_BASE}",
        )[0]
        out["total"], out["corroborated"], out["provinces"], out["categories"] = (int(v or 0) for v in row)

        # 不截断：前端分类徽标要每个分类的全库数量（LIMIT 会让长尾分类显示不出计数）
        out["cat_data"] = [
            {"name": name, "value": n}
            for name, n in _rows(
                conn,
                "SELECT COALESCE(NULLIF(TRIM(category_l1), ''), '其他') AS cat, COUNT(*) AS n "
                f"FROM announcements WHERE {_BASE} GROUP BY cat ORDER BY n DESC",
            )
        ]
        if inds:
            out["ind_data"] = [
                {"name": name, "value": n}
                for name, n in _rows(
                    conn,
                    "SELECT category_l2, COUNT(*) AS n FROM announcements "
                    f"WHERE {_BASE} AND category_l2 IN ({placeholders}) "
                    "GROUP BY category_l2 ORDER BY n DESC LIMIT 14",
                    tuple(inds),
                )
            ]
            out["prov_ind_data"] = [
                {"prov": prov, "ind": ind, "value": n}
                for prov, ind, n in _rows(
                    conn,
                    "SELECT region_province, category_l2, COUNT(*) AS n FROM announcements "
                    f"WHERE {_BASE} AND TRIM(COALESCE(region_province, '')) != '' "
                    f"AND category_l2 IN ({placeholders}) "
                    "GROUP BY region_province, category_l2",
                    tuple(inds),
                )
            ]
        out["prov_data"] = [
            {"name": name, "value": n}
            for name, n in _rows(
                conn,
                "SELECT region_province, COUNT(*) AS n FROM announcements "
                f"WHERE {_BASE} AND TRIM(COALESCE(region_province, '')) != '' "
                "GROUP BY region_province ORDER BY n DESC LIMIT 10",
            )
        ]
        out["year_data"] = [
            {"year": year, "count": n}
            for year, n in _rows(
                conn,
                "SELECT substr(publish_date, 1, 4), COUNT(*) FROM announcements "
                f"WHERE {_BASE} AND publish_date GLOB '[0-9][0-9][0-9][0-9]*' "
                "GROUP BY 1 ORDER BY 1",
            )
        ]
        out["gov_level"] = [
            {"name": name, "value": n}
            for name, n in _rows(
                conn,
                "SELECT COALESCE(NULLIF(TRIM(gov_level), ''), '未标注'), COUNT(*) "
                f"FROM announcements WHERE {_BASE} GROUP BY 1 ORDER BY 2 DESC",
            )
        ]
        recent = [
            {"day": day, "count": n}
            for day, n in _rows(
                conn,
                "SELECT substr(publish_date, 1, 10), COUNT(*) FROM announcements "
                f"WHERE {_BASE} AND publish_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' "
                "GROUP BY 1 ORDER BY 1 DESC LIMIT 30",
            )
        ]
        out["day_data"] = list(reversed(recent))
    return out
