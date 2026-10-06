"""用实机截图验证委托详情面板的空槽位判别。

fixture 截图来自 2026-10-06 实机测试（1280x720 国服）：
推荐填充 5 艘不满足等级的舰船后开始按钮仍灰（触发进船坞兜底），
以及同一委托手动清空槽位后的全空状态。
"""

import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from module.commission.commission import (
    COMMISSION_SHIP_SLOTS,
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


if __name__ == '__main__':
    unittest.main()
