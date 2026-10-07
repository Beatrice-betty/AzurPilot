"""滚动条三行标定、行号完整性与金/彩识别范围的真实截图回归。"""

from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

from module.base.utils import load_image
from module.storage.statistics_recognition import (StorageCard, StorageCatalog, StorageRecognitionError,
    StorageTraversal, calibrate_scroll, detect_rows, is_purple, recognize_rows, same_targets, verify_targets)

FIXTURES = Path(__file__).parent / 'fixtures/storage_statistics'


class ThreeRowPagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = StorageCatalog()

    def test_native_overlap_calibrates_three_row_distance(self):
        before = detect_rows(load_image(str(FIXTURES / 'scrollbar_top.png')))
        after = detect_rows(load_image(str(FIXTURES / 'scrollbar_calibration.png')))
        pitch, scale = calibrate_scroll(before, after, 17)
        self.assertEqual(pitch, 178)
        self.assertAlmostEqual(scale, 181 / 17)
        self.assertAlmostEqual(3 * pitch / scale, 50.1547, places=3)

    def test_wrong_direction_and_ambiguous_calibration_are_rejected(self):
        rows = detect_rows(load_image(str(FIXTURES / 'scrollbar_top.png')))
        with self.assertRaises(StorageRecognitionError):
            calibrate_scroll(rows, rows, -1)
        with self.assertRaises(StorageRecognitionError):
            calibrate_scroll(rows, list(reversed(rows)), 17)

    def test_rainbow_calibration_requires_five_matching_columns_and_unique_offset(self):
        before = detect_rows(load_image(str(FIXTURES / 'scrollbar_rainbow_top.png')))
        after = detect_rows(load_image(str(FIXTURES / 'scrollbar_rainbow_calibration.png')))
        pitch, scale = calibrate_scroll(before, after, 15)
        self.assertEqual(pitch, 178)
        self.assertAlmostEqual(scale, 238 / 15)
        self.assertAlmostEqual(3 * pitch / scale, 33.6555, places=3)
        with patch('module.storage.statistics_recognition.same_card',
                   side_effect=lambda a, b: a is before[2][0] or a is before[2][1]
                   or a is before[2][2] or a is before[2][3]):
            with self.assertRaisesRegex(StorageRecognitionError, '缺少唯一完整重叠行'):
                calibrate_scroll(before, after, 15)
        with patch('module.storage.statistics_recognition.same_card', return_value=True):
            with self.assertRaisesRegex(StorageRecognitionError, '缺少唯一完整重叠行'):
                calibrate_scroll(before, after, 15)

    def test_target_only_preserves_all_original_25_counts(self):
        for number in range(1, 5):
            image = load_image(str(FIXTURES / f'page_{number}.png'))
            full = recognize_rows(image, self.catalog)
            targets = recognize_rows(image, self.catalog, target_only=True)
            self.assertTrue(all(same_targets(a, b) for a, b in zip(full, targets)))
            self.assertTrue(all(card.comparison_amount is None for row in targets for card in row))

    def test_purple_boundary_skips_identity_and_amount_matching(self):
        image = load_image(str(FIXTURES / 'purple_boundary.png'))
        raw = detect_rows(image)
        self.assertTrue(is_purple(raw[-1][0].image))
        with patch.object(self.catalog, 'identify', wraps=self.catalog.identify) as identify, \
             patch.object(self.catalog, 'read_amount', wraps=self.catalog.read_amount) as amount:
            rows = recognize_rows(image, self.catalog, target_only=True)
        self.assertTrue(all(card.identifier is None and card.amount is None for card in rows[-1]))
        self.assertEqual(identify.call_count, 14)
        amount.assert_not_called()
        self.assertTrue(all(not is_purple(call.args[0]) for call in identify.call_args_list))

    def test_early_purple_gifts_do_not_hide_later_rainbow_targets(self):
        rows = recognize_rows(load_image(str(FIXTURES / 'scrollbar_top.png')),
                              self.catalog, target_only=True)
        self.assertEqual(rows[1][-1].identifier, 'PrototypeGearPartsT5')
        self.assertEqual(rows[1][-1].amount, 16)
        self.assertEqual(rows[2][2].identifier, 'SecretDesignPlanT5')
        self.assertEqual(rows[2][2].amount, 9)

    def test_same_page_verification_only_matches_confirmed_targets(self):
        image = load_image(str(FIXTURES / 'page_3.png'))
        previous = recognize_rows(image, self.catalog, target_only=True)
        with patch.object(self.catalog, 'identify', side_effect=AssertionError('不能重跑全目录')), \
             patch.object(self.catalog, 'read_amount', wraps=self.catalog.read_amount) as reader, \
             patch.object(self.catalog, '_scores', wraps=self.catalog._scores) as scores:
            rows = verify_targets(detect_rows(image), previous, self.catalog)
        self.assertTrue(all(same_targets(a, b) for a, b in zip(rows, previous)))
        targets = [card for row in previous for card in row if card.identifier]
        self.assertEqual(reader.call_count, len(targets))
        self.assertEqual(scores.call_count, len(targets))
        self.assertEqual([call.args[2] for call in scores.call_args_list],
                         [card.identifier for card in targets])

    def test_same_page_amount_conflict_and_different_icon_are_rejected(self):
        image = load_image(str(FIXTURES / 'page_3.png'))
        previous = recognize_rows(image, self.catalog, target_only=True)
        old = next(card for row in previous for card in row if card.identifier)
        with patch.object(self.catalog, 'read_amount', return_value=old.amount + 1) as reader:
            card = StorageCard(old.area, old.image, old.context)
            with self.assertRaisesRegex(StorageRecognitionError, '同页数量不一致'):
                self.catalog.verify(card, old)
        reader.assert_called_once()
        other = next(card for row in previous for card in row
                     if card.identifier and card.identifier != old.identifier)
        card = StorageCard(old.area, other.image, other.context)
        with self.assertRaisesRegex(StorageRecognitionError, '同页图标变化'):
            self.catalog.verify(card, old)

    def test_same_page_missing_columns_are_rejected(self):
        image = load_image(str(FIXTURES / 'scrollbar_top.png'))
        previous = recognize_rows(image, self.catalog, target_only=True)
        rows = detect_rows(image)
        rows[0].pop()
        with self.assertRaisesRegex(StorageRecognitionError, '同页材料列数变化'):
            verify_targets(rows, previous, self.catalog)

    def test_native_rainbow_plan_phase_preserves_identity_and_complete_counts(self):
        image = load_image(str(FIXTURES / 'live_rainbow_plan_phase.png'))
        rows = recognize_rows(image, self.catalog, target_only=True)
        self.assertEqual({card.identifier: card.amount for row in rows for card in row if card.identifier},
                         {'GearDesignPlanTorpedoT5': 21, 'GearDesignPlanAntiAirT5': 47,
                          'GearDesignPlanPlaneT5': 59, 'SecretDesignPlanT5': 1})
        confirmed = verify_targets(detect_rows(image), rows, self.catalog)
        self.assertTrue(all(same_targets(a, b) for a, b in zip(rows, confirmed)))

    def test_numbered_three_row_pages_accept_contiguous_rows_and_reject_a_gap(self):
        image = np.zeros((128, 128, 3), dtype=np.uint8)
        rows = [[StorageCard((140, 86, 268, 214), image, identifier=f'item_{index}', amount=index + 1)]
                for index in range(9)]
        traversal = StorageTraversal()
        traversal.append(rows[:3], row_start=0)
        traversal.append(rows[3:6], row_start=3)
        self.assertEqual(len(traversal.rows), 6)
        with self.assertRaisesRegex(StorageRecognitionError, '缺行'):
            traversal.append(rows[6:], row_start=7)
        self.assertEqual(len(traversal.rows), 6)
        self.assertEqual(traversal.pages, 2)
        traversal.append(rows[4:7], row_start=4, at_bottom=True)
        self.assertEqual(len(traversal.rows), 7)
        with self.assertRaisesRegex(StorageRecognitionError, '不一致'):
            traversal.append(rows[5:8], row_start=4, at_bottom=True)


if __name__ == '__main__':
    unittest.main()
