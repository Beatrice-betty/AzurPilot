import threading
import unittest
from contextlib import nullcontext
from unittest.mock import patch

from module.webui.app_statistics_page import StatisticsPageMixin


class _OutputStub:
    def style(self, _value):
        return self


class _TaskHandlerStub:
    def __init__(self):
        self.added = []

    def add(self, func, delay, pending_delete=False):
        self.added.append((func, delay, pending_delete))


class _StatisticsHarness(StatisticsPageMixin):
    def __init__(self):
        self.alas_name = "alas"
        self.page = "Overview"
        self._page_lock = threading.Lock()
        self._statistics_cache_key = None
        self._statistics_source_signature = None
        self._statistics_refresh_pending = False
        self.signature = "v1"
        self.rendered = []
        self.put_scopes = []
        self.cleaned = []
        self.task_handler = _TaskHandlerStub()

    def init_menu(self, name=None):
        self.page = name
        self._overview_panel = "stat"

    def _remove_stale_scopes(self, names):
        """真实实现在 OverviewMixin 上；这里只需可调用。"""
        self.stale_cleaned = getattr(self, "stale_cleaned", [])
        self.stale_cleaned.append(tuple(names))
        if hasattr(self, "order") and "stale" not in self.order:
            self.order.append("stale")

    def run_js(self, *args, **kwargs):
        self.ran_js = getattr(self, "ran_js", [])
        self.ran_js.append((args, kwargs))

    def set_title(self, _title):
        return None

    def cleanup_client_resources(self, *names):
        self.cleaned.append(names)

    def _get_statistics_source_signature(self):
        return self.signature

    def _record_put_scope(self, name, *args, **kwargs):
        self.put_scopes.append(name)
        if hasattr(self, "order") and "scope" not in self.order:
            self.order.append("scope")
        return _OutputStub()

    def _render_ap_chart(self):
        self.rendered.append("ap")
        if hasattr(self, "order") and "render" not in self.order:
            self.order.append("render")

    def _render_resource_chart(self):
        self.rendered.append("resource")

    def _render_opsi_stats(self):
        self.rendered.append("opsi")

    def _render_ship_exp(self):
        self.rendered.append("ship")

    def _render_commission_income(self):
        self.rendered.append("commission")


class TestStatisticsPageCache(unittest.TestCase):
    def setUp(self):
        self.gui = _StatisticsHarness()
        self.patches = (
            patch(
                "module.webui.app_statistics_page.use_scope",
                side_effect=lambda *_args, **_kwargs: nullcontext(),
            ),
            patch(
                "module.webui.app_statistics_page.put_scope",
                side_effect=self.gui._record_put_scope,
            ),
            patch(
                "module.webui.app_statistics_page.put_button",
                return_value=_OutputStub(),
            ),
            patch("module.webui.app_statistics_page.t", side_effect=lambda key: key),
            patch("module.webui.app_statistics_page.run_js"),
        )
        for active_patch in self.patches:
            active_patch.start()

    def tearDown(self):
        for active_patch in reversed(self.patches):
            active_patch.stop()

    def test_reopening_page_remounts_because_container_is_rebuilt(self):
        """每次进统计二级页都要重挂，不能「已装配就复用」。

        进页的 init_menu 会 clear("content")，上回挂在
        statistics-content 下的 scope 随之消失。只刷新会往不存在的
        scope 里写，页面成空壳 —— 实测连续【打开】【总览】第 2 次
        起必现（统计页空白）。所以这里要求每次都重新渲染一遍。
        """
        self.gui.alas_set_stat()
        self.assertEqual(
            ["ap", "resource", "opsi", "ship", "commission"],
            self.gui.rendered,
        )

        self.gui.rendered.clear()
        self.gui.alas_set_stat()

        self.assertEqual(
            ["ap", "resource", "opsi", "ship", "commission"],
            self.gui.rendered,
            "二次进页没有重挂；容器已被清空时会渲染成空壳。",
        )
        self.assertEqual(2, len(self.gui.task_handler.added))
        for callback, delay, pending_delete in self.gui.task_handler.added:
            self.assertEqual("_refresh_statistics_if_changed", callback.__name__)
            self.assertEqual(15, delay)
            self.assertTrue(pending_delete)

    def test_local_data_change_marks_refresh_without_replacing_sections(self):
        self.gui.alas_set_stat()
        self.gui.rendered.clear()
        self.gui.signature = "v2"

        self.gui._refresh_statistics_if_changed()

        self.assertEqual([], self.gui.rendered)
        self.assertTrue(self.gui._statistics_refresh_pending)

        self.gui._refresh_statistics_page()

        self.assertEqual(
            ["ap", "resource", "opsi", "ship", "commission"],
            self.gui.rendered,
        )
        self.assertEqual("v2", self.gui._statistics_source_signature)
        self.assertFalse(self.gui._statistics_refresh_pending)

    def test_switching_instance_replaces_cache_and_cleans_charts(self):
        self.gui.alas_set_stat()
        self.gui.rendered.clear()
        self.gui.alas_name = "alas2"

        self.gui.alas_set_stat()

        self.assertEqual(
            ["ap", "resource", "opsi", "ship", "commission"],
            self.gui.rendered,
        )
        self.assertEqual(
            [("__apChartCleanups", "__resourceChartCleanups")],
            self.gui.cleaned,
        )

    def test_background_check_does_not_render_after_navigation(self):
        self.gui.alas_set_stat()
        self.gui.rendered.clear()
        self.gui.signature = "v2"
        self.gui.page = "Overview"

        self.gui._refresh_statistics_if_changed()

        self.assertEqual([], self.gui.rendered)


if __name__ == "__main__":
    unittest.main()

class TestStatPanelMounting(unittest.TestCase):
    """锁定挂载顺序：先建好全部子 scope，再渲染。

    实测过两次踩坑：
      * 先建空壳、再由「另一条路径」渲染 → 内容写进尚未拿到 id 的节点而丢失；
      * 干脆不建壳 → 渲染时 use_scope 的 create_scope 以当前作用域为父创建，
        而 run_js 会把当前作用域重置为 ROOT，于是图表挂到 ROOT 下，
        撑爆 ROOT 的 grid 行、把 contents 压成 0，左侧设置栏塌陷。
    正确做法：在挂载点作用域内 put_scope 建壳，随后渲染。
    use_scope(name) 见 scope 已存在 → 只清空、不创建、不移动，天然幂等。
    """

    def setUp(self):
        self.gui = _StatisticsHarness()
        self.patches = (
            patch(
                "module.webui.app_statistics_page.use_scope",
                side_effect=lambda *_args, **_kwargs: nullcontext(),
            ),
            patch(
                "module.webui.app_statistics_page.put_scope",
                side_effect=self.gui._record_put_scope,
            ),
            patch(
                "module.webui.app_statistics_page.put_button",
                return_value=_OutputStub(),
            ),
            patch("module.webui.app_statistics_page.t", side_effect=lambda key: key),
            patch("module.webui.app_statistics_page.run_js"),
        )
        for active_patch in self.patches:
            active_patch.start()

    def tearDown(self):
        for active_patch in reversed(self.patches):
            active_patch.stop()

    def test_mount_creates_all_stat_scopes(self):
        """挂载时必须在挂载点内建好全部子 scope。"""
        self.gui._mount_stat_panels("stat_panels")
        for name in self.gui.STAT_PANEL_SCOPES:
            self.assertIn(name, self.gui.put_scopes,
                          f"{name} 必须在挂载时建好，否则渲染时会挂到 ROOT 下")

    def test_mount_order_is_cleanup_then_shells(self):
        """顺序必须是：先清游离节点，再在挂载点内建壳。

        反过来的话，渲染时 use_scope 的 create_scope 会以当前作用域
        （run_js 之后已变成 ROOT）为父创建，图表就挂到 ROOT 下了。
        挂载本身不渲染（渲染由调用方负责，避免同一路径渲染两份）。
        """
        self.gui.order = []
        self.gui._mount_stat_panels("stat_panels")
        self.assertEqual(["stale", "scope"], self.gui.order)
        self.assertEqual([], self.gui.rendered)

    def test_cleanup_happens_before_scopes(self):
        """游离节点清理必须早于建壳（run_js 会把作用域重置为 ROOT）。"""
        self.gui.order = []
        self.gui._mount_stat_panels("stat_panels")
        self.assertIn("stale", self.gui.order)
        self.assertLess(self.gui.order.index("stale"),
                        self.gui.order.index("scope"))

    def test_page_mount_renders_exactly_once(self):
        self.gui.alas_set_stat()
        self.assertEqual(
            ["ap", "resource", "opsi", "ship", "commission"],
            self.gui.rendered,
        )
