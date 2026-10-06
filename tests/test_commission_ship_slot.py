"""用实机截图验证委托船坞的视觉判别。

fixture 截图来自 2026-10-06/07 实机测试（1280x720 国服）：
- 推荐填充 5 艘不满足等级的舰船后开始按钮仍灰（触发进船坞兜底），
  以及同一委托手动清空槽位后的全空状态；
- 点选编队中舰船时弹出的"是否移出编队"询问弹窗。
"""

import types
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from module.commission.commission import (
    COMMISSION_SHIP_SLOTS,
    RewardCommission,
    commission_slot_is_empty,
)


FIXTURES = Path(__file__).parent / 'fixtures' / 'commission_ship_slot'


class TestCommissionShipSlot(unittest.TestCase):
    def test_recommended_5_ships_only_last_slot_empty(self):
        """推荐填 5 艘后，前 5 格判为有船，最右侧第 6 格判为空槽。"""
        image = np.array(
            Image.open(FIXTURES / 'commission_detail_recommended_5.png').convert('RGB'))
        states = [commission_slot_is_empty(image, b) for b in COMMISSION_SHIP_SLOTS.buttons]
        self.assertEqual(states, [False, False, False, False, False, True])

    def test_empty_panel_all_slots_empty(self):
        """未选船时六个槽位全部判为空槽。"""
        image = np.array(
            Image.open(FIXTURES / 'commission_detail_empty_slots.png').convert('RGB'))
        states = [commission_slot_is_empty(image, b) for b in COMMISSION_SHIP_SLOTS.buttons]
        self.assertEqual(states, [True] * 6)


class TestCommissionFleetQuestion(unittest.TestCase):
    @staticmethod
    def _detect(image):
        fake = types.SimpleNamespace(device=types.SimpleNamespace(image=image))
        return RewardCommission._commission_dock_fleet_question(fake)

    def test_fleet_question_popup_detected(self):
        """弹窗截图：红 X 区域判为舰队询问弹窗。"""
        image = np.array(Image.open(FIXTURES / 'commission_fleet_question.png').convert('RGB'))
        self.assertTrue(self._detect(image))

    def test_dock_without_popup_not_detected(self):
        """船坞界面（无弹窗）与详情面板截图不误判为弹窗。"""
        for name in ['commission_dock_list.png',
                     'commission_detail_recommended_5.png',
                     'commission_detail_empty_slots.png']:
            with self.subTest(file=name):
                image = np.array(Image.open(FIXTURES / name).convert('RGB'))
                self.assertFalse(self._detect(image))


class TestCommissionDockGapOffset(unittest.TestCase):
    """行间缝检测：对齐读数与滚动偏移回读。"""

    @staticmethod
    def _detect(image):
        fake = types.SimpleNamespace(device=types.SimpleNamespace(image=image))
        return RewardCommission._commission_dock_gap_offset(fake)

    @staticmethod
    def _fixture():
        return np.array(
            Image.open(FIXTURES / 'commission_dock_list.png').convert('RGB'))

    def test_aligned_fixture_offset_near_zero(self):
        """列表停在顶部、游戏自然对齐的实机截图：偏移应接近 0。

        该截图上带用户标注的红色箭头横穿行间缝，
        平滑后不应影响缝的定位。
        """
        offset = self._detect(self._fixture())
        self.assertLessEqual(abs(offset), 9)

    def test_shifted_content_reports_shift(self):
        """整体滚动内容后，偏移随移动量变化，误差在几个像素内。"""
        image = self._fixture()
        for roll in (-45, 60, -100):
            with self.subTest(roll=roll):
                offset = self._detect(np.roll(image, roll, axis=0))
                expected = (291 - 292 + roll + 113) % 227 - 113
                self.assertAlmostEqual(offset, expected, delta=6)


class TestCommissionDockScrollMetrics(unittest.TestCase):
    def test_pixels_per_position_from_thumb_fraction(self):
        """滑块占轨道 306/565（实机精英档测量值）时，
        每 1.0 位置约 548 内容像素，1.7 行的翻页步长约 0.7 位置。"""
        from module.retire.dock import DOCK_SCROLL
        old = DOCK_SCROLL.length
        try:
            DOCK_SCROLL.length = 306
            value = RewardCommission._commission_dock_pixels_per_position(
                types.SimpleNamespace())
            self.assertAlmostEqual(value, 548, delta=3)
        finally:
            DOCK_SCROLL.length = old


if __name__ == '__main__':
    unittest.main()
