"""月度补扫编排回归；设备交互全部使用桩，不访问游戏或真实用户配置。"""
import json
import tempfile
import unittest
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.config.config import AzurLaneConfig, TaskEnd
from module.exception import GameStuckError, MapDetectionError, RequestHumanTakeover
from module.os.map import OSMap
from module.os.tasks.explore import OpsiExplore
from module.os.globe_operation import OSExploreError


RESET = datetime(2026, 11, 1)


class CleanupTests(unittest.TestCase):
    def runner(self, state=None, enabled=True):
        runner = OpsiExplore.__new__(OpsiExplore)
        runner.config = SimpleNamespace(
            OpsiExplore_MeowfficerCleanup=enabled,
            OpsiExplore_MeowfficerCleanupState=state,
            OpsiExplore_LastZone=33,
            OpsiExplore_ExploreProgress='已完成百分之50.00',
            OpsiFleet_Fleet=1, OS_EXPLORE_FILTER='44 > 24 > 22',
            multi_set=nullcontext, check_task_switch=Mock(),
            task_delay=Mock(), task_call=Mock(), task_stop=Mock(side_effect=TaskEnd),
        )
        runner.calls = []
        runner.name_to_zone = lambda zone: SimpleNamespace(zone_id=zone)
        runner.globe_focus_to = Mock()
        runner.globe_update = Mock()
        runner.os_map_goto_globe = Mock()
        runner.zone_has_safe = Mock(return_value=True)
        def enter(zone, **kwargs):
            self.assertEqual(kwargs, {'types': 'SAFE', 'require_safe': True})
            runner.calls.append(('enter', zone))
            runner.zone = SimpleNamespace(zone_id=zone)
        runner.globe_goto = enter
        runner.fleet_set = Mock()
        runner.fleet_selector = SimpleNamespace(get=lambda: 1)
        runner.map = SimpleNamespace(camera_data=[1, 2])
        runner.map_init = lambda **kw: runner.calls.append(('init', runner.zone.zone_id))
        runner.full_scan = lambda **kw: runner.calls.append(('full', runner.zone.zone_id))
        runner.map_rescan = lambda **kw: runner.calls.append(('rescan', runner.zone.zone_id)) or True
        runner.clear_question_any_fleet = lambda: runner.calls.append(('radar', runner.zone.zone_id))
        return runner

    def state(self, **kwargs):
        return dict(reset=RESET.isoformat(), phase='cleanup', order=[44, 24, 22], next=0, attempts=0, **kwargs)

    @patch('module.os.tasks.explore.get_os_next_reset', return_value=RESET)
    def test_confirmation_precedes_scanning_and_preserves_order(self, reset):
        runner = self.runner()
        with self.assertRaises(TaskEnd):
            runner._os_explore_end()
        self.assertEqual([call.args[0].zone_id for call in runner.globe_focus_to.call_args_list], [44, 24, 22])
        self.assertEqual(runner.calls, [(stage, zone) for zone in [44, 24, 22] for stage in ['enter', 'init', 'full', 'rescan', 'radar']])
        self.assertEqual(runner.config.OpsiExplore_MeowfficerCleanupState['phase'], 'done')
        self.assertFalse(runner._opsi_meowfficer_cleanup)
        runner.config.task_delay.assert_called_once_with(target=RESET)

    @patch('module.os.tasks.explore.get_os_next_reset', return_value=RESET)
    def test_unsafe_zone_never_triggers_cleanup(self, reset):
        runner = self.runner()
        runner.zone_has_safe.side_effect = [True, False]
        with self.assertRaises(OSExploreError):
            runner._os_explore_end()
        self.assertEqual(runner.calls, [])
        self.assertIsNone(runner.config.OpsiExplore_MeowfficerCleanupState)
        runner.config.task_delay.assert_not_called()

    @patch('module.os.tasks.explore.get_os_next_reset', return_value=RESET)
    def test_restart_resumes_only_unfinished_zone(self, reset):
        runner = self.runner(self.state())
        def fail_second(zone, **kwargs):
            runner.calls.append(('enter', zone))
            if zone == 24:
                raise MapDetectionError('识别失败')
            runner.zone = SimpleNamespace(zone_id=zone)
        runner.globe_goto = fail_second
        with self.assertRaises(MapDetectionError):
            runner._os_explore_meowfficer_cleanup()
        stored = json.loads(json.dumps(runner.config.OpsiExplore_MeowfficerCleanupState))
        self.assertEqual(stored['next'], 1)
        self.assertEqual(stored['attempts'], 1)
        self.assertFalse(runner._opsi_meowfficer_cleanup)
        restarted = self.runner(stored)
        with self.assertRaises(TaskEnd):
            restarted.os_explore()
        self.assertEqual([zone for stage, zone in restarted.calls if stage == 'enter'], [24, 22])

    @patch('module.os.tasks.explore.get_os_next_reset', return_value=RESET)
    def test_done_month_is_not_repeated(self, reset):
        state = self.state()
        state['phase'] = 'done'
        runner = self.runner(state)
        with self.assertRaises(TaskEnd):
            runner.os_explore()
        self.assertEqual(runner.calls, [])

    @patch('module.os.tasks.explore.get_os_next_reset', return_value=RESET)
    def test_three_failed_attempts_stop_without_another_entry(self, reset):
        runner = self.runner(self.state())
        runner.globe_goto = Mock(side_effect=GameStuckError('无法进入'))
        for _ in range(3):
            with self.assertRaises(GameStuckError):
                runner._os_explore_meowfficer_cleanup()
        with self.assertRaises(RequestHumanTakeover):
            runner._os_explore_meowfficer_cleanup()
        self.assertEqual(runner.globe_goto.call_count, 3)
        self.assertEqual(runner.config.OpsiExplore_MeowfficerCleanupState['next'], 0)

    @patch('module.os.tasks.explore.get_os_next_reset', return_value=datetime(2026, 12, 1))
    def test_new_month_resets_checkpoint_before_normal_exploration(self, reset):
        runner = self.runner(self.state())
        runner._os_explore = Mock(side_effect=TaskEnd)
        with self.assertRaises(TaskEnd):
            runner.os_explore()
        self.assertEqual(runner.config.OpsiExplore_LastZone, 0)
        self.assertEqual(runner.config.OpsiExplore_MeowfficerCleanupState, {'reset': datetime(2026, 12, 1).isoformat(), 'phase': 'explore'})
        runner._os_explore.assert_called_once()
        self.assertEqual(runner.calls, [])

    @patch('module.os.tasks.explore.get_os_next_reset', return_value=RESET)
    def test_disabled_switch_keeps_existing_finish_flow(self, reset):
        runner = self.runner(enabled=False)
        with self.assertRaises(TaskEnd):
            runner._os_explore_end()
        runner.globe_update.assert_not_called()
        self.assertEqual(runner.calls, [])

    @patch('module.os.tasks.explore.get_os_next_reset', side_effect=[RESET, RESET, datetime(2026, 12, 1)])
    def test_cross_month_never_enters_old_cleanup_zone(self, reset):
        runner = self.runner(self.state())
        with self.assertRaises(GameStuckError):
            runner._os_explore_meowfficer_cleanup()
        self.assertEqual(runner.calls, [])

    def test_cleanup_scan_ignores_battle_producing_events(self):
        runner = SimpleNamespace(_opsi_meowfficer_cleanup=True, _solved_map_event=set())
        runner.view = SimpleNamespace(select=Mock(return_value=[]))
        self.assertFalse(OSMap.map_rescan_current(runner))
        self.assertEqual(runner.view.select.call_args_list, [unittest.mock.call(is_akashi=True)])

    def test_cleanup_radar_errors_are_not_swallowed(self):
        runner = SimpleNamespace(_opsi_meowfficer_cleanup=True, config=SimpleNamespace(OpsiFleet_Fleet=1))
        runner.fleet_set = Mock(side_effect=GameStuckError('换队失败'))
        with self.assertRaises(GameStuckError):
            OSMap.clear_question_any_fleet(runner)
        runner.fleet_set.assert_called_once_with(1)

    @patch('module.os.tasks.explore.get_os_next_reset', return_value=RESET)
    def test_checkpoint_is_saved_and_loaded_by_real_configuration(self, reset):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cleanup-test.json'
            path.write_text(Path('config/template.json').read_text(encoding='utf-8'), encoding='utf-8')
            with patch('module.config.config.filepath_config', return_value=str(path)), \
                    patch('module.config.config_updater.filepath_config', return_value=str(path)):
                config = AzurLaneConfig('cleanup-test', task='OpsiExplore')
                state = self.state()
                state.update(next=2, attempts=1)
                config.OpsiExplore_MeowfficerCleanupState = state
                restarted = AzurLaneConfig('cleanup-test', task='OpsiExplore')
                self.assertEqual(restarted.OpsiExplore_MeowfficerCleanupState, state)
                runner = self.runner(restarted.OpsiExplore_MeowfficerCleanupState)
                runner._os_explore_meowfficer_cleanup()
                self.assertEqual([zone for stage, zone in runner.calls if stage == 'enter'], [22])

    @patch('module.os.tasks.explore.get_os_next_reset', return_value=RESET)
    def test_initialization_skips_battles_for_pending_cleanup(self, reset):
        self.assert_initialization_skips_battles(self.state())

    @patch('module.os.tasks.explore.get_os_next_reset', return_value=RESET)
    def test_late_enabled_cleanup_skips_initial_battles_until_confirmation(self, reset):
        self.assert_initialization_skips_battles(None, progress='已完成百分之100.00')

    def assert_initialization_skips_battles(self, state, progress='已完成百分之50.00'):
        runner = self.runner(state)
        runner.config.OpsiExplore_ExploreProgress = progress
        runner.config.task = SimpleNamespace(command='OpsiExplore')
        runner.config.override = Mock()
        runner.config.cross_get = Mock(return_value=22)
        runner.is_in_map = lambda: True
        runner.is_in_special_zone = lambda: False
        runner.zone = SimpleNamespace(zone_id=44)
        runner.zone_init = Mock()
        runner.hp_reset = Mock()
        runner.handle_after_auto_search = Mock()
        runner.handle_current_fleet_resolve = Mock()
        runner.run_first_auto_search = Mock()
        runner.os_init()
        runner.run_first_auto_search.assert_not_called()

    @patch('module.os.tasks.explore.get_os_next_reset', return_value=RESET)
    def test_exit_failure_does_not_advance_checkpoint(self, reset):
        runner = self.runner(self.state())
        runner.os_map_goto_globe.side_effect = GameStuckError('退出失败')
        with self.assertRaises(GameStuckError):
            runner._os_explore_meowfficer_cleanup()
        self.assertEqual(runner.config.OpsiExplore_MeowfficerCleanupState['next'], 0)
        self.assertEqual(runner.config.OpsiExplore_MeowfficerCleanupState['attempts'], 1)


if __name__ == '__main__':
    unittest.main()
