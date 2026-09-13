"""概览页渲染 + 右栏「日志/统计」切换条的行为测试。

用打桩 harness 真正调用 alas_overview()，验证：
- 右栏两个面板都被挂载，且切换条渲染在其后
- 默认面板为统计，服务端偏好生效
- 切换只改显隐（不重新挂载面板），并双写状态
- 非法面板值被拒绝
"""
import unittest
import re
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from module.webui.app_overview import OverviewMixin
from module.webui.webui_prefs import PANEL_LOG, PANEL_STAT
from module.webui import webui_prefs


class _OutputStub:
    def style(self, *_args, **_kwargs):
        return self


class _TaskHandlerStub:
    def __init__(self):
        self.added = []

    def add(self, func, delay, pending_delete=False):
        self.added.append((func, delay, pending_delete))


class _OverviewHarness(OverviewMixin):
    """只提供 alas_overview 需要的最小环境，其余全部打桩。"""

    def __init__(self):
        self.alas_name = "UItest"
        self.page = "Overview"
        self.scopes = []          # 记录 put_scope 调用顺序
        self.rendered = []        # 记录面板挂载顺序
        self.js = []              # 记录 run_js 脚本
        self.localstorage = {}    # 模拟浏览器 localStorage
        self.task_handler = _TaskHandlerStub()
        self._overview_log = None
        self._overview_log_config_name = None
        self._overview_snapshot = None
        self.ALAS_ARGS = ("OpsiAshBeacon",)
        self.alas_config = _AlasConfig()

    # --- 打桩方法 ---
    def init_menu(self, name=None):
        self.page = name

    def set_title(self, _title):
        return None

    def alas_set_stat(self):
        self.page = "Stat"

    def alas_set_log(self):
        self.page = "Log"

    def active_button(self, position, value):
        self.scopes.append(("active_button", position, value))

    def _alas_start(self):
        return None

    def alas_update_dashboard(self, *_a):
        return None

    def alas_update_overview_task(self):
        return None

    def set_dashboard_display(self, _b):
        return None

    def _mount_stat_panels(self, mount_scope):
        """真实实现会在 mount_scope 内建壳，这里记录挂载点即可。"""
        self.rendered.append("stat_panels")
        self.mount_scope = mount_scope

    def _render_statistics_sections(self):
        self.rendered.append("sections")


class _AlasConfig:
    def __init__(self):
        self.groups = []




class TestOverviewPanelSwitch(unittest.TestCase):
    def setUp(self):
        self.gui = _OverviewHarness()
        self.panel_calls = []
        self.put_scope_names = []
        self.button_calls = []    # 记录 (scope, label)

        def fake_put_scope(name="", content=None, **kwargs):
            self.put_scope_names.append(name)
            return _OutputStub()

        def fake_get_localstorage(key, **_kwargs):
            return self.gui.localstorage.get(key)

        def fake_set_localstorage(key, value, **_kwargs):
            self.gui.localstorage[key] = value

        def fake_run_js(script, **_kwargs):
            self.gui.js.append(script)

        def fake_eval_js(expression, **kwargs):
            """同步清理走 eval_js：记录表达式并返回删除计数。"""
            self.gui.js.append(expression)
            return len(kwargs.get("names") or [])

        self.patches = (
            patch("module.webui.app_overview.put_scope", side_effect=fake_put_scope),
            patch("module.webui.app_overview.use_scope",
                  side_effect=lambda *_a, **_k: nullcontext()),
            patch("module.webui.app_overview.put_text", return_value=_OutputStub()),
            patch("module.webui.app_overview.put_html", return_value=_OutputStub()),
            patch("module.webui.app_overview.put_none", return_value=_OutputStub()),
            patch("module.webui.app_overview.put_button",
                  side_effect=lambda *a, **k: (
                      self.button_calls.append(
                          (k.get("scope"), a[0] if a else k.get("label")))
                      or _OutputStub())),
            patch("module.webui.app_overview.put_buttons",
                  side_effect=lambda buttons, **k: self.panel_calls.append(buttons) or _OutputStub()),
            patch("module.webui.app_overview.t", side_effect=lambda key: key),
            patch("module.webui.app_overview.get_device_id", return_value="dev"),
            patch("module.webui.app_overview.is_demo_mode", return_value=False),
            patch("module.webui.app_overview.run_js", side_effect=fake_run_js),
            patch("module.webui.app_overview.eval_js", side_effect=fake_eval_js),
            patch("module.webui.app_overview.get_localstorage", side_effect=fake_get_localstorage),
            patch("module.webui.app_overview.set_localstorage", side_effect=fake_set_localstorage),
            patch("module.webui.app_overview.RichLog", return_value=_FakeLog()),
            patch("module.webui.app_overview.LogRes", return_value=_AlasConfig()),
            patch("module.webui.app_overview.OverviewMixin.alas_update_overview_task",
                  create=True),
            patch("module.webui.app_overview.updater"),
            patch.object(webui_prefs, "_PREFS_FILE", _tmp_prefs()),
        )
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        _tmp_prefs().unlink(missing_ok=True)

    def test_right_column_has_both_panels(self):
        self.gui.alas_overview()
        for need in ("overview", "stat_panels", "logs", "schedulers"):
            self.assertIn(need, self.put_scope_names, f'右栏缺 {need}')

    def test_buttons_are_in_panel_bar_of_panel_column(self):
        """切换与「打开」两个按钮在 panel_column 的 panel_bar 里。"""
        self.gui.alas_overview()
        self.assertNotIn("panel_switch", self.put_scope_names, "旧切换条应已删除")
        for need in ("panel_column", "panel_bar", "stat-bar",
                     "panel_toggle_btn", "panel_open_btn"):
            self.assertIn(need, self.put_scope_names, f'缺 {need}')
        labels = [label for _scope, label in self.button_calls]
        self.assertIn("Gui.Button.Open", labels, "应有「打开」按钮")
        self.assertIn("Gui.Overview.Log", labels, "默认右栏显示统计，文案应为「日志」")

    def test_both_panels_mounted(self):
        self.gui.alas_overview()
        self.assertIn("stat_panels", self.gui.rendered)

    def test_default_panel_is_stat(self):
        self.gui.alas_overview()
        self.assertEqual(PANEL_STAT, self.gui._overview_panel)
    def test_visibility_js_targets_both_panels(self):
        self.gui.alas_overview()
        self.gui._overview_panel = PANEL_LOG
        js = self.gui._overview_panel_visibility_js()
        self.assertIn('var active = "log"', js)
        self.assertIn("'stat_panels'", js)
        self.assertIn("'logs'", js)
    def test_server_pref_wins(self):
        webui_prefs.set_overview_panel("log")
        self.gui.alas_overview()
        self.assertEqual(PANEL_LOG, self.gui._overview_panel)

    def test_browser_cache_used_when_server_empty(self):
        self.gui.localstorage["alas_overview_panel"] = "log"
        self.gui.alas_overview()
        self.assertEqual(PANEL_LOG, self.gui._overview_panel)

    def test_switch_updates_state_and_double_writes(self):
        self.gui.alas_overview()
        self.gui.rendered.clear()
        self.gui.js.clear()

        self.gui._switch_overview_panel(PANEL_LOG)

        self.assertEqual(PANEL_LOG, self.gui._overview_panel)
        self.assertEqual("log", webui_prefs.get_pref("overview_panel"))
        self.assertEqual("log", self.gui.localstorage["alas_overview_panel"])
        self.assertTrue(any('var active = "log"' in s for s in self.gui.js))
        # 只改显隐，不重新挂载面板
        self.assertEqual([], self.gui.rendered, '切换不应重新挂载面板')

    def test_switch_rejects_unknown_value(self):
        self.gui.alas_overview()
        self.gui._switch_overview_panel("bogus")
        self.assertEqual(PANEL_STAT, self.gui._overview_panel)

    # ---------- 改进1：仅显示时刷新 ----------

    def test_periodic_refresh_requires_overview_page(self):
        """切页后必须早退：否则 use_scope 找不到元素，会在 ROOT 下写幽灵 scope。"""
        self.gui.alas_overview()
        self.gui._overview_panel = PANEL_STAT
        self.gui.page = "Setting"           # 已切走
        self.gui.rendered.clear()
        self.gui._render_stat_panel_refresh()
        self.assertEqual([], self.gui.rendered, '不在概览页时不应重绘统计')

    def test_periodic_refresh_requires_mounted_content(self):
        """统计内容未挂载过时不应重绘（没有可复用的容器）。"""
        self.gui.alas_overview()
        self.gui.page = "Overview"
        self.gui._overview_panel = PANEL_STAT
        self.gui._statistics_cache_key = None
        self.gui.rendered.clear()
        self.gui._render_stat_panel_refresh()
        self.assertEqual([], self.gui.rendered, '未挂载时不应重绘统计')

    def test_switching_into_stat_refreshes_immediately(self):
        self.gui.alas_overview()
        self.gui._overview_panel = PANEL_LOG
        self.gui.rendered.clear()

        self.gui._switch_overview_panel("stat")

        self.assertIn("sections", self.gui.rendered, "切到统计应立即刷新")

    def test_switching_into_log_does_not_refresh_stats(self):
        self.gui.alas_overview()
        self.gui._overview_panel = PANEL_STAT
        self.gui.rendered.clear()

        self.gui._switch_overview_panel(PANEL_LOG)

        self.assertNotIn("sections", self.gui.rendered, "切到日志不必刷新统计")

    def test_periodic_task_registered_is_conditional(self):
        self.gui.alas_overview()
        names = [f.__name__ for f, _d, _p in self.gui.task_handler.added]
        self.assertIn("_render_stat_panel_refresh", names)
        self.assertNotIn("_render_statistics_sections", names,
                         "不应再直接注册无条件全量刷新")

    # ---------- 改进2：两个按钮 ----------

    def test_open_button_opens_the_page_named_by_the_button(self):
        """「打开」进二级菜单显示的那一页 = 切换按钮写着的那一页。

        契约：
          按钮【统计】→ 右栏显示日志栏，点【打开】进【统计页】
          按钮【日志】→ 右栏显示统计栏，点【打开】进【日志页】
        文案是当前面板的反面，所以「打开」也按反面判。
        """
        self.gui.alas_overview()

        # 右栏显示日志（文案【统计】）→ 打开统计页
        self.gui._overview_panel = PANEL_LOG
        self.gui._open_secondary_page()
        self.assertEqual("Stat", self.gui.page,
                         '右栏显示日志、按钮写【统计】，点打开却进了别的页。')

        # 右栏显示统计（文案【日志】）→ 打开日志页
        self.gui._overview_panel = PANEL_STAT
        self.gui._open_secondary_page()
        self.assertEqual("Log", self.gui.page,
                         '右栏显示统计、按钮写【日志】，点打开却进了别的页。')

    def test_toggle_label_and_open_target_agree(self):
        """文案与「打开」的目标必须一致 —— 这是两次取反抵消的病根。

        只测一侧会漏：文案反、判定也反时，单看「打开进对页」是通的，
        但按钮文字与右栏内容是反的。所以两种面板状态都要交叉验证。
        """
        for active, label, page in ((PANEL_STAT, "Gui.Overview.Log", "Log"),
                                    (PANEL_LOG, "Gui.Overview.Stat", "Stat")):
            with self.subTest(active=active):
                self.gui._overview_panel = active
                self.button_calls.clear()
                self.gui._render_panel_bar()
                labels = [lb for _s, lb in self.button_calls]
                self.assertIn(label, labels,
                              f'active={active} 时文案应为 {label}')
                self.gui._open_secondary_page()
                self.assertEqual(page, self.gui.page,
                                 f'active={active} 时点打开应进 {page}')

    def test_toggle_label_names_the_target_panel(self):
        """文案是「点击后会切到哪一栏」，与右栏当前显示的面板相反。

        ``_overview_panel`` 记录右栏**当前显示**的面板（见
        _overview_panel_visibility_js：active='log' → logs 显示）。
        按钮写【日志】表示点它切到日志栏；此时右栏显示的正是统计。

        历史坑：文案与「打开」的判定两处都取反会刚好抵消 ——
        「点开能进对页」看着一直正常，但按钮文字与右栏内容是反的。
        所以文案、判定必须一起改，且测试要同时钉住这两侧。
        """
        self.gui.alas_overview()

        # 右栏显示日志（active=log）→ 文案「统计」（点它切到统计）
        self.button_calls.clear()
        self.gui._overview_panel = PANEL_LOG
        self.gui._render_panel_bar()
        labels = [label for _s, label in self.button_calls]
        self.assertIn("Gui.Overview.Stat", labels, '右栏显示日志时文案应为「统计」')

        # 右栏显示统计（active=stat）→ 文案「日志」（点它切到日志）
        self.button_calls.clear()
        self.gui._overview_panel = PANEL_STAT
        self.gui._render_panel_bar()
        labels = [label for _s, label in self.button_calls]
        self.assertIn("Gui.Overview.Log", labels, '右栏显示统计时文案应为「日志」')

    def test_toggle_switches_to_the_named_panel(self):
        """点击后右栏显示文案所指的面板。"""
        self.gui.alas_overview()
        # 文案「日志」（active=stat）→ 点击后右栏显示日志（active=log）
        self.gui._overview_panel = PANEL_STAT
        self.gui._switch_overview_panel(PANEL_LOG)
        self.assertEqual(PANEL_LOG, self.gui._overview_panel)

    def test_resource_bar_is_independent_of_panels(self):
        """资源栏与 stat_panels / logs 平级，不嵌在 logs 内。

        嵌在 logs 内就会跟着面板一起被隐藏 —— 切到【统计】时资源栏消失，
        这正是要修的问题。
        """
        code = read_src('module/webui/app_overview.py')
        start = code.index('        put_scope(\n            "overview",')
        body = code[start:code.index('        with use_scope("schedulers"):')]
        # dashboard 与 stat_panels / logs 同在 panel_column 的列表里
        self.assertIn('put_scope("dashboard")', body)
        self.assertIn('put_scope("stat_panels")', body)
        self.assertIn('put_scope("logs")', body)
        self.assertLess(body.index('put_scope("dashboard")'),
                        body.index('put_scope("stat_panels")'),
                        'dashboard 应排在面板之前（滚动栏上方）')
        # log-bar 里不应再有 dashboard
        log_panel = code[code.index('    def _render_log_panel(self) -> None:'):
                         code.index('    @use_scope("content", clear=True)')]
        self.assertNotIn('put_scope("dashboard")', log_panel,
                         'dashboard 不应再嵌在 log-bar 内')

    def test_dashboard_is_first_row_of_panel_column(self):
        """资源栏在右栏首行整宽 —— 这样上边框才能与调度器对齐。

        曾经把它放进「第 2 行左格」，结果夹在调度器栏与滚动栏之间。
        """
        css = read_src('assets/gui/css/alas.css')
        sel = ('#pywebio-scope-overview > #pywebio-scope-panel_column '
               '> #pywebio-scope-dashboard')
        rules = [body for sels, body in _css_rules(css) if sel in sels]
        self.assertTrue(rules, '缺资源栏规则')
        joined = ' '.join(rules)
        self.assertIn('grid-column: 1', joined, '资源栏应占整栏宽')
        self.assertIn('grid-row: 1', joined, '资源栏应在首行')
        # 两块可切换面板共用第 2 行
        for child in ('stat_panels', 'logs'):
            sel2 = ('#pywebio-scope-overview > #pywebio-scope-panel_column '
                    f'> #pywebio-scope-{child}')
            hit = [b for s, b in _css_rules(css) if sel2 in s]
            self.assertTrue(hit, f'{child} 缺少定位规则')
            self.assertIn('grid-row: 2', ' '.join(hit), f'{child} 应在第 2 行')

    def test_dashboard_has_panel_appearance(self):
        """资源栏外观与 log-bar 一致（上游它嵌在 log-bar 内才带上这些）。"""
        css = read_src('assets/gui/css/alas.css')
        sel = ('#pywebio-scope-overview > #pywebio-scope-panel_column '
               '> #pywebio-scope-dashboard')
        joined = ' '.join(b for s, b in _css_rules(css) if sel in s)
        for prop in ('border:', 'border-radius:', 'background:'):
            self.assertIn(prop, joined, f'资源栏缺 {prop}')

    def test_panel_column_can_shrink(self):
        """panel_column 必须能收缩，否则资源栏高度会自我强化把容器顶大。"""
        css = read_src('assets/gui/css/alas.css')
        sel = '#pywebio-scope-overview > #pywebio-scope-panel_column'
        hit = [body for sels, body in _css_rules(css) if sel in sels]
        self.assertTrue(hit, '缺 panel_column 规则')
        joined = ' '.join(hit)
        self.assertIn('min-height: 0', joined, 'panel_column 需 min-height:0')
        self.assertIn('overflow: hidden', joined, 'panel_column 需 overflow:hidden')

    def test_logs_css_keeps_two_row_contract(self):
        """logs 保持两行（log-bar / log-container）；dashboard 在 log-bar 内。"""
        css = read_src('assets/gui/css/alas.css')
        block = css[css.index('#pywebio-scope-logs {'):]
        block = block[:block.index('}')]
        self.assertIn('auto 1fr', block, 'logs 应保持两行契约')


    def test_log_secondary_page_exists(self):
        """必须存在对等的日志二级页，否则「打开」文案为【日志】时无处可去。"""
        code = read_src('module/webui/app_overview.py')
        self.assertIn('def alas_set_log', code, '缺日志二级页入口')
        self.assertIn('def _render_log_page', code, '缺日志二级页渲染')
        self.assertIn('LOG_PAGE_SCOPE', code, '日志页需要独立作用域')
        base = read_src('module/webui/base.py')
        self.assertIn('"Log": "log-content"', base, '外壳需为日志页提供槽位')
        self.assertIn('log-content', base, '槽位需在 _show 中创建')
class _FakeLog:
    def __init__(self, *_a, **_k):
        self.scope = "log"
        self.first_display = True
        self.last_display_time = {}
        self.dashboard_arg_group = []
        self.keep_bottom = True
        self.display_dashboard = True
        self.console = type("C", (), {"width": 0})()

    def get_width(self):
        return 100

    def sync_width(self):
        """正式实现见 widgets.RichLog.sync_width（需真实会话）。"""
        return None

    def set_scroll(self, _v):
        return None

    def put_log(self, _alas):
        return lambda: None


_TMP = None


def read_src(rel):
    """读取仓库源文件，用于断言结构与 CSS 契约。"""
    return (Path(__file__).resolve().parent.parent / rel).read_text(encoding='utf-8')


def _css_rules(css):
    """把 CSS 拆成 (选择器文本, 规则体) 列表；去注释，支持多行合并选择器。"""
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    out = []
    for block in css.split('}'):
        if '{' not in block:
            continue
        sels, body = block.split('{', 1)
        out.append((sels.strip(), body.strip()))
    return out


def _tmp_prefs():
    global _TMP
    if _TMP is None:
        import tempfile
        from pathlib import Path
        _TMP = Path(tempfile.mkdtemp()) / "webui_prefs.json"
    return _TMP


if __name__ == "__main__":
    unittest.main(verbosity=2)
