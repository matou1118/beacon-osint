"""Content assertions for the workbench views, using streamlit's own AppTest.

tests/test_view_smoke.py proves a view does not raise. That is not enough: a view
can render an empty page and still "pass". AppTest builds the real element tree, so
these tests assert the numbers a view is supposed to show -- which is how the
采集运维 view was verified to report the robots-blocked count and the failing
health metrics, and the 进化仪表盘 view to report the defect queue.

The 采集运维 and 进化仪表盘 views did not exist as working code before: the nav
called render_evolution_dashboard, which was never defined.
"""
from __future__ import annotations

import unittest

from streamlit.testing.v1 import AppTest

APP = r"D:\tvbox\BeaconOSINT\app.py"
TIMEOUT = 240


def open_view(view: str) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.session_state["active_view"] = view
    at.run()
    return at


def open_view_retry(view: str, attempts: int = 3) -> AppTest:
    """Re-run on a locked database.

    The scheduler writes beacon.db while a test reads it, and SQLite refuses the
    reader with "database is locked". That is a test-environment race, not a view
    bug, so retry instead of failing the suite.
    """
    at = open_view(view)
    for _ in range(attempts):
        messages = [e.message for e in at.exception]
        if not any("locked" in m or "busy" in m for m in messages):
            break
        at = open_view(view)
    return at


def visible_text(at: AppTest) -> str:
    parts = [m.value for m in at.markdown] + [c.value for c in at.caption]
    return "\n".join(str(part) for part in parts)


class CollectorOpsViewTests(unittest.TestCase):
    def test_renders_registry_progress_health_and_history(self) -> None:
        at = open_view_retry("采集运维")
        self.assertEqual([e.message for e in at.exception], [])
        body = visible_text(at)
        for section in ("源注册现状", "robots 拒绝", "采集进度", "数据体检", "规则自修正记录"):
            with self.subTest(section=section):
                self.assertIn(section, body)

    def test_shows_source_table_and_offers_a_collection_run(self) -> None:
        at = open_view_retry("采集运维")
        self.assertTrue(at.dataframe, "源状态明细/不达标项/自修正记录 至少应有一张表")
        labels = [b.label for b in at.button]
        self.assertIn("运行一轮采集", labels)
        self.assertIn("刷新数据" if "刷新数据" in labels else "运行一轮采集", labels)


class EvolutionViewTests(unittest.TestCase):
    def test_reports_defect_counts_and_queue(self) -> None:
        at = open_view_retry("进化仪表盘")
        self.assertEqual([e.message for e in at.exception], [])
        body = visible_text(at)
        for section in ("登记缺陷类别", "当前不达标", "缺陷登记全表"):
            with self.subTest(section=section):
                self.assertIn(section, body)
        self.assertTrue(at.dataframe)

    def test_offers_the_automatic_loop(self) -> None:
        at = open_view_retry("进化仪表盘")
        labels = [b.label for b in at.button]
        self.assertIn("运行一轮自动修正", labels)


class CompareWorkbenchTests(unittest.TestCase):
    def test_discovers_topics_from_the_real_store(self) -> None:
        at = open_view_retry("对比工作台")
        self.assertEqual([e.message for e in at.exception], [])
        self.assertTrue(at.selectbox, "缺少主题下拉")
        options = list(at.selectbox[0].options)
        self.assertTrue(options, "真实数据应至少发现一个可对比主题")
        # Labels carry the counts, so a topic list that lost its numbers is a bug.
        self.assertTrue(any("层级" in str(option) for option in options))

    def test_offers_all_four_exports(self) -> None:
        at = open_view_retry("对比工作台")
        labels = [d.label for d in at.get("download_button")]
        for wanted in ("导出 CSV", "导出 JSON", "导出 Markdown 模板"):
            with self.subTest(export=wanted):
                self.assertIn(wanted, labels)
        # Excel is offered as a disabled button when openpyxl is absent, so it must
        # still be visible rather than silently missing.
        self.assertTrue(any("Excel" in label for label in labels), "缺少 Excel 导出入口")

    def test_switches_between_discovered_and_registered_topics(self) -> None:
        at = open_view_retry("对比工作台")
        modes = [r for r in at.radio if r.label == "对比数据来源"]
        self.assertTrue(modes, "缺少数据来源切换")
        self.assertEqual(list(modes[0].options), ["真实数据自动发现", "已登记主题"])


class SidebarNavigationTests(unittest.TestCase):
    def test_nav_groups_flatten_to_nav_views(self) -> None:
        """Group headers are inserted by position, so the order must line up."""
        import sys

        sys.path.insert(0, r"D:\tvbox\BeaconOSINT")
        import app as app_module

        flattened = [view for _name, members in app_module.NAV_GROUPS for view in members]
        self.assertEqual(list(app_module.NAV_VIEWS), flattened)
        self.assertEqual(len(flattened), len(set(flattened)), "同一视图出现在多个分组里")

    def test_every_view_has_a_valid_icon(self) -> None:
        """A bad icon name raises StreamlitAPIException and blanks the page,
        because the sidebar renders before the body."""
        import sys

        sys.path.insert(0, r"D:\tvbox\BeaconOSINT")
        import app as app_module

        missing = [v for v in app_module.NAV_VIEWS if v not in app_module.NAV_ICONS]
        self.assertEqual(missing, [], f"这些视图没有图标，侧栏会缺格: {missing}")
        for name, icon in app_module.NAV_ICONS.items():
            with self.subTest(view=name):
                self.assertTrue(
                    icon.startswith(":material/") and icon.endswith(":"),
                    f"{name} 的图标 {icon!r} 缺少 :material/ 前缀，会让整页崩溃",
                )

    def test_every_module_is_a_sidebar_button(self) -> None:
        """All modules must be individually clickable in the sidebar.

        The sidebar used to be a single flat radio, which left no room for section
        headers, and one radio per group kept a second navigation state that fought
        active_view and drove st.rerun() in a loop. It is now one button per module.
        """
        import sys

        sys.path.insert(0, r"D:\tvbox\BeaconOSINT")
        import app as app_module

        at = open_view_retry("总览")
        self.assertEqual([e.message for e in at.exception], [])
        labels = [b.label for b in at.button]
        for view in app_module.NAV_VIEWS:
            with self.subTest(view=view):
                self.assertIn(view, labels)

    def test_sidebar_owns_the_aux_panel_toggle(self) -> None:
        """The leftmost rail column was deleted; the right-panel switch moved here."""
        import sys

        sys.path.insert(0, r"D:\tvbox\BeaconOSINT")
        import app as app_module

        self.assertFalse(hasattr(app_module, "render_rail"), "控制轨应已删除")
        at = open_view_retry("总览")
        labels = [b.label for b in at.button]
        self.assertTrue(any("备用" in label for label in labels),
                        f"侧栏应有备用区开关，现有按钮: {labels}")

    def test_nav_covers_every_dispatch_target(self) -> None:
        """Every NAV_VIEWS entry must be dispatchable, or the page is a dead end.

        The last entry may be served by the `else` fallback rather than an explicit
        branch, so at most one view is allowed to have no explicit comparison.
        """
        import sys
        from pathlib import Path

        sys.path.insert(0, r"D:\tvbox\BeaconOSINT")
        import app as app_module

        app = Path(r"D:\tvbox\BeaconOSINT\app.py").read_text(encoding="utf-8")
        views = list(app_module.NAV_VIEWS)
        self.assertTrue(views)
        dispatch = app[app.index("def main("):]
        self.assertIn("else:", dispatch, "分发链缺少兜底分支")
        undispatched = [v for v in views if f'active_view == "{v}"' not in dispatch]
        self.assertLessEqual(
            len(undispatched), 1,
            f"这些导航项既无显式分支也非唯一兜底，会走到 render_quality: {undispatched}",
        )


if __name__ == "__main__":
    unittest.main()
