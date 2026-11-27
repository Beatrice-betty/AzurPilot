"""手动战斗模式管理模块。

管理手动战斗中的舰队移动和操作策略。

手动战斗模式下，需要通过摇杆控制舰队移动。
支持的操作模式：
- stand_still_in_the_middle: 舰队停留在画面中央（适合防空关卡）
- 其他自定义移动模式

手动模式通常用于：
- 需要精确控制舰队位置的关卡
- 自动模式无法通过的高难度关卡
- 特殊战术需求（如潜艇战）

继承自 ModuleBase，被 Combat 组合使用。
"""

from contextlib import contextmanager

from module.base.base import ModuleBase
from module.combat.assets import *

SCUTTLE_MANUAL_MODES = ('stand_still', 'drag_right')
# 手操摇杆拨向一侧时，圆心会超出常规的 20 像素搜索范围。
SCUTTLE_JOYSTICK_OFFSET = (55, 40)


class CombatManual(ModuleBase):
    """手动战斗模式管理器。

    管理手动战斗中的舰队移动和操作。

    Attributes:
        auto_mode_checked (bool): 自动模式是否已检查。
        auto_mode_switched (bool): 是否刚从自动模式切换过来。
        manual_executed (bool): 是否已执行手动操作。
    """
    auto_mode_checked = False
    auto_mode_switched = False
    manual_executed = False

    def combat_manual_reset(self):
        """重置手动战斗执行状态。"""
        self.manual_executed = False

    @contextmanager
    def combat_joystick_drag(self, auto):
        """为向右拖动模式保持单个触点，结算或异常离开时自动松手。"""
        if auto != 'drag_right':
            yield None
            return
        # ADB 与 Hermit 只提供整段手势，持续按住时借用本机的 MaaTouch。
        method = 'MaaTouch' if self.device.config.Emulator_ControlMethod in ('ADB', 'Hermit') else None
        with self.device.live_drag('COMBAT_JOYSTICK_RIGHT', method=method, release_on_click=True) as drag:
            yield drag

    def handle_combat_drag_right(self, drag):
        """识别手操摇杆后水平拖到右侧，等待结算或其他界面操作释放。"""
        if drag.active:
            return False
        button = self.combat_joystick_button(offset=SCUTTLE_JOYSTICK_OFFSET)
        if button is None:
            return False
        x1, y1, x2, y2 = button.button
        x, y = (x1 + x2) // 2, (y1 + y2) // 2
        drag.down((x, y))
        drag.move((x + 100, y))
        return True

    def handle_combat_stand_still_in_the_middle(self, auto):
        """处理战斗中停留在画面中央的模式。

        Args:
            auto (str): 战斗模式配置。

        Returns:
            bool: 是否执行了移动操作。
        """
        if auto != 'stand_still_in_the_middle':
            return False
        # 从自动切换到手动时，舰队通常在中央，无需下移
        # 否则舰队会被移动到底部
        if self.auto_mode_switched:
            return False

        self.device.long_click(MOVE_DOWN, duration=0.8)
        return True

    def handle_combat_stand_still_bottom_left(self, auto):
        """处理战斗中隐藏到左下角的模式。

        Args:
            auto (str): 战斗模式配置。

        Returns:
            bool: 是否执行了移动操作。
        """
        if auto != 'hide_in_bottom_left':
            return False

        self.device.long_click(MOVE_LEFT_DOWN, duration=(3.5, 5.5))
        return True

    def handle_combat_stand_still_upper_left(self, auto):
        """处理战斗中隐藏到左上角的模式。

        Args:
            auto (str): 战斗模式配置。

        Returns:
            bool: 是否执行了移动操作。
        """
        if auto != 'hide_in_upper_left':
            return False

        self.device.long_click(MOVE_LEFT_UP, duration=(1.5, 3.5))
        return True

    def handle_combat_weapon_release(self):
        """处理手动战斗中的武器释放（航空支援和鱼雷）。

        Returns:
            bool: 是否点击释放了就绪的武器。
        """
        if self.appear_then_click(READY_AIR_RAID, interval=10):
            return True
        if self.appear_then_click(READY_TORPEDO, interval=10):
            return True

        return False

    def handle_combat_manual(self, auto):
        """处理手动战斗模式下的位置控制。

        根据配置的战斗模式控制舰队移动到指定位置（停留中央、左下或左上）。

        Args:
            auto (str): 战斗模式配置。

        Returns:
            bool: 是否执行了手动操作。
        """
        if self.manual_executed or not self.auto_mode_checked:
            return False

        if self.handle_combat_stand_still_in_the_middle(auto):
            self.manual_executed = True
            return True
        if self.handle_combat_stand_still_bottom_left(auto):
            self.manual_executed = True
            return True
        if self.handle_combat_stand_still_upper_left(auto):
            self.manual_executed = True
            return True

        return False
