"""活动 PT 达标时停止三油活动任务，保留用户关卡选择。"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from module.campaign.campaign_event import CampaignEvent
from module.campaign.gems_farming import GemsFarming
from module.config.utils import DEFAULT_TIME
from tests.test_farming_combat_config import make_config


class FarmingPtLimitTests(unittest.TestCase):
    def make_campaign(self, command='ThreeOilLowCost', stage='C2', limit=100000, pt=100000):
        config = make_config(command)
        config.data['ThreeOilLowCost']['Campaign'].update(Name=stage, Event='event_20260908_cn')
        config.data['ThreeOilLowCost']['Scheduler']['Enable'] = True
        config.data['GemsFarming']['Campaign'].update(Name='D3', Event='event_20260908_cn')
        config.data['GemsFarming']['Scheduler']['Enable'] = True
        config.override(
            Campaign_Name=stage if command == 'ThreeOilLowCost' else 'D3',
            EventGeneral_PtLimit=limit,
        )
        config.modified.clear()
        campaign = CampaignEvent(config=config, device=Mock())
        campaign.get_event_pt = Mock(return_value=pt)
        return campaign

    def test_pt_limit_disables_three_oil_event_without_rewriting_stage(self):
        for command in ('ThreeOilLowCost', 'Event', 'GemsFarming'):
            for pt in (100000, 100120):
                with self.subTest(command=command, pt=pt):
                    campaign = self.make_campaign(command=command, pt=pt)
                    self.assertTrue(campaign.event_pt_limit_triggered())
                    changes = campaign.config.modified
                    self.assertIs(changes.get('ThreeOilLowCost.Scheduler.Enable'), False)
                    self.assertNotIn('ThreeOilLowCost.Campaign.Name', changes)
                    self.assertNotIn('ThreeOilLowCost.Campaign.Event', changes)
                    self.assertEqual(campaign.config.cross_get('ThreeOilLowCost.Campaign.Name'), 'C2')
                    self.assertIs(changes['Event.Scheduler.Enable'], False)
                    self.assertEqual(changes['EventGeneral.EventGeneral.TimeLimit'], DEFAULT_TIME)

    def test_other_event_reaching_limit_preserves_main_three_oil(self):
        for stage in ('2-4', 'campaign_2_4', ' 7_2 '):
            with self.subTest(stage=stage):
                campaign = self.make_campaign(command='Event', stage=stage)
                self.assertTrue(campaign.event_pt_limit_triggered())
                self.assertFalse(any(key.startswith('ThreeOilLowCost.') for key in campaign.config.modified))

    def test_three_oil_runner_returns_without_starting_another_battle(self):
        campaign = self.make_campaign()
        campaign.config.override(EventGeneral_TimeLimit=DEFAULT_TIME)
        campaign.auto_search_oil_limit_triggered = False
        campaign.is_in_map = Mock(return_value=False)
        campaign.is_in_auto_search_menu = Mock(return_value=False)
        campaign.ensure_campaign_ui = Mock()
        campaign.ensure_auto_search_exit = Mock()
        campaign.run = Mock()
        runner = GemsFarming(config=campaign.config, device=campaign.device)
        runner.campaign = campaign
        runner.stage = 'c2'
        runner.load_campaign = Mock()
        runner.ui_page_appear = Mock(return_value=False)
        runner.disable_raid_on_event = Mock()
        runner.handle_commission_notice = Mock()
        runner.status_get_gems = Mock()
        runner.get_coin = Mock(return_value=10000)
        runner.get_oil = Mock(return_value=10000)
        with patch.object(GemsFarming, '_initial_flagship_check_done', True):
            runner.run('C2', folder='event_20260908_cn')
        campaign.run.assert_not_called()
        campaign.ensure_auto_search_exit.assert_called_once_with()
        runner.load_campaign.assert_called_once_with('c2', folder='event_20260908_cn')
        self.assertIs(campaign.config.modified.get('ThreeOilLowCost.Scheduler.Enable'), False)

    def test_main_three_oil_does_not_read_event_pt(self):
        campaign = self.make_campaign(stage='2-4')
        self.assertFalse(campaign.event_pt_limit_triggered())
        campaign.get_event_pt.assert_not_called()
        self.assertEqual(campaign.config.modified, {})

    def test_below_limit_and_disabled_limit_leave_tasks_unchanged(self):
        for limit, pt in ((100000, 99999), (0, 100000)):
            with self.subTest(limit=limit, pt=pt):
                campaign = self.make_campaign(limit=limit, pt=pt)
                self.assertFalse(campaign.event_pt_limit_triggered())
                self.assertEqual(campaign.config.modified, {})

    def test_gems_farming_retains_its_existing_fallback(self):
        campaign = self.make_campaign()
        self.assertTrue(campaign.event_pt_limit_triggered())
        changes = campaign.config.modified
        self.assertEqual(changes['GemsFarming.Campaign.Name'], '2-4')
        self.assertEqual(changes['GemsFarming.Campaign.Event'], 'campaign_main')
        self.assertNotIn('GemsFarming.Scheduler.Enable', changes)

    def test_time_limit_keeps_existing_event_expiration_policy(self):
        campaign = self.make_campaign()
        now = datetime(2026, 9, 20, 12)
        campaign.config.override(EventGeneral_TimeLimit=now - timedelta(seconds=1))
        with patch('module.campaign.campaign_event.current_time', return_value=now):
            self.assertTrue(campaign.event_time_limit_triggered())
        changes = campaign.config.modified
        self.assertEqual(changes['ThreeOilLowCost.Campaign.Name'], '2-4')
        self.assertEqual(changes['ThreeOilLowCost.Campaign.Event'], 'campaign_main')
        self.assertNotIn('ThreeOilLowCost.Scheduler.Enable', changes)


if __name__ == '__main__':
    unittest.main()
