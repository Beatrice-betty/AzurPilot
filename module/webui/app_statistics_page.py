"""WebUI 统计页装配器。"""

from datetime import date
from pathlib import Path

import module.webui.lang as lang
from module.webui.app_dependencies import put_button, put_scope, run_js, t, use_scope
from module.webui.app_types import WebUIMixinBase
from module.webui.webui_prefs import PANEL_STAT

class StatisticsPageMixin(WebUIMixinBase):
    """惰性装配并复用统计子视图。"""

    def alas_set_stat(self) -> None:
        """显示统计页：每次进页都重挂一份子视图。

        不能「已装配就复用」——二级页的容器每次进页都被重建，
        旧 scope 已不在文档里，复用等于往空处写。
        """
        self.init_menu(name="Stat")
        self.set_title(t("Gui.Overview.Stat"))
        if not hasattr(self, "_ap_chart_view"):
            self._ap_chart_view = "line"
        if not hasattr(self, "_commission_income_period"):
            self._commission_income_period = "month"

        # 每次进页都重挂：init_menu 会 clear("content") 并带走上次的 scope，
        # 只刷新会写进已不存在的 scope（第 2 次进入必现）
        self._mount_statistics_page(self._get_statistics_cache_key())

        # 原先的 5 个任务会在进页后立即各重绘一次。现在仅轮询本地数据源
        # 版本并提示存在新数据，不再打断用户正在查看的图表状态。
        self.task_handler.add(self._refresh_statistics_if_changed, 15, True)

    def _mount_statistics_page(self, cache_key) -> None:
        """在 statistics-content 下建好统计内容。

        每次进统计二级页都会调，先清掉上一份再重建。
        """
        with self._page_lock:
            if getattr(self, "page", None) != "Stat":
                return
            if getattr(self, "_statistics_cache_key", None) is not None:
                self.cleanup_client_resources(
                    "__apChartCleanups",
                    "__resourceChartCleanups",
                )

            with use_scope("statistics-content", clear=True):
                self._mount_stat_panels("statistics-content")

            self._statistics_cache_key = cache_key
            self._render_statistics_sections()
            self._statistics_source_signature = (
                self._get_statistics_source_signature()
            )
            self._statistics_refresh_pending = False

    STAT_PANEL_SCOPES = (
        "ap_chart",
        "resource_chart",
        "opsi_stats",
        "ship_exp_table",
        "commission_income",
    )

    def _is_statistics_context(self) -> bool:
        """当前是否处于能容纳统计子视图的上下文（概览右栏或统计页）。"""
        page = getattr(self, "page", None)
        if page == "Stat":
            return True
        if page != "Overview":
            return False
        return getattr(self, "_overview_panel", PANEL_STAT) == PANEL_STAT

    def _mount_stat_panels(self, mount_scope: str) -> None:
        """建好统计子视图的 scope；调用方负责在正确的挂载点内调用。

        统计图表既可铺满独立统计页，也可内嵌概览右栏，所以 scope 必须建在
        当前挂载点内。四条实测教训决定了这里的写法：

        1) 清理旧节点必须在建壳之前，且必须是同步的。清理用的 run_js 源码就是
           send_msg —— 发完立即返回，建壳消息会先到客户端并撞名；因此改用会等
           返回的 eval_js。
        2) eval_js / run_js 都会把 PyWebIO 的当前作用域重置为 ROOT。所以清理
           之后必须重新进入 mount_scope 再建壳，否则 use_scope 的 create_scope
           会以 ROOT 为父创建图表 scope，撑爆 ROOT 的 grid 行、压塌左侧设置栏。
        3) 先建壳再渲染：渲染时的 use_scope 见 scope 已存在，走 if_exist='blank'
           分支——只清空、不创建、不移动，内容照常写入，天然幂等。
        4) 每次挂载都是全新的一份，所以不需要搬迁逻辑。
        """
        # 清掉上一处挂载点残留的游离节点：仍带 id 但已脱离文档，
        # 会让随后的 use_scope 找到错误的元素。
        #
        # 注意 run_js 会把 PyWebIO 的当前作用域重置为 ROOT，所以清理必须在
        # 建壳之前完成，且之后立刻在挂载点作用域内建好所有子 scope。
        with use_scope(mount_scope):
            self._remove_stale_scopes(self.STAT_PANEL_SCOPES)  # 内部用 eval_js
            # ap-chart-refresh 是模板里的 div（随 ap_chart 一起被清），
            # 不在 STAT_PANEL_SCOPES 里 —— 它由 put_html 提供，不预建，
            # 否则同名节点会撞（「此scope与已有scope重复」）。
            # 图表 scope 必须在这里就地建壳（此刻仍在挂载点作用域内）。
            # 若留到渲染时才创建，use_scope 的 create_scope 会以当前作用域
            # （清理后已变成 ROOT）为父，把图表挂到 ROOT 下，撑爆 ROOT 的 grid
            # 行并把左侧设置栏压塌。先建壳后，渲染时的 use_scope 走
            # if_exist='blank' 分支：只清空、不创建、不移动，内容照常写入。
            for _name in self.STAT_PANEL_SCOPES:
                put_scope(_name)

    def _refresh_statistics_page(self) -> None:
        """刷新已挂载的全部统计模块。

        统计容器既可能铺满统计二级页，也可能内嵌在概览右栏，
        因此不能按 page == "Stat" 拦；只要求内容已挂载过。
        """
        with self._page_lock:
            if getattr(self, "_statistics_cache_key", None) is None:
                return
            if not self._is_statistics_context():
                # 不在概览右栏或统计页时刷新会在 ROOT 下写出幽灵 scope，
                # 之后挂载时撞名（「此scope与已有scope重复」）。
                return

            self._render_statistics_sections()
            self._statistics_source_signature = (
                self._get_statistics_source_signature()
            )
            self._set_statistics_refresh_pending(False)

    def _refresh_statistics_if_changed(self) -> None:
        """检测当前实例的本地统计数据变化并更新刷新提示。"""
        if getattr(self, "page", None) != "Stat":
            return
        if (
            getattr(self, "_statistics_cache_key", None)
            != self._get_statistics_cache_key()
        ):
            return

        source_signature = self._get_statistics_source_signature()
        self._set_statistics_refresh_pending(
            source_signature
            != getattr(self, "_statistics_source_signature", None)
        )

    def _set_statistics_refresh_pending(self, pending: bool) -> None:
        """只更新刷新提示，不替换用户正在查看的统计 DOM。"""
        if pending == getattr(self, "_statistics_refresh_pending", False):
            return
        self._statistics_refresh_pending = pending
        run_js(
            """
            (function () {
                var holder = document.getElementById(
                    "pywebio-scope-ap-chart-refresh"
                );
                var button = holder && holder.querySelector("button");
                if (!button) return;
                button.classList.toggle("statistics-refresh-pending", pending);
                button.title = pending ? refreshHint : "";
                button.setAttribute(
                    "aria-label",
                    pending ? refreshHint : button.textContent.trim()
                );
            })();
            """,
            pending=pending,
            refreshHint=t("Gui.Stat.NewDataAvailable"),
        )

    def _render_statistics_sections(self) -> None:
        """统一刷新各统计子视图。"""
        self._render_ap_chart()
        self._render_resource_chart()
        self._render_opsi_stats()
        self._render_ship_exp()
        self._render_commission_income()

    def _get_statistics_cache_key(self):
        """返回会影响统计页文案与数据归属的键。"""
        return getattr(self, "alas_name", None), lang.LANG

    def _get_statistics_source_signature(self):
        """以廉价的文件版本检查代替重复解析和绘图。"""
        project_root = Path(__file__).resolve().parents[2]
        instance_name = getattr(self, "alas_name", None) or "default"
        paths = (
            project_root / "config" / "cl1_data.db",
            project_root / "config" / "cl1_data.db-wal",
            project_root / "config" / "azurstats_local.db",
            project_root / "config" / "azurstats_local.db-wal",
            project_root / "log" / "cl1" / instance_name / "ship_exp_data.json",
        )
        return date.today().isoformat(), tuple(
            self._get_statistics_file_version(path) for path in paths
        )

    @staticmethod
    def _get_statistics_file_version(path: Path):
        try:
            stat = path.stat()
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_size
