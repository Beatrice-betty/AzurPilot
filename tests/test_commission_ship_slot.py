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


class TestCommissionDockRowStep(unittest.TestCase):
    def test_row_step_from_real_dock(self):
        """实机船坞 131/160：19 行、视口 3 行 → 两行步长约 0.125。"""
        image = np.array(Image.open(FIXTURES / 'commission_dock_list.png').convert('RGB'))
        fake = types.SimpleNamespace(device=types.SimpleNamespace(image=image))
        self.assertAlmostEqual(
            RewardCommission._commission_dock_row_step(fake), 0.125, places=3)


if __name__ == '__main__':
    unittest.main()
