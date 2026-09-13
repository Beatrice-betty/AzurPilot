"""WebUI实例概览和守护模式"""

import os
import sys
import threading

from pywebio.session import register_thread

def _current_theme() -> str:
    """当前生效的 WebUI 主题名。

    State.theme 是主题的权威来源（见 app_shell.add_css_files）。
    这里用函数内导入：module.webui.setting 在导入期会牵到本模块，
    模块级导入会成环。
    """
    from module.webui.setting import State

    return State.theme or 'default'


from module.webui.material_sliders import (
    build_html as build_material_sliders_html,
    build_js as build_material_sliders_js,
)

from module.webui.app_dependencies import (
    BinarySwitchButton,
    LogRes,
    RichLog,
    deep_iter,
    get_device_id,
    get_localstorage,
    json,
    pin,
    put_buttons,
    put_button,
    put_html,
    put_none,
    put_success,
    put_scope,
    put_text,
    eval_js,
    put_error,
    put_input,
    put_link,
    put_markdown,
    put_textarea,
    put_warning,
    run_js,
    set_localstorage,
    t,
    updater,
    use_scope,
)

from module.webui.app_helpers import (
    DEMO_DEVICE_ID_TEXT,
    is_demo_mode,
)

from module.webui.webui_prefs import (
    LOCALSTORAGE_KEYS,
    PANEL_LOG,
    PANEL_STAT,
    get_background_urls,
    get_overview_panel,
    set_background_urls,
    set_overview_panel,
)

from module.webui.background_image import (
    EXTRACTED_DIR_NAME,
    SOURCE_LOCAL,
    SOURCE_MIXED,
    SOURCE_REMOTE,
    current_direct_url,
    ensure_background_dir,
    extract_to_local,
    list_images,
    local_image_url,
    pick_background,
    session_choice,
)

from module.logger import logger


from module.webui.app_types import WebUIMixinBase


class OverviewMixin(WebUIMixinBase):
    """WebUI实例概览和守护模式"""

    def _render_log_panel(self) -> None:
        """渲染日志面板到「logs」作用域，并注册日志相关周期任务。

        `log` 由 alas_overview 取得并存入 self._log，此处复用同一实例，
        避免两处各自创建导致日志流断开。
        """
        log = self._log
        log.first_display = True
        log.last_display_time = {}
        log.sync_width()  # 见 widgets.RichLog.sync_width：元素就绪后再测，持续跟随

        local_commit = updater.get_commit(short_sha1=True)
        version = local_commit[0] if local_commit and local_commit[0] else "Unknown"
        device_id = DEMO_DEVICE_ID_TEXT if is_demo_mode() else get_device_id()
        container_style = (
            f"--device-id: '{device_id}'; --version: 'Ver.{version}';"
        )

        with use_scope("logs"):
            if "Maa" in self.ALAS_ARGS:
                btns = [put_scope("log_scroll_btn")]
            else:
                btns = [
                    put_scope("log_scroll_btn"),
                    put_button(
                        label=t('Gui.Stat.ScreenshotPreviewBtn'),
                        onclick=lambda: run_js(
                            f"window.alasToggleLivePreview({json.dumps(self.alas_name)});"
                        ),
                        color="off",
                    ),
                    put_button(
                        label=t('Gui.Stat.BackgroundBtn'),
                        onclick=self._toggle_background_page,
                        color="off",
                    ),
                    put_scope("dashboard_btn"),
                ]
            title = put_text(t("Gui.Overview.Log")).style(
                "font-size: 1.25rem; margin: auto .5rem auto;"
            )
            put_scope(
                "log-container",
                [
                    put_scope(
                        "log-header",
                        [
                            put_scope("log-bar", [title]),
                            put_scope("log-bar-btns", btns),
                        ],
                    ),
                    put_scope("log", [put_html("")]),
                ],
            ).style(container_style)
            put_scope("log-bg-page")

        switch_log_scroll = BinarySwitchButton(
            label_on=t("Gui.Button.ScrollON"),
            label_off=t("Gui.Button.ScrollOFF"),
            onclick_on=lambda: log.set_scroll(False),
            onclick_off=lambda: log.set_scroll(True),
            get_state=lambda: log.keep_bottom,
            color_on="on",
            color_off="off",
            scope="log_scroll_btn",
        )
        switch_dashboard = BinarySwitchButton(
            label_on=t("Gui.Button.DashboardON"),
            label_off=t("Gui.Button.DashboardOFF"),
            onclick_on=lambda: self.set_dashboard_display(False),
            onclick_off=lambda: self.set_dashboard_display(True),
            get_state=lambda: log.display_dashboard,
            color_on="off",
            color_off="on",
            scope="dashboard_btn",
        )
        self.task_handler.add(switch_log_scroll.g(), 1, True)
        if "Maa" not in self.ALAS_ARGS:
            self.task_handler.add(switch_dashboard.g(), 1, True)
        self.task_handler.add(self.alas_update_overview_task, 10, True)
        if "Maa" not in self.ALAS_ARGS:
            self.task_handler.add(self.alas_update_dashboard, 10, True)
            # 首次绘制延后到 dashboard scope 建好之后（见 alas_overview 尾部）：
            # 这里提前调用会在 scope 尚不存在时把内容挂到 ROOT 下。
        if hasattr(self, "alas") and self.alas is not None:
            self.task_handler.add(log.put_log(self.alas), 0.25, True)

    def _toggle_background_page(self) -> None:
        """点【背景】：展开或收起背景设置页。

        只动右栏日志区，不影响左侧卡片与统计栏。
        """
        self._background_page_open = not self._background_page_open
        self._render_background_page()
        # 脚本同时管背景页的显和日志容器的隐，展开与收起都要发
        # 必须 eval_js：run_js 不等脚本执行就返回，会被后续 DOM 更新冲掉
        eval_js(self._overview_panel_visibility_js())

    def _save_background_urls(self) -> None:
        """保存自定义背景网址，并立即换上新背景。

        保存后重新抽签并重新注入样式：否则本会话还缓存着启动时那张
        （通常是主题内置），用户会觉得“保存了没反应”。
        """
        set_background_urls(self._background_url_draft or [])
        self._apply_background()
        self._render_background_page()

    @staticmethod
    def _apply_background() -> None:
        """清掉会话缓存的抽签结果，重新抽一张并重新注入样式。

        保存设置后必须走这里：否则本会话还缓存着启动时那张（通常是主题内置），
        用户会以为“保存了没反应”。
        """
        from module.webui.app_dependencies import local
        from module.webui.background_image import background_css_file
        from module.webui.utils import add_background_css

        local.webui_bg_choice = None
        if background_css_file() is not None:
            add_background_css()
    def _open_background_folder(self) -> None:
        """在系统文件管理器里打开 ``bg/``，方便往里放图。"""
        target = ensure_background_dir()
        try:
            os.startfile(str(target))  # type: ignore[attr-defined]
        except AttributeError:
            import subprocess
            opener = 'open' if sys.platform == 'darwin' else 'xdg-open'
            try:
                subprocess.Popen([opener, str(target)])
            except OSError as e:
                logger.warning(f'[WebUI-背景] 打开目录失败: {e}')
        except OSError as e:
            logger.warning(f'[WebUI-背景] 打开目录失败: {e}')

    def _extract_background_image(self) -> None:
        """把当前背景图下载到 ``bg/提取/``。

        下载的是「眼前这张」：自定义网址与网页默认都可能指向随机图接口，
        必须跟随跳转拿到落点，否则下到的是另一张。

        同一张图不会重复下载，所以对已经提取过的图再点也没副作用。
        """
        choice = session_choice() or pick_background()
        url = choice.get('css_url')
        if not url:
            with use_scope('bg-extract-result', clear=True):
                put_warning(t('Gui.Stat.ExtractNoImage'))
            return
        if choice.get('source') == SOURCE_LOCAL:
            with use_scope('bg-extract-result', clear=True):
                put_warning(t('Gui.Stat.ExtractAlreadyLocal'))
            return

        with use_scope('bg-extract-result', clear=True):
            put_text(t('Gui.Stat.ExtractRunning'))
        # 下载耗时数秒，同步做会卡住会话；register_thread 注册后才能在线程里回调页面
        worker = threading.Thread(target=self._extract_worker, args=(url, self))
        register_thread(worker)
        worker.start()

    @staticmethod
    def _extract_worker(url: str, sink) -> None:
        """后台线程：下载完成后把结果推回页面（线程已 register_thread 注册）。"""
        result = extract_to_local(url)
        sink._render_extract_result(result)


    @staticmethod
    def _page_origin() -> str:
        """取当前页面的 origin（如 http://127.0.0.1:26548）。

        服务器不知道自己对外的主机名端口（可能经反向代理），所以本地图
        的访问地址只能靠浏览器自己报。结果按会话缓存：同一个会话里
        origin 不变，也就不必每次重画都多问一次浏览器。

        后台线程里读不到（也拿不准 eval_js 是否可用），直接返回空串，
        调用方退回相对地址即可。
        """
        from module.webui.app_dependencies import local
        from pywebio.session import info as session_info

        cached = getattr(local, 'webui_bg_origin', None)
        if cached is not None:
            return cached
        try:
            origin = eval_js('window.location.origin') or ''
        except Exception as e:  # noqa: BLE001 - 后台线程等场景
            logger.warning(f'[WebUI-背景] eval_js 取 origin 失败: {e}')
            origin = ''
        if not origin:
            request = getattr(session_info, 'request', None)
            host = ''
            if request is not None:
                host = request.headers.get('host', '')
            if host:
                scheme = request.headers.get(
                    'x-forwarded-proto', 'http')
                origin = f'{scheme}://{host}'
        if origin:
            local.webui_bg_origin = origin
        return origin

    @classmethod
    def _display_url(cls, url: str) -> str:
        """把可能是相对路径的直链补成完整地址，方便复制后直接用。"""
        if url.startswith('/'):
            return f'{cls._page_origin()}{url}'
        return url

    def _render_background_page(self) -> None:
        """渲染背景设置页（一个可重复调用的幂等渲染）。

        三类来源：本地 ``bg/`` 目录、自定义网址、网页默认。
        优先级由 :func:`pick_background` 决定：本地 > 网址 > 默认。
        """
        if not self._background_page_open:
            return
        self._draw_background_page(self._background_page_widgets())

    def _background_page_widgets(self) -> list:
        """构建背景设置页的内容。

        与「画到页面」分开：提取结果是在后台线程里回写的，那边拿不到实例，
        所以重画走 :meth:`_background_page_widgets_static`。
        """
        return self._background_page_widgets_static(
            self._on_background_action)

    @staticmethod
    def _background_page_widgets_static(onclick) -> list:
        """构建背景页内容（不依赖实例，后台线程可用）。

        Args:
            onclick: 按钮回调。必须传绑定的实例方法：未绑定的类函数会被
                PyWebIO 把按钮值当成 self 传进来，action 永远是空（实测）。
        """
        choice = session_choice() or pick_background()
        n_local = len(list_images())
        n_urls = len(get_background_urls())
        status = OverviewMixin._background_status()
        direct = OverviewMixin._display_url(current_direct_url(choice))
        return [
            put_markdown(t('Gui.Overview.BackgroundTitle')),
            put_markdown(status),
            put_markdown(t('Gui.Overview.BackgroundDirectLink')),
            put_textarea(
                'bg_direct_url',
                rows=1,
                value=direct,
                readonly=False,
            ),
            (put_link(t('Gui.Overview.BackgroundOpenInNewWindow'), direct, new_window=True)
             if direct else put_none()),
            put_buttons(
                [
                    {'label': t('Gui.Overview.BackgroundOpenFolder'), 'value': 'local', 'color': 'off'},
                    {'label': t('Gui.Overview.BackgroundExtractToLocal'), 'value': 'extract', 'color': 'off'},
                ],
                onclick=onclick,
            ),
            put_markdown(t('Gui.Overview.BackgroundLocalCount', n=n_local)),
            put_markdown(t('Gui.Overview.BackgroundUrlCount', n=n_urls)),
            put_markdown(t('Gui.Overview.BackgroundCustomUrls')),
            put_textarea(
                'bg_urls',
                rows=4,
                value='\n'.join(get_background_urls()),
                placeholder='https://example.com/api.php',
            ),
            put_scope('bg-extract-result'),
        ] + OverviewMixin._material_sliders_widgets()

    @staticmethod
    def _material_sliders_widgets() -> list:
        """材质滑块的标记；非高级材质主题返回空列表（不显示）。"""
        html = build_material_sliders_html(_current_theme())
        return [put_html(html)] if html else []

    @staticmethod
    def _draw_background_page(widgets: list) -> None:
        """把背景页内容画到右栏（后台线程也可调用）。"""
        with use_scope('log-bg-page', clear=True):
            put_scope('bg-page', widgets).style('padding: 12px 14px;')
        js = build_material_sliders_js(_current_theme())
        if js:
            run_js(js)

    def _render_extract_result(self, result: dict) -> None:
        """展示提取结果。"""
        with use_scope('bg-extract-result', clear=True):
            if not result.get('ok'):
                put_error(t(
                    'Gui.Stat.ExtractFailed',
                    error=result.get('error') or t('Gui.Stat.ExtractUnknownError'),
                ))
                return
            if result.get('skipped'):
                put_success(t('Gui.Stat.ExtractSkipped', name=result.get('name')))
            else:
                put_success(t(
                    'Gui.Stat.ExtractDone',
                    sub=EXTRACTED_DIR_NAME, name=result.get('name'),
                ))
            put_text(t('Gui.Stat.ExtractHint'))
        self._draw_background_page(
            self._background_page_widgets_static(self._on_background_action))


    @staticmethod
    def _background_status() -> str:
        """这一张背景的来路，转成人能读的一行文案。

        只报来源类别：具体是哪张图由「当前图片直链」那栏给出，
        再复述一遍既冗余又容易与直链不一致。
        """
        choice = session_choice() or pick_background()
        source = choice.get('source')
        if source == SOURCE_LOCAL:
            return t('Gui.Overview.BackgroundCurrentLocal')
        if source == SOURCE_REMOTE:
            return t('Gui.Overview.BackgroundCurrentUrl')
        if source == SOURCE_MIXED:
            return t('Gui.Overview.BackgroundCurrentMixed')
        return t('Gui.Overview.BackgroundCurrentDefault')

    def _on_background_action(self, action) -> None:
        """背景页按钮分发。"""
        if action == 'save':
            try:
                self._background_url_draft = pin.bg_urls
            except Exception:  # noqa: BLE001 - 控件未就绪
                self._background_url_draft = get_background_urls()
            self._save_background_urls()

        elif action == 'local':
            self._open_background_folder()
        elif action == 'extract':
            self._extract_background_image()

    @use_scope("content", clear=True)
    def alas_overview(self) -> None:
        self.init_menu(name="Overview")
        self.set_title(t(f"Gui.MenuAlas.Overview"))
        self._overview_snapshot = None

        put_scope(
            "overview",
            [
                put_scope("schedulers"),
                put_scope(
                    "panel_column",
                    [
                        put_scope("panel_bar"),
                        put_scope("dashboard"),
                        put_scope("stat_panels"),
                        put_scope("logs"),
                    ],
                ),
            ],
        )

        with use_scope("schedulers"):
            put_scope(
                "scheduler-bar",
                [
                    put_text(t("Gui.Overview.Scheduler")).style(
                        "font-size: 1.25rem; margin: auto .5rem auto;"
                    ),
                    put_scope("scheduler_btn"),
                ],
            )
            put_scope(
                "stat-bar",
                [
                    put_scope("panel_toggle_btn"),
                    put_scope("panel_open_btn"),
                ],
            )
            put_scope(
                "running",
                [
                    put_text(t("Gui.Overview.Running")),
                    put_html('<hr class="hr-group">'),
                    put_scope("running_tasks"),
                ],
            )
            put_scope(
                "pending",
                [
                    put_text(t("Gui.Overview.Pending")),
                    put_html('<hr class="hr-group">'),
                    put_scope("pending_tasks"),
                ],
            )
            put_scope(
                "waiting",
                [
                    put_text(t("Gui.Overview.Waiting")),
                    put_html('<hr class="hr-group">'),
                    put_scope("waiting_tasks"),
                ],
            )

        switch_scheduler = BinarySwitchButton(
            label_on=t("Gui.Button.Stop"),
            label_off=t("Gui.Button.Start"),
            onclick_on=lambda: self.alas.stop_by_user(
                self.alas_config.Optimization_WhenSchedulerStopped
            ),
            onclick_off=self._alas_start,
            get_state=lambda: self.alas.alive,
            color_on="off",
            color_off="on",
            scope="scheduler_btn",
        )

        # April Fools: runaway start button
        if getattr(self, "af_flag", False):
            run_js("""
(function(){
    var surrendered = false;
    var bar = document.getElementById('pywebio-scope-scheduler-bar');
    if (!bar) return;
    bar.style.position = 'relative';
    bar.style.overflow = 'hidden';

    var flag = document.createElement('button');
    flag.textContent = '🏳️';
    flag.title = 'I give up...';
    flag.style.cssText = 'border:none;background:transparent;font-size:1.1rem;cursor:pointer;padding:0 4px;margin:auto 2px;opacity:0.45;transition:opacity .2s;flex-shrink:0;';
    flag.onmouseenter = function(){ flag.style.opacity='1'; };
    flag.onmouseleave = function(){ flag.style.opacity='0.45'; };
    flag.onclick = function(){
        surrendered = true;
        flag.style.display = 'none';
        var b = bar.querySelector('.btn-on');
        if(b){ b.style.transition='transform .35s cubic-bezier(.34,1.56,.64,1)'; b.style.transform=''; }
    };
    bar.appendChild(flag);

    bar.addEventListener('mousemove', function(e){
        if (surrendered) return;
        var btn = bar.querySelector('.btn-on');
        if (!btn) return;
        var r = btn.getBoundingClientRect();
        var bx = r.left + r.width/2, by = r.top + r.height/2;
        var dx = bx - e.clientX, dy = by - e.clientY;
        var dist = Math.sqrt(dx*dx + dy*dy);
        if (dist < 100 && dist > 1) {
            var pr = bar.getBoundingClientRect();
            var push = 100 - dist;
            var nx = dx/dist * push, ny = dy/dist * push * 0.3;
            var cur = btn.style.transform.match(/translate\\(([^,]+)px,\\s*([^)]+)px\\)/);
            var ox = cur ? parseFloat(cur[1]) : 0, oy = cur ? parseFloat(cur[2]) : 0;
            var tx = ox + nx, ty = oy + ny;
            var maxX = (pr.width - r.width) / 2 - 4;
            var maxY = (pr.height - r.height) / 2;
            tx = Math.max(-maxX, Math.min(maxX, tx));
            ty = Math.max(-maxY, Math.min(maxY, ty));
            btn.style.transition = 'transform .13s ease-out';
            btn.style.transform = 'translate('+tx+'px,'+ty+'px)';
        }
    });
})();
""")

        if (
            self._overview_log is None
            or self._overview_log_config_name != self.alas_name
        ):
            self._overview_log = RichLog("log")
            self._overview_log_config_name = self.alas_name
        else:
            self._overview_log.scope = "log"
        log = self._overview_log
        log.first_display = True
        log.last_display_time = {}
        self._log = log
        self._log.dashboard_arg_group = LogRes(self.alas_config).groups

        self._overview_panel = get_overview_panel(
            get_localstorage(LOCALSTORAGE_KEYS["overview_panel"])
        )
        self._background_page_open = False
        with use_scope("stat_panels"):
            self._mount_stat_panels("stat_panels")
            # 渲染必须在挂载点作用域内：清理会把作用域重置为 ROOT，
            # _mount_stat_panels 已把自己包回 stat_panels。
            self._render_statistics_sections()
        self._render_log_panel()
        self.task_handler.add(self._render_stat_panel_refresh, 60, True)
        self.task_handler.add(switch_scheduler.g(), 1, True)
        self._render_panel_bar()
        # dashboard scope 此时才存在，首次绘制放这里才不会写出幽灵节点。
        if "Maa" not in self.ALAS_ARGS:
            self.alas_update_dashboard(True)
        eval_js(self._overview_panel_visibility_js())

    def set_dashboard_display(self, b):
        self._log.set_dashboard_display(b)
        self.alas_update_dashboard(True)

    LOG_PAGE_SCOPE = "log-page"

    def alas_set_log(self) -> None:
        """打开日志二级页（大屏），复用同一份日志流。

        与统计页对等：渲染进外壳的 ``log-content`` 槽位，
        由 ``set_secondary_content_visible("Log")`` 负责显隐。
        """
        self.init_menu(name="Log")
        self.set_title(t("Gui.Overview.Log"))
        self._render_log_page()

    def _render_log_page(self) -> None:
        """把日志面板铺满二级页。"""
        log = getattr(self, "_log", None)
        if log is None:
            return
        log.scope = self.LOG_PAGE_SCOPE
        log.first_display = True
        log.last_display_time = {}
        log.sync_width()  # 见 widgets.RichLog.sync_width：元素就绪后再测，持续跟随

        with use_scope("log-content", clear=True):
            put_scope("log-page-bar", [put_scope("log_page_scroll_btn")])
            put_scope(
                self.LOG_PAGE_SCOPE, [put_html("")]
            ).style("overflow-y: auto; flex: 1;")

        switch_log_scroll = BinarySwitchButton(
            label_on=t("Gui.Button.ScrollON"),
            label_off=t("Gui.Button.ScrollOFF"),
            onclick_on=lambda: log.set_scroll(False),
            onclick_off=lambda: log.set_scroll(True),
            get_state=lambda: log.keep_bottom,
            color_on="on",
            color_off="off",
            scope="log_page_scroll_btn",
        )
        self.task_handler.add(switch_log_scroll.g(), 1, True)
        if hasattr(self, "alas") and self.alas is not None:
            self.task_handler.add(log.put_log(self.alas), 0.25, True)

    @use_scope("content", clear=True)
    def alas_daemon_overview(self, task: str) -> None:
        self.init_menu(name=task)
        self.set_title(t(f"Task.{task}.name"))

        log = RichLog("log")

        if self.is_mobile:
            put_scope(
                "daemon-overview",
                [
                    put_scope("scheduler-bar"),
                    put_scope("stat-bar"),
                    put_scope("groups"),
                    put_scope("log-bar"),
                    put_scope("log", [put_html("")]),
                ],
            )
        else:
            put_scope(
                "daemon-overview",
                [
                    put_none(),
                    put_scope(
                        "_daemon",
                        [
                            put_scope(
                                "_daemon_upper",
                                [put_scope("scheduler-bar"), put_scope("log-bar")],
                            ),
                            put_scope("groups"),
                            put_scope("log", [put_html("")]),
                        ],
                    ),
                    put_none(),
                ],
            )

        log.sync_width()  # 见 widgets.RichLog.sync_width：元素就绪后再测，持续跟随

        with use_scope("scheduler-bar"):
            put_text(t("Gui.Overview.Scheduler")).style(
                "font-size: 1.25rem; margin: auto .5rem auto;"
            )
            put_scope("scheduler_btn")

        with use_scope("stat-bar"):
            put_text(t("Gui.Overview.Stat")).style(
                "font-size: 1.25rem; margin: auto .5rem auto;"
            )
            put_button(
                label=t("Gui.Button.Open"),
                onclick=self.alas_set_stat,
                color="on",
            )

        switch_scheduler = BinarySwitchButton(
            label_on=t("Gui.Button.Stop"),
            label_off=t("Gui.Button.Start"),
            onclick_on=lambda: self.alas.stop_by_user(
                self.alas_config.Optimization_WhenSchedulerStopped
            ),
            onclick_off=lambda: self.alas.start(task),
            get_state=lambda: self.alas.alive,
            color_on="off",
            color_off="on",
            scope="scheduler_btn",
        )

        with use_scope("log-bar"):
            put_text(t("Gui.Overview.Log")).style(
                "font-size: 1.25rem; margin: auto .5rem auto;"
            )
            put_scope(
                "log-bar-btns",
                [
                    put_scope("log_scroll_btn"),
                    put_button(
                        label=t('Gui.Stat.ScreenshotPreviewBtn'),
                        onclick=lambda: run_js(
                            f"window.alasToggleLivePreview({json.dumps(self.alas_name)});"
                        ),
                        color="off",
                    ),
                ],
            )

        switch_log_scroll = BinarySwitchButton(
            label_on=t("Gui.Button.ScrollON"),
            label_off=t("Gui.Button.ScrollOFF"),
            onclick_on=lambda: log.set_scroll(False),
            onclick_off=lambda: log.set_scroll(True),
            get_state=lambda: log.keep_bottom,
            color_on="on",
            color_off="off",
            scope="log_scroll_btn",
        )

        config = self.alas_config.read_file(self.alas_name)
        for group, arg_dict in deep_iter(self.ALAS_ARGS[task], depth=1):
            if group[0] == "Storage":
                continue
            self.set_group(group, arg_dict, config, task)

        run_js(
            """
            $("#pywebio-scope-log").css(
                "grid-row-start",
                -2 - $("#pywebio-scope-_daemon").children().filter(
                    function(){
                        return $(this).css("display") === "none";
                    }
                ).length
            );
            $("#pywebio-scope-log").css(
                "grid-row-end",
                -1
            );
        """
        )

        self.task_handler.add(switch_scheduler.g(), 1, True)
        self.task_handler.add(switch_log_scroll.g(), 1, True)
        if hasattr(self, "alas") and self.alas is not None:
            self.task_handler.add(log.put_log(self.alas), 0.25, True)

    def _overview_panel_visibility_js(self) -> str:
        """生成右栏显隐脚本：切到哪个面板就显示哪个。

        背景设置页只盖住日志滚动区：展开时**只**隐藏日志流，
        标题条与其中的按钮栏必须留着 —— 藏了就再也点不到【背景】，
        收不回来（踩过两次：先藏了 log-container，又藏了 log-header）。

        生成的 JS 里**不写任何注释**：脚本是压成一行发出去的，
        行注释会把它后面的语句一起注释掉，表现为脚本跑了却毫无效果。
        """
        active = getattr(self, "_overview_panel", PANEL_STAT)
        show_bg = "true" if self._background_page_open else "false"
        return (
            "(function () {"
            "  var map = {stat: 'stat_panels', log: 'logs'};"
            "  var active = %s;"
            "  Object.keys(map).forEach(function (key) {"
            "    var el = document.getElementById('pywebio-scope-' + map[key]);"
            "    if (el) { el.style.display = (key === active) ? '' : 'none'; }"
            "  });"
            "  var bg = document.getElementById('pywebio-scope-log-bg-page');"
            "  var showBg = %s && active === 'log';"
            "  if (bg) { bg.style.display = showBg ? '' : 'none'; }"
            "  var stream = document.getElementById('pywebio-scope-log');"
            "  if (stream) { stream.style.display = showBg ? 'none' : ''; }"
            "})();"
        ) % (json.dumps(active), show_bg)

    # 统计子视图写入的 scope；后台刷新任务只碰这些
    STAT_PANEL_SCOPES = (
        "ap_chart",
        "resource_chart",
        "opsi_stats",
        "ship_exp_table",
        "commission_income",
    )

    def _rerender_stat_panels(self) -> None:
        """清理旧 scope 并重绘统计面板，整段在 stat_panels 作用域内。

        清理用的 eval_js 会把 PyWebIO 的当前作用域重置为 ROOT：不在清理之后
        重新进入 stat_panels，渲染时的 use_scope 就会以 ROOT 为父创建图表
        scope，撑爆 ROOT 的 grid 行、把 contents 压成 0，左侧设置栏随之塌陷。
        清理与渲染必须成对，所以收敛到这里，避免每个调用点各包一次。
        """
        with use_scope("stat_panels"):
            self._remove_stale_scopes(self.STAT_PANEL_SCOPES)
            self._render_statistics_sections()

    @staticmethod
    def _remove_stale_scopes(names) -> None:
        """删除指定 scope 的旧节点，让随后可以干净地重建。

        必须用 eval_js 而不是 run_js：run_js 的源码就是
        ``send_msg('run_script', ...)``——发完立即返回、不等浏览器执行，
        于是随后的 put_scope 建壳会先于清理生效，同名节点还在，前端就放弃
        创建并弹「此scope与已有scope重复」，新挂载点里只剩空壳。
        eval_js 会等浏览器返回，因此这里是同步的：先删干净，再建全新的。

        删除是**无条件**的（不管节点在不在内容容器里）：scope 建好后不会
        自动跟随页面切换，残留节点留在上一处容器里，光靠「游离节点」判据
        抓不到它们。
        """
        try:
            eval_js(
                """
                (function () {
                    var removed = 0;
                    names.forEach(function (name) {
                        var el = document.getElementById("pywebio-scope-" + name);
                        if (el && el.parentNode) {
                            el.parentNode.removeChild(el);
                            removed += 1;
                        }
                    });
                    return removed;
                })();
                """,
                names=list(names),
            )
        except Exception as exc:  # 清理失败不应阻断页面渲染
            logger.warning(f"清理旧 scope 失败：{exc}")

    def _is_overview_page(self) -> bool:
        """概览页是否仍是当前页面（后台任务写 scope 前必须确认）。"""
        return getattr(self, "page", None) == "Overview"

    def _render_panel_bar(self) -> None:
        """渲染 stat-bar 里的两个按钮：切换在左，「打开」在右。

        按钮直接渲染进各自作用域，不额外包一层 put_scope ——
        包一层会添一个裸 div，主题的 .btn 样式反而落不到按钮上。

        切换按钮的文案是「点击后会切到哪一栏」，与右栏当前显示的面板相反：
        右栏显示统计时按钮写【日志】，显示日志时按钮写【统计】。
        因此按钮文案同时就是「打开」按钮要打开的二级页名。
        """
        active = getattr(self, "_overview_panel", PANEL_STAT)
        label = t("Gui.Overview.Stat") if active == PANEL_LOG else t("Gui.Overview.Log")
        with use_scope("panel_toggle_btn", clear=True):
            put_button(
                label=label,
                onclick=lambda: self._switch_overview_panel(
                    PANEL_LOG if active == PANEL_STAT else PANEL_STAT
                ),
                color="on",
            )
        with use_scope("panel_open_btn", clear=True):
            put_button(
                label=t("Gui.Button.Open"),
                onclick=self._open_secondary_page,
                color="off",
            )

    def _open_secondary_page(self) -> None:
        """打开满屏二级页：进切换按钮写着的那一页。

        按钮文案是「点击后会切到哪一栏」（_render_panel_bar 里取反得到），
        它同时就是这里要打开的二级页名。于是：
          按钮【统计】→ 右栏显示日志栏，这里进统计页
          按钮【日志】→ 右栏显示统计栏，这里进日志页
        也就是跟着面板的**反面**走：右栏显示日志就开统计页。
        """
        if getattr(self, "_overview_panel", PANEL_STAT) == PANEL_LOG:
            self.alas_set_stat()
        else:
            self.alas_set_log()

    def _render_stat_panel_refresh(self) -> None:
        """右栏统计面板的周期刷新。

        三重条件缺一不可：
        ① 仍在概览页 —— 否则 use_scope 找不到元素，会在 ROOT 下写出幽灵 scope；
        ② 右栏正显示统计面板 —— 隐藏时不重绘，避免停留在日志页还在后台画图；
        ③ 统计内容已挂载过 —— 否则没有可复用的容器。
        """
        if not self._is_overview_page():
            return
        if getattr(self, "_overview_panel", PANEL_STAT) != PANEL_STAT:
            return
        if getattr(self, "_statistics_cache_key", None) is None:
            return
        self._rerender_stat_panels()

    def _switch_overview_panel(self, panel: str) -> None:
        """切换右栏面板并记住选择（服务端 + 浏览器双写）。

        只动显隐，不重新挂载面板，因此日志流与统计图表状态都不会重置。
        """
        if panel not in (PANEL_LOG, PANEL_STAT):
            return
        previous = getattr(self, "_overview_panel", PANEL_STAT)
        self._overview_panel = panel
        set_overview_panel(panel)
        set_localstorage(LOCALSTORAGE_KEYS["overview_panel"], panel)
        # 必须 eval_js：run_js 等着页面被重新渲染，脚本会被后面的 DOM 更新冲掉，
        # 表现为按钮点了没反应（统计/日志切换实测如此）。
        eval_js(self._overview_panel_visibility_js())
        self._render_panel_bar()
        # 切到统计时立即刷新一次，之后由 60s 周期任务在显示状态下继续刷新。
        if panel == PANEL_STAT and previous != PANEL_STAT:
            # 后台任务可能已在 ROOT 下留下同名 scope，不清理会撞名
            self._rerender_stat_panels()
