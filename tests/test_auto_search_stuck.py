"""用虚拟时钟回放自律停在地图上的故障，不连接模拟器。"""

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from module.combat.auto_search_combat import AutoSearchCombat
from module.exception import CampaignEnd, GameStuckError
from module.handler.assets import AUTO_SEARCH_MAP_OPTION_OFF, AUTO_SEARCH_MAP_OPTION_ON
from module.map.assets import FLEET_NUM_2
from tests.test_auto_search_defeat import ScreenReplay, screen


class MovingReplay(ScreenReplay):
    """每帧指定采样时间，检查 30 秒超时及两次点击间隔。"""

    def __init__(self, frames):
        self.times = [time for time, _ in frames]
        self.click_times = []
        super().__init__([frame for _, frame in frames])
        self.time = self.times[0]

    def screenshot(self):
        self.index += 1
        if self.index >= len(self.frames):
            raise AssertionError(f'自律等待未在夹具内结束，已点击：{self.clicks}')
        self.time = self.times[self.index]
        self.device.image = self.frames[self.index]

    def click(self, button):
        super().click(button)
        self.click_times.append(self.time)

    def make_campaign(self):
        campaign = super().make_campaign(AutoSearchCombat)
        campaign.auto_search_watch_fleet = Mock(return_value=True)
        campaign.auto_search_watch_oil = Mock(return_value=True)
        campaign.auto_search_watch_coin = Mock(return_value=True)
        campaign.handle_map_cat_attack = Mock(return_value=False)
        campaign.match_template_color = Mock(side_effect=self.appear)
        return campaign

    def run(self, method='auto_search_moving', **kwargs):
        with patch('module.base.timer.time', side_effect=lambda: self.time):
            return getattr(self.campaign, method)(**kwargs)


ON = screen(AUTO_SEARCH_MAP_OPTION_ON)
OFF = screen(AUTO_SEARCH_MAP_OPTION_OFF)


class AutoSearchStuckTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.combat.auto_search_combat.logger'))

    def test_waits_thirty_seconds_then_restarts_with_half_second_gap(self):
        replay = MovingReplay([
            (100, ON), (129.99, ON), (130.01, ON),
            (130.3, OFF), (130.52, OFF), (130.8, ON), (131, screen('LOADING')),
        ])
        replay.make_campaign()
        replay.run()
        self.assertEqual([AUTO_SEARCH_MAP_OPTION_ON.name, AUTO_SEARCH_MAP_OPTION_OFF.name], replay.clicks)
        self.assertEqual([130.01, 130.52], replay.click_times)
        replay.device.sleep.assert_not_called()

    def test_retirement_reentry_keeps_original_three_second_retry(self):
        replay = MovingReplay([
            (100, OFF), (100.1, ON), (102.99, ON), (103.01, ON),
            (103.2, OFF), (104, screen('LOADING')),
        ])
        campaign = replay.make_campaign()
        campaign.handle_auto_search_map_wait = Mock(side_effect=AssertionError('退役流程不得接入普通自律恢复'))
        campaign._auto_search_wait_reset = Mock(side_effect=AssertionError('退役流程不得重置普通自律计时'))
        replay.run(method='map_offensive_auto_search')
        self.assertEqual([AUTO_SEARCH_MAP_OPTION_OFF.name, AUTO_SEARCH_MAP_OPTION_ON.name,
                          AUTO_SEARCH_MAP_OPTION_OFF.name], replay.clicks)
        self.assertEqual([100, 103.01, 103.2], replay.click_times)
        campaign.handle_auto_search_map_wait.assert_not_called()
        campaign._auto_search_wait_reset.assert_not_called()

    def test_retirement_during_moving_uses_original_reentry_without_new_recovery(self):
        replay = MovingReplay([(100, ON)])
        campaign = replay.make_campaign()
        campaign.handle_retirement = Mock(return_value=True)
        campaign.map_offensive_auto_search = Mock()
        campaign.handle_auto_search_map_wait = Mock()
        replay.run()
        campaign.map_offensive_auto_search.assert_called_once_with()
        campaign.handle_auto_search_map_wait.assert_not_called()

    def test_battle_transition_has_priority_over_timeout_and_stale_map_button(self):
        for transition in ('LOADING', 'COMBAT'):
            with self.subTest(transition=transition):
                replay = MovingReplay([(100, ON), (130.01, screen(transition, AUTO_SEARCH_MAP_OPTION_ON))])
                replay.make_campaign()
                replay.run()
                self.assertEqual([], replay.clicks)

    def test_battle_start_during_restart_delay_cancels_second_click(self):
        for transition in ('LOADING', 'COMBAT'):
            with self.subTest(transition=transition):
                replay = MovingReplay([
                    (100, ON), (130.01, ON),
                    (130.2, screen(transition, AUTO_SEARCH_MAP_OPTION_OFF)),
                ])
                replay.make_campaign()
                replay.run()
                self.assertEqual([AUTO_SEARCH_MAP_OPTION_ON.name], replay.clicks)

    def test_fast_loading_still_updates_visible_fleet_before_returning(self):
        replay = MovingReplay([(100, screen('LOADING', AUTO_SEARCH_MAP_OPTION_ON, FLEET_NUM_2))])
        campaign = replay.make_campaign()
        campaign.fleet_show_index = 1
        campaign.lv_get = Mock()
        campaign.auto_search_watch_fleet = AutoSearchCombat.auto_search_watch_fleet.__get__(campaign)
        replay.run()
        self.assertEqual(2, campaign.fleet_show_index)
        self.assertEqual([], replay.clicks)

    def test_second_click_waits_for_confirmed_off_state(self):
        replay = MovingReplay([
            (100, ON), (130.01, ON), (130.52, ON), (131, screen()),
            (131.2, OFF), (132, screen('LOADING')),
        ])
        replay.make_campaign()
        replay.run()
        self.assertEqual([130.01, 131.2], replay.click_times)
        self.assertEqual([AUTO_SEARCH_MAP_OPTION_ON.name, AUTO_SEARCH_MAP_OPTION_OFF.name], replay.clicks)

    def test_failed_close_keeps_existing_error_recovery_available(self):
        replay = MovingReplay([(100, ON), (130.01, ON), (130.52, ON), (160.02, ON)])
        replay.make_campaign()
        with self.assertRaises(GameStuckError):
            replay.run()
        self.assertEqual([AUTO_SEARCH_MAP_OPTION_ON.name], replay.clicks)

    def test_no_battle_after_restart_raises_instead_of_toggling_forever(self):
        replay = MovingReplay([
            (100, ON), (130.01, ON), (130.52, OFF), (130.8, ON), (160.53, ON),
        ])
        replay.make_campaign()
        with self.assertRaises(GameStuckError):
            replay.run()
        self.assertEqual([AUTO_SEARCH_MAP_OPTION_ON.name, AUTO_SEARCH_MAP_OPTION_OFF.name], replay.clicks)

    def test_end_menu_during_delay_does_not_reenable_auto_search(self):
        replay = MovingReplay([(100, ON), (130.01, ON), (130.52, screen('STAGE'))])
        campaign = replay.make_campaign()
        campaign._handle_auto_search_menu_missing = Mock(side_effect=lambda: campaign.is_in_stage())
        with self.assertRaises(CampaignEnd):
            replay.run()
        self.assertEqual([AUTO_SEARCH_MAP_OPTION_ON.name], replay.clicks)

    def test_popups_are_handled_before_map_recovery(self):
        for handler in ('handle_combat_low_emotion', 'handle_story_skip', 'handle_vote_popup'):
            with self.subTest(handler=handler):
                replay = MovingReplay([
                    (100, ON), (130.01, screen('POPUP', AUTO_SEARCH_MAP_OPTION_ON)),
                    (130.2, screen('LOADING')),
                ])
                campaign = replay.make_campaign()
                setattr(campaign, handler, Mock(side_effect=lambda: 'POPUP' in campaign.device.image))
                replay.run()
                self.assertEqual([], replay.clicks)

    def test_restart_resume_guard_allows_map_stuck_recovery(self):
        replay = MovingReplay([
            (100, ON), (130.01, ON), (130.3, OFF), (130.52, OFF), (131, screen('LOADING')),
        ])
        campaign = replay.make_campaign()
        with patch('module.base.timer.time', side_effect=lambda: replay.time):
            self.assertTrue(campaign.prepare_auto_search_recovery('map'))
        replay.run()
        self.assertEqual([AUTO_SEARCH_MAP_OPTION_ON.name, AUTO_SEARCH_MAP_OPTION_OFF.name], replay.clicks)
        campaign.emotion.reduce.assert_not_called()

    def test_device_errors_still_propagate(self):
        replay = MovingReplay([(100, ON), (101, ON)])
        replay.make_campaign()
        replay.device.screenshot.side_effect = GameStuckError('设备原有保护')
        with self.assertRaisesRegex(GameStuckError, '设备原有保护'):
            replay.run()


class AutoSearchSwitchRecognitionTests(unittest.TestCase):
    def test_real_map_switch_states_require_both_template_and_color(self):
        fixtures = Path(__file__).parent / 'fixtures'
        for path, expected in (
            (fixtures / 'auto_search_stuck' / 'map_on.png', AUTO_SEARCH_MAP_OPTION_ON),
            (fixtures / 'combat_defeat' / 'map_fleet2.png', AUTO_SEARCH_MAP_OPTION_OFF),
        ):
            with self.subTest(path=path.name):
                image = np.array(Image.open(path).convert('RGB'))
                self.assertEqual((720, 1280, 3), image.shape)
                for button in (AUTO_SEARCH_MAP_OPTION_ON, AUTO_SEARCH_MAP_OPTION_OFF):
                    self.assertEqual(button is expected,
                                     button.match_template_color(image, offset=(5, 5), threshold=10))


if __name__ == '__main__':
    unittest.main()
