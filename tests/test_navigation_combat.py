"""导航接管遗留战斗的状态序列回归，不操作真实游戏。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from module.combat.navigation_combat import NavigationCombat
from module.ui.ui import UI
from module.os_handler.assets import AUTO_SEARCH_REWARD


class TestNavigationCombat(unittest.TestCase):
    def worker(self, frames):
        h = NavigationCombat.__new__(NavigationCombat)
        h.frame = "initial"

        def loop():
            for frame in frames:
                h.frame = frame
                yield None

        h.loop = loop
        h.is_in_stage = Mock(side_effect=lambda: h.frame == "stage")
        h._is_os_map = Mock(side_effect=lambda: h.frame == "os_map")
        h.is_in_map = Mock(side_effect=lambda: h.frame == "map")
        h.is_in_auto_search_menu = Mock(side_effect=lambda: h.frame == "menu")
        h.is_combat_executing = Mock(side_effect=lambda: h.frame == "combat")
        h.is_combat_loading = Mock(side_effect=lambda: h.frame == "loading")
        h.appear = Mock(return_value=False)
        h.appear_then_click = Mock(
            side_effect=lambda button, **kwargs: (
                button is AUTO_SEARCH_REWARD and h.frame == "os_reward"
            )
        )
        h.device = Mock()
        h.combat_auto_reset = Mock()
        h.handle_combat_auto = Mock(return_value=False)
        h.combat_result_reset = Mock()
        for name in (
            "handle_auto_search_exit",
            "combat_execute",
            "combat_status",
            "combat_preparation",
            "handle_get_ship",
            "handle_get_items",
            "handle_combat_automation_confirm",
            "handle_retirement",
            "handle_combat_low_emotion",
            "handle_story_skip",
            "handle_map_cat_attack",
            "handle_popup_confirm",
            "handle_exp_info",
        ):
            setattr(h, name, Mock(return_value=False))
        h.handle_battle_status = Mock(side_effect=lambda: h.frame == "result")
        h.handle_auto_search_map_option = Mock(side_effect=lambda: h.frame == "map")
        h.withdraw = Mock(side_effect=AssertionError("禁止撤离"))
        h.handle_auto_search_continue = Mock(side_effect=AssertionError("禁止开下一轮"))
        return h

    def test_unfinished_map_runs_auto_then_finishes_current_round(self):
        h = self.worker(["map", "loading", "combat", "result", "menu", "stage"])
        h.run()
        h.handle_auto_search_map_option.assert_called()
        h.combat_preparation.assert_called_once_with(
            balance_hp=False, emotion_reduce=False, auto="combat_auto", fleet_index=1
        )
        h.handle_combat_auto.assert_called_once_with("combat_auto")
        h.combat_execute.assert_not_called()
        h.handle_auto_search_exit.assert_called_once_with()
        h.withdraw.assert_not_called()
        h.handle_auto_search_continue.assert_not_called()

    def test_running_battle_returns_to_os_map_after_settlement(self):
        h = self.worker(["combat", "os_map"])
        h.run()
        h.handle_combat_auto.assert_called_once_with("combat_auto")
        h.combat_execute.assert_not_called()
        h.combat_status.assert_not_called()
        h.withdraw.assert_not_called()

    def test_battle_rewards_then_os_map_uses_one_state_loop(self):
        h = self.worker(
            ["combat", "combat", "os_reward", "os_reward", "os_map", "combat"]
        )
        h.run()
        self.assertEqual(h.frame, "os_map")
        self.assertEqual(h.handle_combat_auto.call_count, 2)
        h.combat_auto_reset.assert_called_once()
        self.assertEqual(h.appear_then_click.call_count, 5)
        h.combat_execute.assert_not_called()
        h.withdraw.assert_not_called()

    def test_real_os_reward_screenshot_matches_leave_button(self):
        import cv2

        image = cv2.cvtColor(
            cv2.imread("tests/fixtures/navigation_combat/os_total_rewards.png"),
            cv2.COLOR_BGR2RGB,
        )
        self.assertTrue(AUTO_SEARCH_REWARD.match(image, offset=(50, 50)))

    def test_rewards_alone_are_recognized_as_pending(self):
        h = self.worker([])
        h.frame = "os_reward"
        h.combat_result_observe = Mock(return_value=False)
        h.appear = Mock(
            side_effect=lambda button, **kwargs: button is AUTO_SEARCH_REWARD
        )
        self.assertTrue(h.has_pending_battle())
        h.loop = lambda: iter([None])
        h.run()
        h.appear_then_click.assert_called_with(
            AUTO_SEARCH_REWARD, offset=(50, 50), interval=3
        )

    def test_settlement_only_does_not_start_a_new_battle(self):
        h = self.worker(["result", "stage"])
        h.run()
        h.combat_execute.assert_not_called()
        h.handle_battle_status.assert_called_once()

    def test_navigation_guard_cleans_up_on_success_and_error(self):
        for error in (None, RuntimeError("测试错误")):
            ui = UI.__new__(UI)
            ui.config = SimpleNamespace()
            ui.device = Mock()
            worker = Mock()
            worker.has_pending_battle.return_value = True
            worker.run.side_effect = error
            with patch(
                "module.combat.navigation_combat.NavigationCombat", return_value=worker
            ):
                if error:
                    with self.assertRaises(RuntimeError):
                        ui.handle_navigation_battle()
                else:
                    self.assertTrue(ui.handle_navigation_battle())
            self.assertFalse(hasattr(ui.config, "_navigation_battle_active"))

    def test_active_handoff_does_not_recursively_start_another_worker(self):
        ui = UI.__new__(UI)
        ui.config = SimpleNamespace(_navigation_battle_active=True)
        with patch("module.combat.navigation_combat.NavigationCombat") as worker:
            self.assertFalse(ui.handle_navigation_battle())
            worker.assert_not_called()

    def test_no_pending_battle_leaves_navigation_unchanged(self):
        ui = UI.__new__(UI)
        ui.config = SimpleNamespace()
        ui.device = Mock()
        worker = Mock()
        worker.has_pending_battle.return_value = False
        with patch(
            "module.combat.navigation_combat.NavigationCombat", return_value=worker
        ):
            self.assertFalse(ui.handle_navigation_battle())
        worker.run.assert_not_called()
