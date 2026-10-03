"""仓库统计：真实截图、虚拟滚动设备和临时数据库，绝不操作真实账号。"""

from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from module.base.utils import load_image
from module.exception import StorageStatisticsError
from module.statistics.storage_snapshot import latest_snapshot, save_snapshot
from module.storage.statistics_recognition import (StorageCatalog, StorageRecognitionError,
    StorageTraversal, detect_rows, recognize_rows)

FIXTURES = Path(__file__).parent / 'fixtures/storage_statistics'


class RecognitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = StorageCatalog()
        cls.images = [load_image(str(FIXTURES / f'page_{n}.png')) for n in range(1, 5)]
        cls.cases = json.loads((FIXTURES / 'expected.json').read_text(encoding='utf-8'))

    def test_all_four_pages_have_only_the_25_marked_items(self):
        pages = [recognize_rows(image, self.catalog) for image in self.images]
        expected = {(case['page'], case['row'], case['column']): case for case in self.cases}
        self.assertEqual(len(expected), 25)
        for page, rows in enumerate(pages, 1):
            for row, cards in enumerate(rows):
                for column, card in enumerate(cards):
                    case = expected.get((page, row, column))
                    with self.subTest(page=page, row=row, column=column):
                        self.assertEqual(card.identifier, case['id'] if case else None)
                        self.assertEqual(card.amount, case['amount'] if case else None)

    def test_items_can_move_between_columns_and_rows(self):
        image = self.images[3].copy()
        rows = detect_rows(image)
        a, b = rows[0][0].area, rows[1][6].area
        x1, y1, x2, y2 = a
        u1, v1, u2, v2 = b
        first, second = image[y1:y2, x1:x2].copy(), image[v1:v2, u1:u2].copy()
        image[y1:y2, x1:x2], image[v1:v2, u1:u2] = second, first
        result = recognize_rows(image, self.catalog)
        self.assertIsNone(result[0][0].identifier)
        self.assertEqual((result[1][6].identifier, result[1][6].amount), ('GearDesignPlanGunT4', 699))

    def test_vertical_scroll_offset_and_clipped_rows(self):
        image = self.images[3].copy()
        image[65:637] = (32, 36, 50)
        image[88:637] = self.images[3][65:614]
        rows = recognize_rows(image, self.catalog)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0][0].amount, 699)
        self.assertGreater(rows[0][0].area[1], 90)

    def test_unreadable_amount_never_becomes_zero(self):
        icon = detect_rows(self.images[0])[0][1].image.copy()
        icon[99:126, 25:127] = (255, 255, 255)
        with self.assertRaises(StorageRecognitionError):
            self.catalog.read_amount(icon)

    def test_partially_erased_leading_digit_is_not_silently_dropped(self):
        icon = detect_rows(self.images[0])[0][1].image.copy()
        icon[104:118, 87:95] = (32, 36, 50)
        with self.assertRaises(StorageRecognitionError):
            self.catalog.read_amount(icon)

    def test_untrained_quantities_and_repeated_digits_from_other_items(self):
        rows = detect_rows(self.images[1])
        for row, column, amount in [(0, 0, 215), (0, 2, 143), (0, 5, 142),
                                    (2, 0, 77), (2, 1, 43), (2, 5, 854)]:
            with self.subTest(amount=amount):
                self.assertEqual(self.catalog.read_amount(rows[row][column].image), amount)

    def test_other_items_with_connected_icon_debris_are_rejected(self):
        rows = detect_rows(self.images[1])
        for column in [2, 6]:
            with self.subTest(column=column), self.assertRaises(StorageRecognitionError):
                self.catalog.read_amount(rows[1][column].image)

    def test_128_pixel_contour_at_bottom_is_still_a_clipped_row(self):
        device = InventoryDevice()
        device.position = .971
        self.assertEqual(len(detect_rows(device.screenshot())), 2)

    def test_overlap_is_deduplicated_and_missing_overlap_rejected(self):
        rows = [row for image in self.images[:2] for row in recognize_rows(image, self.catalog)]
        scan = StorageTraversal()
        scan.append(rows[:3])
        scan.append(rows[2:5])
        self.assertEqual(len(scan.rows), 5)
        quantities = {item['id']: item['amount'] for item in self.catalog.snapshot_items(scan.rows)}
        self.assertEqual(quantities['PrototypeGearPartsT5'], 153)
        with self.assertRaises(StorageRecognitionError):
            scan.append(rows[:2])

    def test_ambiguous_repeating_rows_are_rejected(self):
        row = recognize_rows(self.images[0], self.catalog)[0]
        scan = StorageTraversal()
        scan.append([row, row])
        with self.assertRaises(StorageRecognitionError):
            scan.append([row, row, row])

    def test_final_overlapping_page_is_not_counted_twice(self):
        rows = recognize_rows(self.images[0], self.catalog)
        scan = StorageTraversal()
        scan.append(rows)
        scan.append(rows[-2:], at_bottom=True)
        self.assertEqual(len(scan.rows), 3)
        self.assertEqual(scan.pages, 2)
        with self.assertRaises(StorageRecognitionError):
            scan.append(rows[-2:])

    def test_last_partial_row_has_consecutive_empty_slots(self):
        image = self.images[3].copy()
        image[422:604, 296:1235] = (32, 36, 50)
        rows = detect_rows(image)
        self.assertEqual(len(rows), 3)
        self.assertEqual([card.present for card in rows[-1]], [True, False, False, False, False, False, False])


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(self.temp) / 'config/storage_statistics.db'
        self.items = [dict(id='chips', name='心智单元', group='材料', amount=13393),
                      dict(id='absent', name='未发现物品', group='材料', amount=None)]

    def save(self, instance='alpha', items=None):
        return save_snapshot(instance, 'cn', items or self.items, started_at=datetime.now().isoformat(),
                             pages=6, catalog_version='test', database=self.path)

    def test_read_before_run_does_not_create_any_file(self):
        self.assertIsNone(latest_snapshot('alpha', database=self.path))
        self.assertFalse(self.path.parent.exists())

    def test_latest_snapshot_isolated_by_instance_and_read_only(self):
        self.save()
        self.save('beta', [dict(self.items[0], amount=4)])
        before = self.path.read_bytes()
        result = latest_snapshot('alpha', database=self.path)
        self.assertEqual(result['items'][0]['amount'], 13393)
        self.assertIsNone(result['items'][1]['amount'])
        self.assertEqual(before, self.path.read_bytes())
        self.assertIsNone(latest_snapshot('gamma', database=self.path))

    def test_invalid_counts_and_partial_transaction_keep_old_snapshot(self):
        scan_id = self.save()
        for value in [0, -1, True, 1.5]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.save(items=[dict(self.items[0], amount=value)])
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("CREATE TRIGGER abort_item BEFORE INSERT ON storage_items BEGIN SELECT RAISE(ABORT, 'test'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.save()
        self.assertEqual(latest_snapshot('alpha', database=self.path)['id'], scan_id)

    def test_report_only_reads_saved_counts(self):
        from module.api.statistics_service import report
        configs = SimpleNamespace(path=Mock(return_value=self.path.parent / 'alpha.json'))
        first = report(configs, 'alpha', 'storage', None, 7, 'month')
        self.assertTrue(all(row[3] is None for row in first['tables'][0]['rows']))
        self.assertFalse(self.path.exists())
        self.save()
        before = self.path.read_bytes()
        result = report(configs, 'alpha', 'storage', None, 7, 'month')
        self.assertEqual(result['tables'][0]['rows'][0][3], 13393)
        self.assertEqual(before, self.path.read_bytes())


class FrameTimer:
    """测试时用访问次数代替时间，保持真实任务的状态转移。"""
    def __init__(self, seconds, count=0):
        self.seconds = seconds
        self.count = 0
    def start(self):
        return self
    def reset(self):
        self.count = 0
        return self
    def clear(self):
        return self.reset()
    def reached(self):
        self.count += 1
        return self.count > 100 if self.seconds >= 20 else True


class InventoryDevice:
    """按真实滚动条几何生成完整材料列表截图，记录所有操作。"""
    def __init__(self):
        images = [load_image(str(FIXTURES / f'page_{n}.png')) for n in range(1, 5)]
        strips = []
        for image in images:
            for row in detect_rows(image):
                y = row[0].area[1] - 6
                strips.append(image[y:y + 178, 130:1235])
        self.content = np.concatenate(strips)
        self.base = images[0]
        self.position = .37
        self.length = round(572 / len(self.content) * 472)
        self.swipes = []
        self.screenshot()

    def screenshot(self):
        image = self.base.copy()
        offset = round((len(self.content) - 572) * self.position)
        image[65:637, 130:1235] = self.content[offset:offset + 572]
        image[104:576, 1256:1265] = (25, 26, 38)
        y = 104 + round(self.position * (472 - self.length))
        image[y:y + self.length, 1256:1265] = (247, 211, 66)
        self.image = image
        return image

    def swipe(self, start, end, **kwargs):
        self.swipes.append((start, end))
        self.position = min(1., max(0., (end[1] - 104 - self.length / 2) / (472 - self.length)))


class TaskTests(unittest.TestCase):
    def setUp(self):
        from module.storage.statistics import StorageStatistics
        self.module = __import__('module.storage.statistics', fromlist=['StorageStatistics'])
        self.task = StorageStatistics.__new__(StorageStatistics)
        self.task.config = SimpleNamespace(config_name='test', task_delay=Mock())
        self.task.device = InventoryDevice()
        self.task.ui_goto_storage = Mock()
        self.task._storage_enter_material = Mock()
        self.task._storage_in_material = Mock(return_value=True)
        self.task.handle_info_bar = Mock(return_value=False)
        def frames(**kwargs):
            for _ in range(250):
                yield self.task.device.screenshot()
        self.task.loop = frames
        self.enterContext(patch.object(self.module, 'Timer', FrameTimer))
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(self.temp) / 'warehouse.db'
        self.enterContext(patch.object(self.module, 'save_snapshot',
            side_effect=lambda *args, **kwargs: save_snapshot(*args, **kwargs, database=self.path)))

    def test_task_navigates_and_scans_twice_before_atomic_commit(self):
        self.task.run()
        self.task.ui_goto_storage.assert_called_once()
        self.task._storage_enter_material.assert_called_once()
        snapshot = latest_snapshot('test', database=self.path)
        expected = json.loads((FIXTURES / 'expected.json').read_text(encoding='utf-8'))
        self.assertEqual({item['id']: item['amount'] for item in snapshot['items']},
                         {item['id']: item['amount'] for item in expected})
        self.assertGreater(snapshot['pages'], 4)
        self.assertGreater(len(self.task.device.swipes), 4)
        self.task.config.task_delay.assert_called_once_with(success=True)

    def test_failed_second_pass_does_not_write_a_snapshot(self):
        original = self.task._scan_pass
        calls = 0
        def scan(catalog):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise StorageRecognitionError('复核失败')
            return original(catalog)
        self.task._scan_pass = scan
        with self.assertRaises(StorageStatisticsError):
            self.task.run()
        self.assertFalse(self.path.exists())
        self.task.config.task_delay.assert_called_once_with(success=False)

    def test_single_page_with_full_scroll_handle_is_complete(self):
        device = self.task.device
        padding = np.full((38, device.content.shape[1], 3), (32, 36, 50), dtype=np.uint8)
        device.content = np.concatenate([device.content[:534], padding])
        device.length = 472
        self.task.run()
        snapshot = latest_snapshot('test', database=self.path)
        quantities = {item['id']: item['amount'] for item in snapshot['items']}
        self.assertEqual(quantities['PrototypeGearPartsT5'], 153)
        self.assertIsNone(quantities['CognitiveChips'])
        self.assertEqual(snapshot['pages'], 2)
        self.assertEqual(device.swipes, [])


class ApiIntegrationTests(unittest.TestCase):
    def test_storage_report_over_websocket_and_nested_static_icon(self):
        import shutil
        from starlette.testclient import TestClient
        from module.api.app import create_app
        from module.api.config_service import ROOT
        from tests.test_api import fixture
        temp = self.enterContext(tempfile.TemporaryDirectory())
        root = fixture(temp)
        relative = 'assets/stats/opsi_items/PrototypeGearPartsT5.png'
        destination = root / relative
        destination.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / relative, destination)
        app = create_app(root=root, password='', manage_runtime=False, mount_mcp=False)
        client = TestClient(app)
        before = (root / 'config/testpilot.json').read_bytes()
        with client.websocket_connect('/api/v1/ws') as ws:
            ws.receive_json()
            ws.send_json({'v': 1, 'type': 'request', 'id': 'storage', 'method': 'statistics.report',
                          'params': {'instance': 'testpilot', 'category': 'storage'}})
            response = ws.receive_json()
            self.assertTrue(response['ok'], response)
            rows = response['result']['tables'][0]['rows']
            self.assertEqual(len(rows), 25)
            self.assertTrue(all(row[3] is None and row[4] == '未扫描' for row in rows))
        self.assertEqual(before, (root / 'config/testpilot.json').read_bytes())
        self.assertFalse((root / 'config/storage_statistics.db').exists())
        icon = client.get('/storage-items/opsi_items/PrototypeGearPartsT5.png')
        self.assertEqual(icon.status_code, 200)
        self.assertEqual(icon.content, destination.read_bytes())

    def test_manual_storage_task_uses_existing_instance_mutex(self):
        from module.api.protocol import ApiError
        from module.api.runtime_service import RuntimeService
        runtime = RuntimeService(SimpleNamespace(path=lambda _: None))
        manager = SimpleNamespace(alive=False)
        manager.start = Mock(side_effect=lambda *args, **kwargs: setattr(manager, 'alive', True))
        stop_event = object()
        with patch.object(runtime, 'manager', return_value=manager), \
             patch.object(runtime, 'overview', return_value={}), \
             patch.object(runtime, '_record_running_now'), \
             patch('module.runtime.updater.updater', SimpleNamespace(event=stop_event)):
            runtime.start('testpilot', 'StorageStatistics')
            with self.assertRaises(ApiError) as raised:
                runtime.start('testpilot', 'StorageStatistics')
        self.assertEqual(raised.exception.code, 'INSTANCE_RUNNING')
        manager.start.assert_called_once_with('StorageStatistics', ev=stop_event)


if __name__ == '__main__':
    unittest.main()
