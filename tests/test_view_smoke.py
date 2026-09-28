"""Smoke test: every navigation view must render without raising.

A streamlit view that raises does not fail the app, it just blanks the page from
that point on. Because the nav rail renders before the body, an exception in a
view function left the user staring at a page with no toolbar and no menu, with
the traceback only visible in the browser.

`st.selectbox` returning one value that the view unpacked into two
("too many values to unpack") killed 关联确认 exactly this way, and no unit test
covered it because none of them call render().

Each view runs in a subprocess in streamlit bare mode with active_view seeded,
so one broken view cannot hide the others.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RUNNER = """
import sys
sys.path.insert(0, r"{root}")
import streamlit as st
st.session_state["active_view"] = sys.argv[1]
import app
print("VIEW_OK", sys.argv[1])
"""

# Kept in sync with app.NAV_VIEWS; the guard test below fails if they drift.
VIEWS = [
    "总览", "中国地图", "公告检索", "对比工作台", "关联确认", "字段复核",
    "订阅中心", "采集运维", "灯塔采集", "来源健康", "知识图谱", "热频话题", "进化仪表盘",
    "质量与备份",
]


def _nav_views() -> list[str]:
    """Read NAV_VIEWS from the module: it is a list comprehension now, so
    ast.literal_eval cannot parse it."""
    import sys

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import app as app_module

    return list(app_module.NAV_VIEWS)


class ViewSmokeTests(unittest.TestCase):
    def test_nav_views_list_matches_app(self) -> None:
        self.assertEqual(sorted(VIEWS), sorted(_nav_views()))

    def test_every_view_renders(self) -> None:
        runner = Path(__file__).with_name("_smoke_view_runner.py")
        runner.write_text(RUNNER.format(root=ROOT), encoding="utf-8")
        self.addCleanup(runner.unlink, missing_ok=True)
        failures: list[str] = []
        for view in VIEWS:
            with self.subTest(view=view):
                proc = subprocess.run(
                    [sys.executable, "-B", str(runner), view],
                    cwd=ROOT, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=300,
                )
                if f"VIEW_OK {view}" not in (proc.stdout or ""):
                    noise = [
                        line for line in (proc.stderr or "").splitlines()
                        if line.strip() and "Warning" not in line and "warnings.warn" not in line
                    ]
                    failures.append(f"{view}: {' | '.join(noise[-3:])[:400]}")
        self.assertEqual(failures, [], "views failed to render:\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
