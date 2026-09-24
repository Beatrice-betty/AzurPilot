"""科研统计页两个视图（期数 / 心智物资）的版式一致性。

前端按后端返回的数据形状渲染：`metrics` 出收益卡片、`tables` 出明细表，所以
「版式一致」= 两个口径返回同样的段落结构与列名。这里锁住这一点，免得以后
其中一个视图被改回单表。

用假记录喂进服务层，不碰 SQLite 与用户数据；名称表用仓库里的真表。
"""

from datetime import datetime, timedelta
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch


def module_stub(name, **attrs):
    module = ModuleType(name)
    module.__dict__.update(attrs)
    return module


class SysModules:
    """临时替换 sys.modules 里的若干项，退出时逐项还原。

    不用 ``patch.dict(sys.modules, ...)``：它退出时会还原整个 sys.modules 快照，
    把替换期间新进来的模块（numpy 的 C 扩展等）一并抹掉，之后再 import 就会报
    ``cannot load module more than once per process``，把后面的测试连带弄挂。
    """

    def __init__(self, **modules):
        self.modules = modules
        self.previous = {}

    def __enter__(self):
        for name, module in self.modules.items():
            self.previous[name] = sys.modules.get(name)
            sys.modules[name] = module
        return self

    def __exit__(self, *exc):
        for name in self.modules:
            previous = self.previous[name]
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def load_research_stats():
    """仅导入待测实现，日志模块用替身，避免初始化用户配置与日志目录。"""
    path = Path(__file__).resolve().parents[1] / 'module/statistics/research_stats.py'
    spec = importlib.util.spec_from_file_location('_layout_research_stats', path)
    module = importlib.util.module_from_spec(spec)
    stub = module_stub('module.logger', logger=Mock(), warning=Mock(), info=Mock())
    with SysModules(**{'module.logger': stub}):
        spec.loader.exec_module(module)
    return module


def load_service():
    """导入统计服务（模块级只依赖 pydantic 的协议模型，无需替身）。"""
    path = Path(__file__).resolve().parents[1] / 'module/api/statistics_service.py'
    spec = importlib.util.spec_from_file_location('_layout_statistics_service', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeDb:
    """只看月份的科研掉落库替身。"""

    def __init__(self, entries):
        self.entries = entries

    def get_research_drop(self, instance, year, month):
        prefix = f'{year:04d}-{month:02d}'
        return [item for item in self.entries if str(item['ts']).startswith(prefix)]


def entry(items, series, when):
    return {'ts': when.isoformat(), 'project': 'G-531-MI', 'series': series, 'items': items}


class ResearchReportLayoutTest(unittest.TestCase):
    """两个视图共用「收益卡片 + 收获明细 + 掉落记录」三段式。"""

    def setUp(self):
        self.now = datetime.now()
        self.entries = [
            # 九期：物资 + 一张船图纸（船图纸只有期数视图认）
            entry({'Coins': 120, 'BlueprintTakahashi': 1}, 9, self.now),
            # 七期：心智单元 + 物资
            entry({'CognitiveChips': 40, 'Coins': 88}, 7, self.now - timedelta(days=2)),
        ]
        self.stats = load_research_stats()
        self.api = load_service()
        self.enterContext(SysModules(**{
            'module.statistics.cl1_database': module_stub('module.statistics.cl1_database',
                                                          db=FakeDb(self.entries)),
            'module.statistics.research_stats': self.stats,
        }))
        self.enterContext(patch.object(self.stats, '_iter_entries',
                                       lambda *args, **kwargs: iter(self.entries)))
        self.configs = SimpleNamespace(path=Mock())

    def consumable(self):
        return self.api.report(self.configs, 'alas', 'research', None, 365, 'month', 0, 'consumable')

    def series(self):
        return self.api.report(self.configs, 'alas', 'research',
                               self.now.strftime('%Y-%m'), 7, 'month', 9, 'series')

    def test_consumable_has_the_same_sections_and_columns_as_series(self):
        consumable = self.consumable()
        series = self.series()
        self.assertEqual([item['title'] for item in consumable['tables']],
                         ['心智/物资收获明细', '掉落记录'])
        for index in (0, 1):
            with self.subTest(table=index):
                self.assertEqual(consumable['tables'][index]['columns'],
                                 series['tables'][index]['columns'])

    def test_consumable_cards_list_both_items_with_icons(self):
        """卡片行与期数视图同构：先掉落记录，再每件物品一张带图标的卡。"""
        metrics = self.consumable()['metrics']
        self.assertEqual([item['label'] for item in metrics], ['掉落记录', '心智单元', '物资'])
        self.assertEqual([item.get('icon') for item in metrics],
                         [None, 'research:CognitiveChips', 'research:Coins'])
        self.assertEqual(metrics[0]['value'], 2)
        self.assertEqual(metrics[1]['value'], 40)
        self.assertEqual(metrics[2]['value'], 208)

    def test_consumable_detail_keeps_a_row_per_item(self):
        """没掉过的物品也留一行（显示「—」），与期数视图的固定清单一致。"""
        rows = self.consumable()['tables'][0]['rows']
        self.assertEqual([row[1] for row in rows], ['心智单元', '物资'])
        self.assertEqual([row[2] for row in rows], ['金', '—'])
        self.assertEqual([row[3] for row in rows], [40, 208])

    def test_consumable_detail_shows_dash_for_missing_item(self):
        """没掉过的那件留空行显示「—」，而不是整行消失。"""
        self.entries[:] = [item for item in self.entries if 'CognitiveChips' not in item['items']]
        rows = self.consumable()['tables'][0]['rows']
        self.assertEqual([row[1] for row in rows], ['心智单元', '物资'])
        self.assertIsNone(rows[0][3])
        self.assertIsNone(rows[0][4])
        self.assertEqual(rows[1][3], 120)

    def test_consumable_records_exclude_series_only_items(self):
        """掉落记录只列本视图认的物品：那次的船图纸不算进心智/物资口径。"""
        records = self.consumable()['tables'][1]['rows']
        self.assertEqual(len(records), 2)
        for row in records:
            with self.subTest(row=row):
                self.assertNotIn('蓝图', row[3])
                self.assertIn('物资', row[3])


if __name__ == '__main__':
    unittest.main()
