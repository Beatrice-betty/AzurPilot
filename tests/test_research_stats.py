"""科研掉落汇总：期数视图 / 金装视图 / 今日本月计数。

用假记录喂进汇总层，不碰 SQLite 与用户数据；名称表用仓库里的真表，
这样「金装 = 稀有度 4 的装备图纸」这条口径是拿真数据验证的。
"""

from datetime import datetime, timedelta
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import Mock, patch


def module_stub(name, **attrs):
    module = ModuleType(name)
    module.__dict__.update(attrs)
    return module


def load_research_stats():
    """仅导入待测实现，日志模块用替身，避免初始化用户配置与日志目录。"""
    path = Path(__file__).resolve().parents[1] / 'module/statistics/research_stats.py'
    spec = importlib.util.spec_from_file_location('_research_stats_test', path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'module.logger': module_stub('module.logger', logger=Mock())}):
        spec.loader.exec_module(module)
    return module


RESEARCH_STATS = load_research_stats()

# 取自真实名称表的四类代表：彩装备、金装备、金船图纸、心智单元
RAINBOW_GEAR = 'Prototype_Quadruple_305mm_SKC39_Main_Gun_Mount_T0'   # r=5 彩装
GOLD_GEAR = 'Prototype_Quadruple_610mm_Cruiser_Torpedo_Mount_T0'     # r=4 金装
GOLD_BLUEPRINT = 'BlueprintTakahashi'                                # r=4 金船图
CHIPS = 'CognitiveChips'                                             # 心智单元


def entry(items, series, when):
    return {
        'ts': when.isoformat(),
        'completed_at': when.isoformat(),
        'project': 'G-531-MI',
        'series': series,
        'items': items,
    }


class ResearchStatsScopeTest(unittest.TestCase):
    """展示口径：期数视图看彩装/图纸/心智，金装视图只看金装备图纸且不分期。"""

    def setUp(self):
        self.now = datetime.now()
        entries = [
            # 九期：彩装 + 金装 + 金船图 + 心智
            entry({RAINBOW_GEAR: 2, GOLD_GEAR: 3, GOLD_BLUEPRINT: 1, CHIPS: 40}, 9, self.now),
            # 七期：只掉金装
            entry({GOLD_GEAR: 5}, 7, self.now - timedelta(days=2)),
        ]
        self.patcher = patch.object(RESEARCH_STATS, '_iter_entries', lambda *a, **k: iter(entries))
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def collect(self, **kwargs):
        return RESEARCH_STATS.collect('alas', days=90, **kwargs)

    def test_series_view_shows_rainbow_blueprint_chips(self):
        summary = self.collect(series=9)
        names = [item['name'] for item in summary['items']]
        self.assertEqual(summary['scope'], 'series')
        self.assertIn(RAINBOW_GEAR, names)
        self.assertIn(GOLD_BLUEPRINT, names)
        self.assertIn(CHIPS, names)

    def test_series_view_hides_gold_gear(self):
        names = [item['name'] for item in self.collect(series=9)['items']]
        self.assertNotIn(GOLD_GEAR, names)

    def test_gold_view_merges_all_series(self):
        summary = self.collect(gold=True)
        self.assertEqual(summary['scope'], 'gold')
        self.assertEqual(summary['series'], 0)
        # 九期的 3 张与七期的 5 张合并成 8
        self.assertEqual([item['name'] for item in summary['items']], [GOLD_GEAR])
        self.assertEqual(summary['items'][0]['amount'], 8)
        self.assertEqual(summary['items'][0]['count'], 2)
        self.assertEqual(summary['records'], 2)

    def test_gold_view_excludes_non_gear(self):
        names = [item['name'] for item in self.collect(gold=True)['items']]
        self.assertNotIn(RAINBOW_GEAR, names)   # 彩装不属于金装
        self.assertNotIn(GOLD_BLUEPRINT, names)  # 舰船图纸不属于金装
        self.assertNotIn(CHIPS, names)           # 心智单元不属于金装

    def test_series_view_keeps_only_selected_series(self):
        summary = self.collect(series=7)
        self.assertEqual(summary['items'], [])  # 七期只有金装，期数视图不展示
        self.assertEqual(summary['records'], 1)
        self.assertEqual(summary['available'], [9, 7])

    def test_series_default_picks_latest_available(self):
        self.assertEqual(self.collect(series=0)['series'], 9)


class ResearchStatsTodayMonthTest(unittest.TestCase):
    """今日 / 本月计数：按记录时间戳分桶，时间戳缺失时不计入任何一桶。"""

    def setUp(self):
        now = datetime.now()
        first_this_month = now.replace(day=1, hour=0, minute=0)
        old = now - timedelta(days=200)
        self.now = now
        entries = [
            entry({GOLD_GEAR: 2}, 9, now),
            entry({GOLD_GEAR: 3}, 9, first_this_month),
            entry({GOLD_GEAR: 5}, 9, old),
            {'project': 'G-531-MI', 'series': 9, 'items': {GOLD_GEAR: 7}},  # 没有时间戳
        ]
        self.patcher = patch.object(RESEARCH_STATS, '_iter_entries', lambda *a, **k: iter(entries))
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_today_and_month_buckets(self):
        summary = RESEARCH_STATS.collect('alas', days=365, series=9, gold=True)
        item = summary['items'][0]
        self.assertEqual(item['amount'], 17)
        self.assertEqual(item['today'], 2)
        # 「本月」= 今天那条 + 本月 1 号那条；即使今天就是 1 号，两条也在同一个月里
        self.assertEqual(item['month'], 5)
        self.assertEqual(summary['today'], 2)
        self.assertEqual(summary['month'], 5)

    def test_missing_timestamp_is_not_counted_as_today(self):
        summary = RESEARCH_STATS.collect('alas', days=365, series=9, gold=True)
        self.assertLess(summary['today'], 7)
        self.assertEqual(summary['records'], 4)


if __name__ == '__main__':
    unittest.main()
