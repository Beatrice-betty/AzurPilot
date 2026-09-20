"""活动任务收尾时保留用户关卡，禁止低耗活动任务回退主线。"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from module.campaign.campaign_event import CampaignEvent
from module.campaign.gems_farming import GemsFarming
from module.config.config import TaskEnd
from module.config.utils import DEFAULT_TIME
from tests.test_farming_combat_config import make_config


class FarmingEventStopTests(unittest.TestCase):
    def make_campaign(self, command='ThreeOilLowCost', stage='C2', limit=100000, pt=100000, gems_stage='D3'):
        config = make_config(command)
        config.data['ThreeOilLowCost']['Campaign'].update(Name=stage, Event='event_20260908_cn')
        config.data['ThreeOilLowCost']['Scheduler']['Enable'] = True
        config.data['GemsFarming']['Campaign'].update(Name=gems_stage, Event='event_20260908_cn')
        config.data['GemsFarming']['Scheduler']['Enable'] = True
        config.override(
            Campaign_Name=stage if command == 'ThreeOilLowCost' else gems_stage,
            EventGeneral_PtLimit=limit,
        )
        config.modified.clear()
        campaign = CampaignEvent(config=config, device=Mock())
        campaign.get_event_pt = Mock(return_value=pt)
        return campaign

    def assert_farming_stopped_without_stage_change(self, campaign):
        changes = campaign.config.modified
        for task in ('ThreeOilLowCost', 'GemsFarming'):
            self.assertIs(changes.get(f'{task}.Scheduler.Enable'), False)
            self.assertNotIn(f'{task}.Campaign.Name', changes)
            self.assertNotIn(f'{task}.Campaign.Event', changes)

    def test_pt_limit_disables_three_oil_event_without_rewriting_stage(self):
        for command in ('ThreeOilLowCost', 'Event', 'GemsFarming'):
            for pt in (100000, 100120):
                with self.subTest(command=command, pt=pt):
                    campaign = self.make_campaign(command=command, pt=pt)
                    self.assertTrue(campaign.event_pt_limit_triggered())
                    changes = campaign.config.modified
                    self.assert_farming_stopped_without_stage_change(campaign)
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

    def test_gems_farming_event_also_stops_at_pt_limit(self):
        campaign = self.make_campaign()
        self.assertTrue(campaign.event_pt_limit_triggered())
        self.assert_farming_stopped_without_stage_change(campaign)

    def test_time_limit_stops_farming_without_rewriting_stage(self):
        campaign = self.make_campaign()
        now = datetime(2026, 9, 20, 12)
        campaign.config.override(EventGeneral_TimeLimit=now - timedelta(seconds=1))
        with patch('module.campaign.campaign_event.current_time', return_value=now):
            self.assertTrue(campaign.event_time_limit_triggered())
        self.assert_farming_stopped_without_stage_change(campaign)

    def test_missing_event_entrance_stops_without_rewriting_stage(self):
        campaign = self.make_campaign()
        campaign.appear = Mock(return_value=True)
        campaign.config.task_stop = Mock(side_effect=TaskEnd)
        with self.assertRaises(TaskEnd):
            campaign.is_event_entrance_available()
        self.assert_farming_stopped_without_stage_change(campaign)
        campaign.config.task_stop.assert_called_once_with()

    def test_new_activity_stops_old_farming_without_rewriting_stage(self):
        for command in ('Raid', 'Coalition', 'MaritimeEscort'):
            for other_event_enabled in (False, True):
                with self.subTest(command=command, other_event_enabled=other_event_enabled):
                    campaign = self.make_campaign(command=command)
                    enabled = {'ThreeOilLowCost', 'GemsFarming'}
                    if other_event_enabled:
                        enabled.add('Event')
                    campaign.config.is_task_enabled = Mock(side_effect=lambda task: task in enabled)
                    self.assertTrue(campaign.disable_event_on_raid())
                    self.assert_farming_stopped_without_stage_change(campaign)

    def test_all_event_cleanup_paths_leave_main_farming_unchanged(self):
        for stage in ('2-4', 'campaign_2_4', ' 7_2 '):
            for trigger in ('pt', 'time', 'entrance', 'activity'):
                with self.subTest(stage=stage, trigger=trigger):
                    campaign = self.make_campaign(command='Raid', stage=stage, gems_stage=stage)
                    if trigger == 'pt':
                        self.assertTrue(campaign.event_pt_limit_triggered())
                    elif trigger == 'time':
                        now = datetime(2026, 9, 20, 12)
                        campaign.config.override(EventGeneral_TimeLimit=now - timedelta(seconds=1))
                        with patch('module.campaign.campaign_event.current_time', return_value=now):
                            self.assertTrue(campaign.event_time_limit_triggered())
                    elif trigger == 'entrance':
                        campaign.appear = Mock(return_value=True)
                        campaign.config.task_stop = Mock(side_effect=TaskEnd)
                        with self.assertRaises(TaskEnd):
                            campaign.is_event_entrance_available()
                    else:
                        campaign.config.is_task_enabled = Mock(
                            side_effect=lambda task: task in ('ThreeOilLowCost', 'GemsFarming'))
                        self.assertFalse(campaign.disable_event_on_raid())
                        self.assertEqual(campaign.config.modified, {})
                    for task in ('ThreeOilLowCost', 'GemsFarming'):
                        self.assertFalse(any(key.startswith(f'{task}.') for key in campaign.config.modified))


if __name__ == '__main__':
    unittest.main()
