"""导航遇到未完成战斗时，接管自律并完成当前轮次后继续导航。"""

from module.combat.auto_search_combat import AutoSearchCombat
from module.combat.assets import BATTLE_PREPARATION
from module.handler.assets import AUTO_SEARCH_MAP_OPTION_ON
from module.map.assets import WITHDRAW
from module.os_handler.assets import AUTO_SEARCH_REWARD
from module.os_handler.enemy_searching import (
    EnemySearchingHandler as OSEnemySearchingHandler,
)
from module.logger import logger


class NavigationCombat(AutoSearchCombat):
    """不依赖当前任务的关卡地图定义，复用战斗及结算状态处理器。"""

    def has_pending_battle(self):
        return bool(
            self.appear(WITHDRAW, offset=(30, 30))
            or self.appear(AUTO_SEARCH_MAP_OPTION_ON, offset=self._auto_search_offset)
            or self.is_combat_executing()
            or self.is_combat_loading()
            or self.appear(BATTLE_PREPARATION, offset=(30, 20))
            or self.combat_result_observe()
            or self.appear(AUTO_SEARCH_REWARD, offset=(50, 50))
        )

    def _is_os_map(self):
        return OSEnemySearchingHandler.is_in_map(self)

    def run(self):
        """完成遗留关卡或单场战斗，关卡完成后退出结算菜单，不开启下一轮。

        Pages:
            in: 未完成关卡地图、战斗准备、加载、战斗或战斗结算。
            out: 关卡选择页，或已经完成当前战斗的大世界地图。
        """
        logger.hr("[导航-战斗接管] 先完成当前战斗，再继续原任务")
        self.battle_status_click_interval = 2
        self.combat_result_reset(emotion_reduce=False, fleet_index=1)
        combat_active = False
        for _ in self.loop():
            # 大世界自动结算可能越过普通战斗结果页，必须在同一个状态循环处理。
            if self.appear_then_click(AUTO_SEARCH_REWARD, offset=(50, 50), interval=3):
                logger.info("[导航-战斗接管] 关闭大世界合计奖励，等待返回地图")
                continue
            if self.is_in_stage() or self._is_os_map():
                self.device.screenshot_interval_set()
                logger.info("[导航-战斗接管] 当前战斗及结算完成，恢复导航")
                return
            if self.is_in_auto_search_menu():
                self.handle_auto_search_exit()
                continue
            if self.is_combat_executing():
                if not combat_active:
                    self.combat_auto_reset()
                    self.device.stuck_record_clear()
                    self.device.click_record_clear()
                    combat_active = True
                if self.handle_combat_automation_confirm():
                    continue
                self.handle_combat_auto("combat_auto")
                continue
            combat_active = False
            if self.is_combat_loading() or self.appear(
                BATTLE_PREPARATION, offset=(30, 20)
            ):
                self.combat_preparation(
                    balance_hp=False,
                    emotion_reduce=False,
                    auto="combat_auto",
                    fleet_index=1,
                )
                continue
            if self.handle_battle_status() or self.handle_exp_info():
                continue
            if self.handle_get_ship() or self.handle_get_items():
                continue
            if self.handle_combat_automation_confirm():
                continue
            if self.handle_auto_search_map_option():
                continue
            if self.handle_retirement() or self.handle_combat_low_emotion():
                continue
            if self.handle_story_skip() or self.handle_map_cat_attack():
                continue
            if self.handle_popup_confirm("NAVIGATION_COMBAT"):
                continue
