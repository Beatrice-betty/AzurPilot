"""图表尺寸同步与统计子板块样式的结构性约束。

这里盯的是两个容易再被改坏、且坏了不会立刻报错的地方：

1. canvas 的位图尺寸必须与显示尺寸保持 dpr 精确一致。原实现把量到的像素写回
   `cv.style.width/height`，而 canvas 是 `width:100%` 自适应的 —— 它的宽度因此
   由自己上一步的值决定：容器变窄后一直报旧宽度，位图永不更新（重影），而且
   再也回不来。resizeCanvas() 还必须定义在 initChart() 之外，因为 ResizeObserver
   回调在外层作用域，放里面就报 `resizeCanvas is not defined`（已踩过）。

2. 高级材质的磨砂选择器列表与基础层的子板块卡片列表必须覆盖同一批板块。
   曾经漏掉 resource_chart，导致它是透明无内边距，与其它板块格格不入。
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEBAPP = ROOT / 'webapp'
CSS = ROOT / 'assets' / 'gui' / 'css'

CHARTS = ('ap_chart.js', 'resource_chart.js')

# 统计子板块的 scope 名
PANELS = (
    'opsi_stats',
    'ap_chart',
    'resource_chart',
    'ship_exp_table',
    'commission_income',
)


def scope_list_in(css_text, first_scope):
    """从 css 文本里取出以 first_scope 开头的整条选择器列表。"""
    m = re.search(
        r'((?:#pywebio-scope-[\w-]+,\s*)*#pywebio-scope-'
        + re.escape(first_scope) + r'[\w-]*(?:,\s*#pywebio-scope-[\w-]+)*)\s*\{',
        css_text)
    return m.group(1) if m else None


class TestChartCanvasSizing(unittest.TestCase):
    def test_no_inline_canvas_size_write(self):
        """不能把量到的像素写回 canvas 的内联尺寸 —— 那会造成自引用尺寸锁定。"""
        for name in CHARTS:
            text = (WEBAPP / name).read_text(encoding='utf-8')
            # 去掉注释行再找真正的赋值
            code = '\n'.join(
                ln for ln in text.split('\n')
                if not ln.lstrip().startswith('//'))
            with self.subTest(chart=name):
                self.assertIsNone(
                    re.search(r'\b(?:cv|ovCv)\.style\.(?:width|height)\s*=', code),
                    f'{name} 仍在写 canvas 内联尺寸：canvas 是 width:100% 自适应的，'
                    '写回像素值会让它的宽度由自己上一步的值决定，容器一变窄就'
                    '再也回不来（重影）。')

    def test_resize_canvas_outside_init_chart(self):
        """resizeCanvas/handleCanvasResize 必须在 initChart 之外（外层作用域）。"""
        for name in CHARTS:
            text = (WEBAPP / name).read_text(encoding='utf-8')
            with self.subTest(chart=name):
                m = re.search(r'^([ \t]*)function initChart\(\)', text, re.M)
                self.assertIsNotNone(m, f'{name} 未找到 initChart')
                indent = m.group(1)
                start = m.start()

                for fn in ('resizeCanvas', 'handleCanvasResize',
                           'setCanvasTransform'):
                    fm = re.search(
                        r'^([ \t]*)function ' + fn + r'\(', text, re.M)
                    self.assertIsNotNone(fm, f'{name} 未找到 {fn}')
                    self.assertEqual(
                        fm.group(1), indent,
                        f'{name} 的 {fn} 缩进与 initChart 不同，说明它被定义在'
                        ' initChart 内部；外层作用域的回调取不到它。')
                    self.assertLess(
                        fm.start(), start,
                        f'{name} 的 {fn} 定位在 initChart 之后，'
                        '请放在 initChart 之前（外层作用域）。')

    def test_resize_canvas_called_before_draw(self):
        """绘制入口必须先同步尺寸，否则容器变化后按旧尺寸重绘就是重影。"""
        for name in CHARTS:
            text = (WEBAPP / name).read_text(encoding='utf-8')
            with self.subTest(chart=name):
                m = re.search(r'function initChart\(\)[ \t]*\{[ \t]*\r?\n(.*)',
                              text, re.DOTALL)
                self.assertIsNotNone(m)
                body = m.group(1)[:400]
                self.assertIn('resizeCanvas()', body,
                              f'{name} 的 initChart 开头没有调用 resizeCanvas()')

    def test_resize_observer_registered(self):
        """容器尺寸变化必须触发重新同步。"""
        for name in CHARTS:
            text = (WEBAPP / name).read_text(encoding='utf-8')
            with self.subTest(chart=name):
                self.assertIn('ResizeObserver', text)
                self.assertIn('addEventListener', text)
                self.assertIn('disconnect()', text,
                              f'{name} 的 cleanup 没有释放 ResizeObserver')


class TestStatPanelSelectors(unittest.TestCase):
    def _scopes(self, css_text, anchor):
        block = scope_list_in(css_text, anchor)
        self.assertIsNotNone(block, f'未找到以 {anchor} 开头的选择器列表')
        return set(re.findall(r'#pywebio-scope-([\w-]+)', block))

    def test_glass_rule_covers_all_panels(self):
        """高级材质里每个子板块都要被样式覆盖（曾漏掉 resource_chart）。

        不要求挤在同一条规则里：resource_chart 在总览页左侧上方，
        不跟着右栏一起调透，所以它有自己一条规则。这里按文件里
        所有 #pywebio-scope-xxx 出现情况合并统计。
        """
        adv = (CSS / 'advanced-material-alas.css').read_text(encoding='utf-8')
        covered = set(re.findall(r'#pywebio-scope-([\w-]+)\s*(?:,|\{)', adv))
        missing = set(PANELS) - covered
        self.assertFalse(
            missing,
            f'高级材质磨砂规则漏了 {sorted(missing)}；漏掉的板块会是透明无内边距，'
            '与其它板块不一致。')

    def test_base_card_rule_covers_all_panels(self):
        """基础层的子板块卡片要覆盖同一批板块。"""
        base = (CSS / 'alas.css').read_text(encoding='utf-8')
        scopes = self._scopes(base, 'opsi_stats')
        missing = set(PANELS) - scopes
        self.assertFalse(missing, f'alas.css 子板块规则漏了 {sorted(missing)}')

    def test_base_card_rule_has_padding_and_border(self):
        """普通材质没有磨砂玻璃，靠基础层的内边距与边框把内容与卡片边缘分开。"""
        base = (CSS / 'alas.css').read_text(encoding='utf-8')
        m = re.search(
            r'#pywebio-scope-opsi_stats,(?:[^{]*?)\{(.*?)\}', base, re.DOTALL)
        self.assertIsNotNone(m, '未找到子板块卡片规则块')
        block = m.group(1)
        for prop in ('padding', 'border', 'border-radius', 'background'):
            with self.subTest(prop=prop):
                self.assertIn(prop, block,
                              f'子板块卡片规则缺少 {prop}')

    def test_no_transparent_override_on_panels(self):
        """不能有用 background:transparent 清空板块底色的覆盖。

        曾经在 dark-advanced-material-overrides 里给三个表格板块写了
        `background: transparent !important`，它压过磨砂规则的 background-color，
        导致高级黑主题下这三个板块全透明、两个图表板块保留底色，五个板块不一致。
        """
        for name in ('advanced-material-alas.css',
                     'dark-advanced-material-overrides-alas.css',
                     'dark-alas.css', 'light-alas.css', 'alas.css'):
            text = (CSS / name).read_text(encoding='utf-8')
            body = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
            for m in re.finditer(r'([^{}]+)\{(.*?)\}', body, re.DOTALL):
                selector, decls = m.group(1), m.group(2)
                if not any(p in selector for p in PANELS):
                    continue
                sels = [s.strip() for s in selector.split(',')]
                if not any(re.fullmatch(
                        r'#pywebio-scope-(?:' + '|'.join(PANELS) + r')', s)
                        for s in sels):
                    continue
                with self.subTest(file=name, selector=selector.strip()[:60]):
                    self.assertNotRegex(
                        decls, r'background(?:-color)?\s*:\s*transparent',
                        f'{name} 里 {selector.strip()[:60]} 把板块底色清成透明，'
                        '会压过磨砂规则，导致各板块底色不一致。')

    def test_plain_themes_define_panel_tokens(self):
        """普通材质与浅色主题要给出可分辨的卡片面。"""
        for name in ('dark-alas.css', 'light-alas.css'):
            text = (CSS / name).read_text(encoding='utf-8')
            with self.subTest(theme=name):
                self.assertIn('--alas-panel-bg', text)
                self.assertIn('--alas-panel-border', text)

class TestChartThemeColors(unittest.TestCase):
    """图表配色必须跟随主题，不能写死。

    两个图表 JS 原来把背景写死 #1a1a2e、网格 #2a2a3e、刻度 #666，
    所以任何材质模板下图表都是那个内置深蓝。CSS 里虽然有
    --alas-chart-{bg,grid,label}，但 JS 从未读取。
    """

    HARDCODED = ('#1a1a2e', '#2a2a3e')

    def test_no_hardcoded_chart_colors(self):
        """画图时只能用 _cc，不能出现字面颜色。

        允许 __FALLBACK_CC 那一行保留旧色值：令牌读不到时（旧浏览器、
        样式表未就绪）还得有个能看的底色。只禁止它在绘制语句里出现。
        """
        for name in CHARTS:
            code = '\n'.join(
                ln for ln in (WEBAPP / name).read_text(encoding='utf-8').split('\n')
                if '__FALLBACK_CC' not in ln
                and not ln.lstrip().startswith('//'))
            with self.subTest(chart=name):
                for color in self.HARDCODED:
                    self.assertNotIn(
                        color, code,
                        f'{name} 的绘制代码里又写死了图表颜色 {color}；'
                        '应当用 chartThemeColors() 读 --alas-chart-* 令牌。')

    def test_reads_chart_tokens(self):
        """必须真的通过 getComputedStyle 读令牌（canvas 不接受 var()）。"""
        for name in CHARTS:
            text = (WEBAPP / name).read_text(encoding='utf-8')
            with self.subTest(chart=name):
                self.assertIn('getComputedStyle', text)
                for token in ('--alas-chart-bg', '--alas-chart-grid',
                              '--alas-chart-label'):
                    self.assertIn(token, text, f'{name} 未读取 {token}')

    def test_repaints_on_theme_switch(self):
        """canvas 的颜色在绘制时就烘焙进位图了，主题换了必须重绘。"""
        for name in CHARTS:
            text = (WEBAPP / name).read_text(encoding='utf-8')
            with self.subTest(chart=name):
                self.assertIn(
                    'MutationObserver', text,
                    f'{name} 没有监听主题切换；换主题后位图里还是旧主题的颜色。')
                self.assertIn('watchThemeRedraw', text)

    def test_color_use_is_in_scope(self):
        """_cc 的声明与使用必须在同一个函数里，否则运行时报未定义。"""
        for name in CHARTS:
            text = (WEBAPP / name).read_text(encoding='utf-8')
            lines = text.split('\n')
            tops = []
            for i, ln in enumerate(lines):
                m = re.match(r'^([ \t]*)function (\w+)\(', ln)
                if not m or m.group(1) != '    ':
                    continue
                if m.group(2) in ('chartThemeColors', 'watchThemeRedraw'):
                    continue
                depth = 0
                started = False
                for j in range(i, len(lines)):
                    depth += lines[j].count('{') - lines[j].count('}')
                    if '{' in lines[j]:
                        started = True
                    if started and depth <= 0:
                        tops.append((m.group(2), i + 1, j + 1))
                        break

            def owner(n):
                for nm, a, b in tops:
                    if a <= n <= b:
                        return nm
                return None

            with self.subTest(chart=name):
                # _cc 现在提在模块级（先给兜底值），由绘制入口在每次绘制时
                # 重读 —— 主题令牌名不变、值变，位图里烘焙的是绘制当时的
                # 颜色，不重读的话切换主题后画的还是旧色。
                self.assertTrue(
                    [i for i, ln in enumerate(lines, 1)
                     if 'var _cc = __FALLBACK_CC;' in ln],
                    f'{name} 没有模块级 _cc 兜底声明')
                entry_read = [i for i, ln in enumerate(lines, 1)
                              if '_cc = chartThemeColors();' in ln]
                self.assertTrue(entry_read, f'{name} 绘制入口没有重读 _cc')
                use_at = [i for i, ln in enumerate(lines, 1)
                          if '_cc.' in ln and 'var _cc' not in ln]
                self.assertTrue(use_at, f'{name} 没有 _cc 使用')
                # 每个用到 _cc 的顶层函数里都要有一次重读，否则那个函数
                # 会拿上一次绘制遗留的旧颜色。
                read_owners = {owner(r) for r in entry_read}
                for u in use_at:
                    self.assertIn(
                        owner(u), read_owners,
                        f'{name} 第 {u} 行用了 _cc，但它所在的函数 '
                        f'{owner(u)} 里没有重读 _cc；换主题后会沿用旧色。')


class TestButtonFrames(unittest.TestCase):
    """每个主题都必须定义 .btn-off，否则按钮只剩文字。

    Bootstrap 主题 CSS 给 .btn 设了 `border: 1px solid transparent`（无框）。
    dark-alas / light-alas 都用 .btn-off 覆盖了它，但高级材质两套都漏了 ——
    高级白/高级黑下所有未选中按钮（打开/设置/截图预览/折叠…）
    没有边框也没有填充，看着就只剩文字。
    """

    THEMES = ('dark-alas.css', 'light-alas.css', 'advanced-material-alas.css',
              'dark-advanced-material-overrides-alas.css')

    def test_every_theme_defines_btn_off(self):
        for name in self.THEMES:
            text = (CSS / name).read_text(encoding='utf-8')
            body = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
            with self.subTest(theme=name):
                self.assertRegex(
                    body, r'(?:^|\n)\.btn-off\s*(?:,[^{]*)?\{',
                    f'{name} 没有 .btn-off 定义；Bootstrap 的 .btn 是 transparent '
                    '边框，没这条覆盖按钮就只剩文字。')

    def test_btn_off_sets_border_and_fill(self):
        """补上还不够，必须同时给出可见的边框和填充色。"""
        for name in self.THEMES:
            text = (CSS / name).read_text(encoding='utf-8')
            body = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
            m = re.search(r'\.btn-off\s*(?:,[^{]*)?\{(.*?)\}', body, re.DOTALL)
            with self.subTest(theme=name):
                self.assertIsNotNone(m, f'{name} 未找到 .btn-off 规则块')
                block = m.group(1)
                self.assertRegex(block, r'border\s*:',
                                 f'{name} 的 .btn-off 没有边框')
                self.assertRegex(block, r'background(?:-color)?\s*:',
                                 f'{name} 的 .btn-off 没有填充色')
                self.assertNotRegex(
                    block, r'background(?:-color)?\s*:\s*transparent',
                    f'{name} 的 .btn-off 填充是透明的，按钮还是只剩文字')
                self.assertNotRegex(
                    block, r'border(?:-color)?\s*:[^;]*transparent',
                    f'{name} 的 .btn-off 边框是透明的，看不见框')
    def test_no_duplicate_btn_off_rules(self):
        """一个主题里 .btn-off 只能有一份。

        两份时 !important 会跨规则竞争、由源码顺序决胜负，而
        padding / border-radius 只在其中一份里 —— 看着生效了，
        其实靠的是「后面那条恰好赢了」，改一处就会莫名失效。
        """
        for name in self.THEMES:
            text = (CSS / name).read_text(encoding='utf-8')
            body = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
            n = len(re.findall(r'(?:^|\n)\.btn-off\s*(?:,[^{]*)?\{', body))
            with self.subTest(theme=name):
                self.assertEqual(
                    n, 1,
                    f'{name} 里 .btn-off 定义了 {n} 份；应合并为一份。')

    def test_advanced_material_btn_off_uses_accent_fill(self):
        """高级材质两套的未选中按钮要「强调色底 + 白字」。

        曾经用 --alas-apple-card-bg（白玻璃）+ --alas-apple-text（近黑），
        看着是白底黑字，与主题的蓝色强调色不搭。
        """
        for name in ('advanced-material-alas.css',
                     'dark-advanced-material-overrides-alas.css'):
            text = (CSS / name).read_text(encoding='utf-8')
            body = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
            m = re.search(r'(?:^|\n)\.btn-off\s*(?:,[^{]*)?\{(.*?)\}',
                          body, re.DOTALL)
            with self.subTest(theme=name):
                self.assertIsNotNone(m, f'{name} 未找到 .btn-off 规则块')
                block = m.group(1)
                self.assertIn('--alas-apple-accent', block,
                              f'{name} 的 .btn-off 填充不是强调色')
                self.assertRegex(block, r'color\s*:\s*#ffffff',
                                 f'{name} 的 .btn-off 文字不是白字')


class TestChartThemePolish(unittest.TestCase):
    """滚动栏图表的透明背景与曲线配色。"""

    THEMES = ('dark-alas.css', 'light-alas.css', 'advanced-material-alas.css',
              'dark-advanced-material-overrides-alas.css')

    def test_inline_chart_box_rule_is_not_theme_specific(self):
        """滚动栏里 .ap-chart-box 的透明规则要在基础层。

        否则只有写规则的那套主题透明，其他主题下图表还是一块纯色浮窗 ——
        这正是「高级材质下才透明」的漏法。
        """
        base = (CSS / 'alas.css').read_text(encoding='utf-8')
        body = re.sub(r'/\*.*?\*/', '', base, flags=re.DOTALL)
        self.assertRegex(
            body,
            r'#pywebio-scope-stat_panels\s+\.ap-chart-box\s*\{[^}]*'
            r'background\s*:\s*transparent',
            'alas.css 缺少滚动栏图表的透明背景规则；图表会自画一个纯色浮窗。')
        for name in ('advanced-material-alas.css',
                     'dark-advanced-material-overrides-alas.css'):
            text = (CSS / name).read_text(encoding='utf-8')
            body = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
            self.assertNotIn(
                'stat_panels .ap-chart-box', body,
                f'{name} 不该重复定义这条规则（基础层已覆盖），重复声明以后调一处漏一处。')

    def test_every_theme_defines_chart_line_colors(self):
        """四个主题都要给出曲线涨跌色。

        原来写死 #ef5350 / #26a69a，在亮色背景下对比度不足。
        """
        for name in self.THEMES:
            text = (CSS / name).read_text(encoding='utf-8')
            with self.subTest(theme=name):
                for tok in ('--alas-chart-up', '--alas-chart-down'):
                    self.assertRegex(
                        text, re.escape(tok) + r'\s*:\s*#',
                        f'{name} 没有 {tok}；曲线会回退到写死的颜色。')

    def test_chart_js_reads_line_color_tokens(self):
        """JS 必须读这两个令牌，且不再残留写死的涨跌色。"""
        for name in ('ap_chart.js', 'resource_chart.js'):
            text = (WEBAPP / name).read_text(encoding='utf-8')
            with self.subTest(chart=name):
                for tok in ('--alas-chart-up', '--alas-chart-down'):
                    self.assertIn(tok, text, f'{name} 没有读取 {tok}')
                for hard in ('"#ef5350"', '"#26a69a"'):
                    self.assertNotIn(
                        hard, text,
                        f'{name} 还残留写死的 {hard}；亮色背景下对比度不足。')

    def test_inline_background_is_transparent_in_js(self):
        """画布背景要区分滚动栏与二级菜单统计页。

        滚动栏里透明（让磨砂玻璃透出来），二级菜单里用主题底色
        （那里 canvas 直接铺在页面上，全透明会让网格线看不清）。
        """
        for name in ('ap_chart.js', 'resource_chart.js'):
            text = (WEBAPP / name).read_text(encoding='utf-8')
            with self.subTest(chart=name):
                self.assertIn(
                    '#pywebio-scope-stat_panels', text,
                    f'{name} 没有识别滚动栏；无法区分两种背景。')
                self.assertRegex(text, r'isInline\s*\?\s*.rgba\(0, 0, 0, 0\)',
                                 f'{name} 的滚动栏背景不是透明的')
                self.assertIn(
                    '--alas-chart-bg', text,
                    f'{name} 的二级菜单统计页没有用主题底色')
    def test_lines_are_emphasised_by_width_not_glow(self):
        """曲线靠线宽强调，不用 shadowBlur 泛光。

        canvas 的 shadowBlur 会给细线蒙一层阴影、看着发糊（K 线影线
        尤其明显）。用户要的是线条本身更醒目，所以改用线宽倍数：
        网格保持 1px，数据曲线与均线乘上 __apLineW。
        """
        for name in ('ap_chart.js', 'resource_chart.js'):
            text = (WEBAPP / name).read_text(encoding='utf-8')
            code = '\n'.join(l for l in text.splitlines()
                             if not l.strip().startswith('//'))
            with self.subTest(chart=name):
                self.assertNotIn(
                    'shadowBlur', code,
                    f'{name} 还在用 shadowBlur；曲线会发糊。')
                self.assertNotIn(
                    '__glowStroke', code, f'{name} 还留着旧的泛光调用。')
                self.assertIn(
                    '__apLineW', code,
                    f'{name} 没有线宽强调；曲线和网格一样粗，分不出主次。')
                self.assertRegex(
                    code, r'__apLineW\s*=\s*\(_cc\.lw',
                    f'{name} 的线宽没有按主题取值。')
                # 网格线必须保持 1px，否则整张图糊成一片
                self.assertIn('ctx.lineWidth = 1;', code,
                              f'{name} 网格线宽被改动了。')

    def test_up_down_colors_match_upstream(self):
        """最高/最低 的红绿必须是上游原始值。

        它们一度被换成主题令牌 --alas-chart-up/down，颜色跟着主题漂移，
        与用户熟悉的提示色对不上。上游写死 #ef5350 / #26a69a，照抄。
        """
        for name in ('dark-alas.css', 'light-alas.css',
                     'advanced-material-alas.css',
                     'dark-advanced-material-overrides-alas.css'):
            css = (CSS / name).read_text(encoding='utf-8')
            with self.subTest(theme=name):
                self.assertRegex(
                    css, r'--alas-chart-up:\s*#ef5350\b',
                    f'{name} 的涨色不是上游的 #ef5350')
                self.assertRegex(
                    css, r'--alas-chart-down:\s*#26a69a\b',
                    f'{name} 的跌色不是上游的 #26a69a')

    def test_series_colors_match_upstream(self):
        """统计数值/图例的系列色必须等于上游 seriesColors。

        上游：["#64b5f6", "#ce93d8", "#ffd54f", "#22d3ee", "#1565c0"]
        → 黄币 #ffd54f 紫币 #ce93d8 资产 #22d3ee 海里数 #1565c0。
        资产与海里数都是冷色，给反了肉眼很容易漏。
        """
        UPSTREAM = {'--ap-series-1': '#64b5f6', '--ap-series-2': '#ffd54f',
                    '--ap-series-3': '#ce93d8', '--ap-series-4': '#22d3ee',
                    '--ap-series-5': '#1565c0'}
        for name in ('dark-alas.css', 'light-alas.css',
                     'advanced-material-alas.css',
                     'dark-advanced-material-overrides-alas.css'):
            css = (CSS / name).read_text(encoding='utf-8')
            for token, want in UPSTREAM.items():
                with self.subTest(theme=name, token=token):
                    self.assertRegex(
                        css, rf'{token}:\s*{want}\b',
                        f'{name} 的 {token} 不是上游的 {want}')
        src = (ROOT / 'module/webui/app_stat_action_point.py').read_text(
            encoding='utf-8')
        for label, token in (('黄币', 'ap-series-2'), ('紫币', 'ap-series-3'),
                             ('资产', 'ap-series-4'), ('海里数', 'ap-series-5')):
            with self.subTest(label=label):
                self.assertRegex(
                    src, rf'<span>{label}: <b class="ap-value {token}"',
                    f'{label} 没有用 {token}；与上游配色不符')


    def test_resource_panel_is_not_shrunk_with_right_column(self):
        """资源统计栏不能被并进右栏统计栏规则一起调透。

        它在总览页左侧上方，不属于右栏。曾经把 #pywebio-scope-resource_chart
        一起写进右栏那条规则，用上了给右栏专用的低不透明度令牌，
        于是这一栏明显比周围卡片透 —— 上游用的是基础卡片面令牌。
        """
        css = (CSS / 'advanced-material-alas.css').read_text(encoding='utf-8')
        i = css.index('#pywebio-scope-opsi_stats,')
        j = css.index('}', css.index('#pywebio-scope-commission_income {', i)) + 1
        blk = css[i:j]
        self.assertNotIn(
            'resource_chart', blk,
            '资源统计栏被并进右栏统计栏规则；它会跟着右栏一起被调透。')
        k = css.index('#pywebio-scope-resource_chart {')
        rblk = css[k:css.index('}', k)]
        self.assertIn('var(--alas-apple-card-bg)', rblk,
                      '资源统计栏没用自己的基础卡片面令牌。')

    def test_ap_segmented_line_is_emphasised(self):
        """体力红绿分段主线也要加宽。

        它是一段段画的（每段换 strokeStyle 表示叠涨），跟其它曲线不是
        同一段代码，容易漏掉 —— 曾漏过，于是主线比网格线还细。
        """
        js = (WEBAPP / 'ap_chart.js').read_text(encoding='utf-8')
        i = js.index("if (chartType === 'line' && seriesVisible[0]) {")
        blk = js[i:i + 420]
        self.assertIn(
            '__apLineW', blk,
            '体力红绿分段主线没有线宽强调；它会是全图最细的线。')

    def test_advanced_themes_declare_panel_bg(self):
        """高级材质主题必须声明 --alas-panel-bg，否则吃硬编码兜底色。

        alas.css 里 `background: var(--alas-panel-bg, var(--alas-card-bg,
        #303338))` 带 !important，且基础层在主题之后加载。主题不声明
        --alas-panel-bg 时回退到 #303338 —— 不透明实心深灰，会让统计
        子板块与周围卡片观感不一致。
        """
        for name in ('advanced-material-alas.css',
                     'dark-advanced-material-overrides-alas.css'):
            css = (CSS / name).read_text(encoding='utf-8')
            self.assertIn(
                '--alas-panel-bg:', css,
                f'{name} 没声明 --alas-panel-bg；统计子板块会回退到 '
                '硬编码 #303338，与周围卡片不一致。')

    def test_all_themes_declare_panel_bg(self):
        """四个主题都应声明 --alas-panel-bg（基础层只引用不定义）。"""
        for name in ('advanced-material-alas.css', 'dark-alas.css',
                     'light-alas.css',
                     'dark-advanced-material-overrides-alas.css'):
            css = (CSS / name).read_text(encoding='utf-8')
            self.assertIn('--alas-panel-bg:', css,
                          f'{name} 没声明 --alas-panel-bg')


    def test_base_theme_rules_are_tokenised_for_dark_override(self):
        """高级白规则里会漏给高级黑的属性必须走令牌。

        高级黑是覆盖文件，只重定义令牌、不重定义规则。基座规则里
        写死的值（如 blur(16px)）会整片漏过去。这里确保高级黑要
        独立控制的这几个属性都走 var()。
        """
        light = (CSS / 'advanced-material-alas.css').read_text(encoding='utf-8')
        dark = (CSS / 'dark-advanced-material-overrides-alas.css').read_text(
            encoding='utf-8')

        m = re.search(r'#pywebio-scope-logs \{(.*?)\}', light, re.S)
        self.assertIsNotNone(m, '未找到右栏底图规则')
        rule = m.group(1)
        for prop in ('background-color', 'backdrop-filter'):
            d = re.search(prop + r':\s*([^;]+);', rule)
            self.assertIsNotNone(d, f'右栏底图规则没有 {prop}')
            self.assertIn(
                'var(--', d.group(1),
                f'高级白右栏底图的 {prop} 是硬编码值；它会漏给高级黑，'
                '两边无法分开调。')

        i = light.index('pywebio-scope-running > p')
        ts = re.search(r'text-shadow:\s*([^;]+);', light[i:i + 400])
        self.assertIsNotNone(ts, '未找到标题 text-shadow')
        self.assertIn('var(--', ts.group(1),
                      '标题描边是硬编码值；会漏给高级黑。')

        for tok in ('--alas-log-column-bg:', '--alas-log-column-blur:',
                    '--alas-card-title-shadow:'):
            self.assertIn(
                tok, dark,
                f'高级黑没有独立声明 {tok}；它会继承高级白的值。')

    def test_stat_value_colors_use_tokens(self):
        """统计数值的配色必须与曲线同源。

        曾经写死 #ef5350 / #26a69a，而曲线已改用 --alas-chart-up/down，
        于是「最高/最低」的颜色和曲线对不上。
        """
        src = (ROOT / 'module/webui/app_stat_action_point.py').read_text(
            encoding='utf-8')
        # 只看每个统计行内部。change_color 变量仍按涨跌给「变化」值上色，
        # 那是渲染时的三元表达式，不在这个范围内。
        rows = re.findall(r'<div class="ap-stat-row".*?</div>', src, re.DOTALL)
        self.assertEqual(len(rows), 4, f'统计行数 {len(rows)}，应为 4')
        for row in rows:
            self.assertNotIn(
                '#ef5350', row,
                '统计行还写死 #ef5350；最高应与曲线同用 --alas-chart-up。')
            self.assertNotIn(
                '#26a69a', row,
                '统计行还写死 #26a69a；最低应与曲线同用 --alas-chart-down。')
        for cls in ('ap-up', 'ap-down', 'ap-value'):
            self.assertIn(cls, src, f'统计数值缺少 {cls} 类')
        # 数值上不能有发光类：text-shadow 会让数字糊掉（用户明确反馈）
        self.assertNotIn('ap-glow', src,
                         '统计数值上还有 ap-glow；光晕会让数字糊，不清晰。')
        # 每个主题都要定义红绿提示色与不模糊的加亮类
        for name in ('dark-alas.css', 'light-alas.css',
                     'advanced-material-alas.css',
                     'dark-advanced-material-overrides-alas.css'):
            css = (CSS / name).read_text(encoding='utf-8')
            with self.subTest(theme=name):
                self.assertRegex(css, r'\.ap-up\s*\{[^}]*color',
                                 f'{name} 缺 .ap-up；最高值的红色提示会丢')
                self.assertRegex(css, r'\.ap-down\s*\{[^}]*color',
                                 f'{name} 缺 .ap-down；最低值的绿色提示会丢')
                self.assertRegex(css, r'\.ap-value\s*\{[^}]*filter',
                                 f'{name} 缺 .ap-value；数值没有加亮')

    def test_every_stat_row_has_five_columns(self):
        """统计标注的每一行都必须是 5 个元素，否则列对不齐。

        顶部标注 5 列（资源名/变化/最高/最低/均值），但只有第一行
        （行动力，在模板里）有均值。其余 4 行由 Python 拼字符串生成，
        曾经只有 4 个元素 —— 塞进 5 列 grid 就整体错位。
        现在它们带一个空占位列（均值列只第一行有值）。
        """
        tpl = (ROOT / 'webapp/ap_chart_panel.html').read_text(encoding='utf-8')
        head = re.search(
            r'<div class="ap-chart-stats ap-stat-grid".*?</div>',
            tpl, re.DOTALL)
        self.assertIsNotNone(head, '模板里没找到带网格类的标注行')
        self.assertEqual(
            5, head.group(0).count('<span'),
            '标注第一行不是 5 个元素；各列会对不齐。')

        src = (ROOT / 'module/webui/app_stat_action_point.py').read_text(
            encoding='utf-8')
        rows = re.findall(r'<div class="ap-stat-row".*?</div>', src, re.DOTALL)
        self.assertEqual(4, len(rows), f'数据行数 {len(rows)}，应为 4')
        for i, row in enumerate(rows):
            with self.subTest(row=i):
                self.assertEqual(
                    5, row.count('<span'),
                    f'第 {i + 1} 个数据行不是 5 个元素；'
                    '列数不一致会和第一行错位。')

    def test_all_stat_rows_share_one_grid_container(self):
        """所有标注行必须在**同一个**网格容器里，列宽才会跨行统一。

        每行自己当 grid 容器时，max-content 只按本行内容算列宽，
        各行的第 2 列会落在不同 x（实测 691/686/679/705/704）。
        曾把容器的 </div> 留在数据行之前 —— 行在网格外，等于
        什么也没改，所以这里用哨兵值确认插入点在容器内部。
        """
        tpl = (ROOT / 'webapp/ap_chart_panel.html').read_text(encoding='utf-8')
        marker = '<SENTINEL-ROWS>'
        filled = tpl.replace('{coins_stats_html}', marker)

        start = filled.index('ap-chart-stats ap-stat-grid')
        depth = 1  # 从容器自身的 <div> 之后开始扫，故初始深度为 1
        end = None
        for i in range(filled.index('>', start), len(filled)):
            if filled.startswith('<div', i):
                depth += 1
            elif filled.startswith('</div>', i):
                depth -= 1
                if depth == 0:
                    end = i
                    break
        self.assertIsNotNone(end, '网格容器没有闭合')
        self.assertLess(
            filled.index(marker), end,
            '数据行插在网格容器**外面**；列宽不会跨行统一，'
            '各列仍会错位。')

    def test_stat_row_grid_fits_long_numbers(self):
        """统计数值行不能用固定列宽。

        资产那种 459115.0 会超出 90px 被折行，看着就和「最高/最低」
        标签不同行。要用 max-content + 可伸缩列并禁止折行。
        """
        src = (ROOT / 'module/webui/app_stat_action_point.py').read_text(
            encoding='utf-8')
        self.assertNotIn(
            'grid-template-columns:150px 100px 90px 90px 90px', src,
            '统计行还在用固定列宽；长数字会被折行，与标签错位。')
        self.assertEqual(src.count('class="ap-stat-row"'), 4,
                         '统计行数量不是 4，网格类没盖全。')
        for name in ('dark-alas.css', 'light-alas.css',
                     'advanced-material-alas.css',
                     'dark-advanced-material-overrides-alas.css'):
            css = (CSS / name).read_text(encoding='utf-8')
            with self.subTest(theme=name):
                self.assertIn('.ap-stat-row', css, f'{name} 没定义 .ap-stat-row')
                # 列定义已令牌化（各主题可独立声明），所以先取令牌值，
                # 再校验它确实是按内容撑开，而不是固定像素。
                m = re.search(r'--alas-stat-grid-cols:\s*([^;]+);', css)
                self.assertIsNotNone(
                    m, f'{name} 没定义 --alas-stat-grid-cols')
                cols = m.group(1).strip()
                self.assertEqual(
                    5, cols.count('max-content'),
                    f'{name} 的统计行不是 5 列 max-content：{cols}')
                self.assertRegex(
                    css,
                    r'\.ap-stat-row\s*\{[^}]*grid-template-columns:\s*'
                    r'var\(--alas-stat-grid-cols\)',
                    f'{name} 的 .ap-stat-row 没用网格列令牌')
                self.assertRegex(
                    css, r'\.ap-stat-row\s*\{[^}]*white-space:\s*nowrap',
                    f'{name} 的统计行允许折行，长数字会与标签错位')

    def test_stat_panels_are_frosted_glass(self):
        """高级材质下统计栏板块要是磨砂玻璃。

        只给半透明底色还不够，得配上 backdrop-filter —— 否则背景图
        一路透到底，看着就是一块纯色。
        """
        for name in ('advanced-material-alas.css',
                     'dark-advanced-material-overrides-alas.css'):
            css = (CSS / name).read_text(encoding='utf-8')
            with self.subTest(theme=name):
                self.assertRegex(
                    css, r'--alas-apple-card-bg:\s*rgba\(',
                    f'{name} 的卡片面不是半透明；磨砂无从谈起。')
        adv = (CSS / 'advanced-material-alas.css').read_text(encoding='utf-8')
        i = adv.index('#pywebio-scope-opsi_stats,')
        blk = adv[i:adv.index('}', adv.index('#pywebio-scope-commission_income {', i))]
        self.assertIn('backdrop-filter', blk,
                      '统计栏面板规则里没有 backdrop-filter，不是磨砂玻璃。')


if __name__ == '__main__':
    unittest.main()
