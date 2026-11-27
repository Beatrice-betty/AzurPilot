"""沉船手操、结算与潜艇召唤的离线战斗回放。"""

import unittest
from unittest.mock import MagicMock, Mock, patch

from module.base.timer import Timer
from module.coalition.assets import SCUTTLE_CONFIRM
from module.coalition.coalition_scuttle import CoalitionScuttleRun
from module.combat.assets import (
    AUTOMATION_OFF, AUTOMATION_ON, AUTOMATION_SWITCH, BATTLE_PREPARATION,
    COMBAT_AUTO, COMBAT_AUTO_SWITCH, OPTS_INFO_D, SUBMARINE_AVAILABLE_CHECK_1,
    SUBMARINE_AVAILABLE_CHECK_2, SUBMARINE_READY,
)
from module.device.control import Control
from module.raid.scuttle import RaidScuttleRun
from tests.test_auto_search_defeat import ScreenReplay, screen


class ScuttleReplay(ScreenReplay):
    def __init__(self, cls, mode, frames, summon=False):
        tail = [screen(OPTS_INFO_D)]
        if cls is CoalitionScuttleRun:
            tail.append(screen(SCUTTLE_CONFIRM))
        super().__init__([*frames, *tail, screen('END')])
        self.events = []
        task = self.make_campaign(cls)
        task.config.Fleet_Fleet1Mode = mode
        task.config.Campaign_Event = 'coalition_20261124' if cls is CoalitionScuttleRun else 'raid_test'
        task.config.Coalition_Fleet = 'multi'
        task.config.Submarine_Fleet = 1 if summon else 0
        task.config.Submarine_Mode = 'do_not_use'
        task.config.Submarine_AutoSearchMode = 'sub_auto_call'
        task.config.Emulator_ControlMethod = 'MaaTouch'
        task.config.campaign_name = 'test_scuttle'
        task.config.DropRecord_CombatRecord = 'do_not'
        task.config.override = lambda **kwargs: vars(task.config).update(kwargs)
        task.config.temporary = Mock()
        task.stat = MagicMock()
        self.device.config = task.config
        self.device.click_methods = {'MaaTouch': Mock()}
        self.device.live_drag = Mock(side_effect=self.live_drag)

        del task.handle_combat_auto
        del task.handle_submarine_call
        task._automation_set_timer = Timer(1)
        task.auto_mode_click_timer = Timer(5)
        task.auto_skip_timer = Timer(1)
        task.auto_click_interval_timer = Timer(1)
        task.handle_combat_weapon_release = Mock()
        task.handle_battle_preparation = Mock(return_value=False)
        task.handle_raid_ticket_use = Mock(return_value=False)
        task.is_auto_search_running = Mock(return_value=False)
        task._handle_auto_search_menu_missing = Mock(return_value=False)
        task.handle_urgent_commission = Mock(return_value=False)
        task.triggered_stop_condition = Mock(return_value=False)
        task.enter_map = Mock()
        task.raid_enter = Mock()
        task.FUNCTION_NAME_BASE = 'battle_'
        task.in_coalition = lambda: 'END' in self.device.image
        task.raid_expected_end = lambda: 'END' in self.device.image

    def click(self, button):
        Control.click(self.device, button)
        super().click(button)
        self.events.append((button.name, self.index))

    def live_drag(self, name, **kwargs):
        drag = Control.live_drag(self.device, name, **kwargs)
        drag._send = Mock(side_effect=lambda operation, point: self.events.append((operation, self.index, point)))
        return drag

    def run(self):
        task = self.campaign
        with patch('module.base.timer.time', side_effect=lambda: self.time):
            if isinstance(task, CoalitionScuttleRun):
                task.coalition_execute_once(task.config.Campaign_Event, 'hard', 'multi')
            else:
                task.raid_execute_once('ex', task.config.Campaign_Event)


class ScuttleCombatControlTests(unittest.TestCase):
    def test_stand_still_turns_auto_off_and_skips_movement_and_weapons(self):
        for cls in (RaidScuttleRun, CoalitionScuttleRun):
            with self.subTest(task=cls.__name__):
                replay = ScuttleReplay(cls, 'stand_still', [
                    screen(BATTLE_PREPARATION, AUTOMATION_ON), screen(BATTLE_PREPARATION, AUTOMATION_OFF),
                    screen('COMBAT', COMBAT_AUTO), screen('COMBAT', COMBAT_AUTO), screen('COMBAT'),
                ])
                replay.run()
                self.assertEqual(replay.clicks.count(AUTOMATION_SWITCH.name), 1)
                self.assertNotIn(COMBAT_AUTO_SWITCH.name, replay.clicks)
                self.assertNotIn(SUBMARINE_READY.name, replay.clicks)
                self.assertIn(OPTS_INFO_D.name, replay.clicks)
                replay.device.live_drag.assert_not_called()
                replay.campaign.handle_combat_manual.assert_not_called()
                replay.campaign.handle_combat_weapon_release.assert_not_called()
                replay.campaign.config.temporary.assert_not_called()

    def test_scuttle_drag_holds_right_and_resumes_after_summoning_in_manual_battle(self):
        for cls in (RaidScuttleRun, CoalitionScuttleRun):
            with self.subTest(task=cls.__name__):
                replay = ScuttleReplay(cls, 'drag_right', [
                    screen(BATTLE_PREPARATION, AUTOMATION_OFF),
                    screen('COMBAT', COMBAT_AUTO), screen('COMBAT', COMBAT_AUTO), screen('COMBAT'),
                    screen('COMBAT', COMBAT_AUTO, SUBMARINE_AVAILABLE_CHECK_1, SUBMARINE_AVAILABLE_CHECK_2, SUBMARINE_READY),
                    screen('COMBAT', COMBAT_AUTO), screen('COMBAT'),
                ], summon=True)
                replay.run()
                battle_events = [event for event in replay.events if event[0] != BATTLE_PREPARATION.name]
                self.assertEqual([event[0] for event in battle_events[:8]], [
                    'down', 'move', 'up', SUBMARINE_READY.name, 'down', 'move', 'up', OPTS_INFO_D.name,
                ])
                x, y = battle_events[0][2]
                self.assertEqual(battle_events[1][2], (x + 100, y))
                self.assertGreater(battle_events[2][1], battle_events[1][1])
                self.assertNotIn(AUTOMATION_SWITCH.name, replay.clicks)
                self.assertNotIn(COMBAT_AUTO_SWITCH.name, replay.clicks)
                replay.campaign.handle_combat_weapon_release.assert_not_called()

    def test_scuttle_switches_auto_off_if_needed_in_battle(self):
        for cls in (RaidScuttleRun, CoalitionScuttleRun):
            with self.subTest(task=cls.__name__):
                replay = ScuttleReplay(cls, 'stand_still', [
                    screen(BATTLE_PREPARATION, AUTOMATION_OFF),
                    screen('COMBAT'), screen('COMBAT'), screen('COMBAT'), screen('COMBAT'),
                    screen('COMBAT', COMBAT_AUTO), screen('COMBAT'),
                ])
                replay.run()
                self.assertEqual(replay.clicks.count(COMBAT_AUTO_SWITCH.name), 1)
                self.assertNotIn(AUTOMATION_SWITCH.name, replay.clicks)
                replay.campaign.handle_combat_weapon_release.assert_not_called()
