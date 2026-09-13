"""图表模板的渲染契约。

两个图表模板由 str.format() 渲染：
    html_tpl = read_webapp_template("ap_chart_panel.html")
    html = html_tpl.format(chart_id=..., ...)

所以模板里任何裸花括号都会让渲染抛 KeyError。
曾经在模板里加了一段 <style> 块（CSS 全是 { }），生产环境渲染图表时直接
`KeyError: '\\n    position'`，图表整个画不出来。这个测试就是钉住这一点。
"""
import re
import unittest
from pathlib import Path

WEBAPP = Path(__file__).resolve().parents[1] / 'webapp'

# 模板声明的占位符（与调用处 .format(...) 的关键字保持一致）
TEMPLATES = {
    'ap_chart_panel.html': {
        'chart_id', 'view_title', 'ap_cur', 'change_color', 'change_sign',
        'ap_change', 'ap_max', 'ap_min', 'ap_avg', 'coins_stats_html',
        'coins_legend_html', 'detail_controls_display',
    },
    'resource_chart.html': {
        'chart_id', 'title', 'stats_html', 'legend_html',
    },
}


class TestChartTemplates(unittest.TestCase):
    def test_templates_exist(self):
        for name in TEMPLATES:
            self.assertTrue((WEBAPP / name).is_file(), f'{name} 缺失')

    def test_format_does_not_raise(self):
        """核心回归：模板必须能被 .format() 渲染。"""
        for name, keys in TEMPLATES.items():
            text = (WEBAPP / name).read_text(encoding='utf-8')
            with self.subTest(template=name):
                try:
                    text.format(**{k: 'X' for k in keys})
                except (KeyError, IndexError, ValueError) as e:
                    self.fail(
                        f'{name} 无法被 .format() 渲染：{type(e).__name__}: {e}\n'
                        '模板里的裸花括号会被当作占位符。'
                        'CSS 等含花括号的内容请放到 assets/gui/css/ 里。')

    def test_no_style_block(self):
        """模板里不放过 <style>：它正是花括号的来源。"""
        for name in TEMPLATES:
            text = (WEBAPP / name).read_text(encoding='utf-8')
            with self.subTest(template=name):
                self.assertNotIn(
                    '<style>', text,
                    f'{name} 含 <style> 块，CSS 花括号会让 .format() 抛 KeyError。'
                    '请把样式放到 alas.css。')

    def test_only_declared_placeholders(self):
        """模板里的花括号必须都是已知占位符。"""
        for name, keys in TEMPLATES.items():
            text = (WEBAPP / name).read_text(encoding='utf-8')
            found = set(re.findall(r'\{(\w+)\}', text))
            with self.subTest(template=name):
                unknown = found - keys
                self.assertFalse(
                    unknown, f'{name} 出现未声明的占位符 {sorted(unknown)}')
                missing = keys - found
                self.assertFalse(
                    missing, f'{name} 缺少占位符 {sorted(missing)}')

    def test_chart_classes_defined_in_css(self):
        """模板用的类名必须在 CSS 里有定义，否则图表容器没有主题样式。"""
        css_dir = Path(__file__).resolve().parents[1] / 'assets' / 'gui' / 'css'
        base = (css_dir / 'alas.css').read_text(encoding='utf-8')
        for cls in ('ap-chart-box', 'ap-chart-legend', 'ap-chart-stats',
                    'ap-chart-tip', 'ap-chart-ctrl'):
            with self.subTest(cls=cls):
                self.assertIn(f'.{cls}', base,
                              f'alas.css 未定义 .{cls}')

    def test_no_hardcoded_dark_container(self):
        """图表面板不应再出现写死的深色内联样式。"""
        for name in TEMPLATES:
            text = (WEBAPP / name).read_text(encoding='utf-8')
            with self.subTest(template=name):
                self.assertNotIn('background:#1a1a2e', text)
                self.assertNotIn('border:1px solid #333', text)


if __name__ == '__main__':
    unittest.main()
