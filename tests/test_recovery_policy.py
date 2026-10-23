"""恢复授权、动态设置、后台监护与真实工作子进程退出的隔离验证。"""

import json
import multiprocessing
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from alas import AzurLaneAutoScript
from module.config.recovery import RecoveryStopped, check_error_recovery, read_recovery_settings
from module.device.connection import retry
from module.device.method.retry import retry_backend
from module.exception import (
    AutoSearchSetError, EmulatorNotRunningError, GameBugError, GameNotRunningError,
    GamePageUnknownError, GameStuckError, GameTooManyClickError, RequestHumanTakeover, ScriptError,
)


def _worker_exit_probe(folder, background, queue):
    """只有测试子进程创建模拟 worker，禁止连接设备或发送通知。"""
    from contextlib import ExitStack
    from module.runtime.process_manager import ProcessManager
    with ExitStack() as stack:
        for name in ('alas.logger', 'alas.handle_notify', 'alas.notify_webui',
                     'module.runtime.preview.set_task'):
            stack.enter_context(patch(name))
        script = AzurLaneAutoScript('isolated-probe')
        script.config = Mock(
            Error_HandleError=False, Error_StrictRestart=False,
            DailySummary_Enable=False, Error_TaskRestartLimit=0,
            EmulatorManagement_ScheduledEmulatorRestart=False,
            Error_WatchdogEnable=True, Error_WatchdogTaskEnable=True,
            Error_WatchdogTaskTimeout=1,
        )
        script.device = Mock()
        script.checker = Mock()
        script.checker.is_recovered.return_value = False
        script.config.cross_get.return_value = False
        script.get_next_task = Mock(return_value='Commission')
        script._start_watchdog = Mock()
        script._sync_background_services = Mock()
        stack.enter_context(patch('module.config.utils.is_oobe_needed', return_value=False))
        stack.enter_context(patch('module.base.backup.backup'))
        stack.enter_context(patch.dict('os.environ', {'ALAS_DEBUG_SERVER': '0'}))
        script._channel_float_done = True
        script.save_error_log = lambda: Path(folder, 'saved-scene.txt').write_text('错误现场已保存', encoding='utf-8')
        script.commission = Mock(side_effect=GameStuckError('模拟游戏卡死'))
        def forbidden(*args, **kwargs):
            Path(folder, 'unexpected-recovery.txt').write_text('检测到越权恢复', encoding='utf-8')
            raise AssertionError('禁止执行恢复操作')
        script.config.task_call = Mock(side_effect=forbidden)
        script._try_restart_emulator = Mock(side_effect=forbidden)
        script.device.app_stop.side_effect = forbidden
        script.device.app_start.side_effect = forbidden
        script.device.emulator_stop.side_effect = forbidden
        script.device.emulator_start.side_effect = forbidden
        if background:
            script._watchdog_active = True
            script._watchdog_task_name = 'Commission'
            threading.Thread(
                target=lambda: script._watchdog_recover(61, task_name='Commission'), daemon=True,
            ).start()
            threading.Event().wait(45)
            raise AssertionError('后台监护没有退出整个 worker')
        stack.enter_context(patch.object(ProcessManager, '_run_process', side_effect=lambda *args: script.loop()))
        ProcessManager.run_process('isolated-probe', 'alas', queue, run_id='probe-run')


class RecoveryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.folder / 'instance.json'
        self.enterContext(patch('module.config.recovery.filepath_config', return_value=str(self.path)))
        self.enterContext(patch('alas.filepath_config', return_value=str(self.path)))
        self.enterContext(patch('alas.logger'))
        self.enterContext(patch('module.config.recovery.logger'))
        self.enterContext(patch('alas.handle_notify'))
        self.enterContext(patch('alas.notify_webui'))
        self.enterContext(patch('module.runtime.preview.set_task'))
        self.enterContext(patch('alas.time.sleep'))
        self.data = {'Alas': {'Error': {
            'HandleError': True, 'StrictRestart': False, 'TaskRestartLimit': 3,
            'GameStuckRestart': False, 'GameStuckThreshold': 3,
            'AdbOfflineRestart': False, 'AdbOfflineThreshold': 3,
            'WatchdogEnable': False, 'WatchdogTaskEnable': True, 'WatchdogTaskTimeout': 1,
        }, 'EmulatorManagement': {
            'ScheduledEmulatorRestart': False, 'ForceScheduledRestart': False, 'RestartIntervalHours': 1,
        }, 'DailySummary': {'Enable': False}}, 'RunParams': {'Reboot': {'Tries': 3}}}
        self.write()
        self.script = AzurLaneAutoScript('isolated-test')
        self.script.config = Mock(config_name='isolated-test', Error_LowPushMode=True)
        self.device = Mock()
        self.device.emulator_stop.return_value = True
        self.device.emulator_start.return_value = True
        self.script.device = self.device
        self.script.checker = Mock()
        self.script.checker.is_available.return_value = True
        self.script._channel_float_done = True
        self.script.commission = Mock()
        self.script.restart = Mock()
        self.script.save_error_log = Mock()
        self.script._emulator_op_with_timeout = Mock(side_effect=lambda func, **kwargs: func())
        self.script.config.cross_get.return_value = False
        self.script._sync_background_services = Mock()
        self.script._start_watchdog = Mock()
        self.script._stop_watchdog = Mock()

    def write(self):
        self.path.write_text(json.dumps(self.data), encoding='utf-8')

    def settings(self, **values):
        self.data['Alas']['Error'].update(values)
        self.write()

    def test_disabled_master_exits_on_every_recovery_exception_without_actions(self):
        for error_type in (GameNotRunningError, GameStuckError, GameTooManyClickError, GameBugError,
                           GamePageUnknownError, EmulatorNotRunningError, RequestHumanTakeover,
                           AutoSearchSetError, ScriptError, TypeError, TimeoutError):
            with self.subTest(error=error_type.__name__):
                self.settings(HandleError=False)
                self.script.commission.side_effect = error_type('隔离故障')
                with self.assertRaises(SystemExit) as caught:
                    self.script.run('Commission')
                self.assertEqual(caught.exception.code, 1)
                self.script.config.task_call.assert_not_called()
                self.device.emulator_stop.assert_not_called()
                self.device.app_stop.assert_not_called()
                self.script.save_error_log.assert_called()
                self.script.checker.check_now.assert_not_called()

    def test_human_script_and_unknown_errors_never_restart(self):
        for error_type in (RequestHumanTakeover, ScriptError, ValueError):
            self.script.commission.side_effect = error_type('需要处理根因')
            with self.assertRaises(SystemExit):
                self.script.run('Commission')
        self.script.config.task_call.assert_not_called()
        self.device.emulator_stop.assert_not_called()

    def test_game_stuck_switch_and_threshold_combinations(self):
        for enabled in (False, True):
            self.script.consecutive_game_stuck = 0
            self.device.emulator_stop.reset_mock()
            self.device.emulator_start.reset_mock()
            self.settings(GameStuckRestart=enabled, GameStuckThreshold=3)
            self.script.commission.side_effect = GameStuckError('卡死')
            for count in range(1, 4):
                self.assertEqual(self.script.run('Commission'), 'recoverable')
                self.assertEqual(self.device.emulator_stop.call_count, int(enabled and count >= 3))
            self.assertEqual(self.script.config.task_call.call_count, 3 if not enabled else 6)

    def test_offline_switch_and_threshold_apply_to_task_and_global_boundaries(self):
        for enabled in (False, True):
            self.settings(AdbOfflineRestart=enabled, AdbOfflineThreshold=2)
            self.script.consecutive_adb_offline = 0
            self.device.emulator_stop.reset_mock()
            for count in range(1, 3):
                self.assertEqual(self.script._handle_task_error('Commission', EmulatorNotRunningError('离线')), 'recoverable')
                self.assertEqual(self.device.emulator_stop.call_count, int(enabled and count >= 2))

    def test_live_file_overrides_stale_bound_values_without_reloading_task(self):
        self.script.config.Error_HandleError = True
        self.settings(HandleError=False)
        self.script.commission.side_effect = GameStuckError('运行中关闭异常处理')
        before = self.path.read_bytes()
        with self.assertRaises(SystemExit):
            self.script.run('Commission')
        self.script.config.load.assert_not_called()
        self.script.config.bind.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)

    def test_pending_error_restart_is_revoked_but_manual_restart_still_works(self):
        self.script.commission.side_effect = GameNotRunningError('游戏未运行')
        self.assertEqual(self.script.run('Commission'), 'recoverable')
        self.settings(HandleError=False)
        with self.assertRaises(SystemExit):
            self.script.run('Restart')
        self.script.restart.assert_not_called()
        self.script._pending_recovery = None
        self.assertTrue(self.script.run('Restart'))
        self.script.restart.assert_called_once_with()

    def test_revoked_pending_recovery_exits_before_task_selection_or_device_initialization(self):
        self.script._pending_recovery = ('Commission', GameStuckError('已经排队的故障恢复'))
        self.script.config.DailySummary_Enable = False
        self.script.get_next_task = Mock(return_value='Restart')
        self.script.checker.is_recovered.return_value = False
        self.settings(HandleError=False)
        with (patch('module.config.utils.is_oobe_needed', return_value=False),
              patch('module.base.backup.backup'),
              patch.dict('os.environ', {'ALAS_DEBUG_SERVER': '0'})):
            with self.assertRaises(SystemExit):
                self.script.loop()
        self.script.get_next_task.assert_not_called()
        self.script.checker.wait_until_available.assert_not_called()
        self.device.screenshot.assert_not_called()
        self.script.restart.assert_not_called()
        self.script.config.task_call.assert_not_called()

    def test_scheduled_emulator_restart_is_independent_of_error_switches(self):
        self.settings(HandleError=False, GameStuckRestart=False, AdbOfflineRestart=False)
        self.data['Alas']['EmulatorManagement']['ScheduledEmulatorRestart'] = True
        self.write()
        self.assertTrue(self.script._try_restart_emulator(reason='scheduled'))
        self.device.emulator_stop.assert_called_once_with()
        self.device.emulator_start.assert_called_once_with(deep=False, failures=0)

    def test_false_platform_result_is_not_reported_as_restart_success(self):
        self.settings(AdbOfflineRestart=True, AdbOfflineThreshold=1)
        self.device.emulator_stop.return_value = False
        self.assertFalse(self.script._try_restart_emulator())
        self.device.emulator_start.assert_not_called()
        self.assertEqual(self.script._emulator_restart_failures, 1)

    def test_dynamic_offline_enable_threshold_and_disable_reset_counter(self):
        self.settings(AdbOfflineRestart=True, AdbOfflineThreshold=3)
        self.assertFalse(self.script._try_restart_emulator())
        self.settings(AdbOfflineRestart=False)
        self.assertFalse(self.script._try_restart_emulator())
        self.assertEqual(self.script.consecutive_adb_offline, 0)
        self.settings(AdbOfflineRestart=True, AdbOfflineThreshold=2)
        self.assertFalse(self.script._try_restart_emulator())
        self.assertTrue(self.script._try_restart_emulator())

    def test_watchdog_master_subswitch_zero_timeout_and_live_disable(self):
        self.script._watchdog_active = True
        self.script._watchdog_task_name = 'Commission'
        for values in ({'WatchdogEnable': False}, {'WatchdogEnable': True, 'WatchdogTaskEnable': False},
                       {'WatchdogTaskEnable': True, 'WatchdogTaskTimeout': 0}):
            self.settings(**values)
            with patch('module.device.platform.Platform') as platform:
                self.script._watchdog_recover(1000, task_name='Commission')
            platform.assert_not_called()
        self.settings(WatchdogEnable=True, WatchdogTaskEnable=True, WatchdogTaskTimeout=1, HandleError=False)
        with self.assertRaises(SystemExit):
            self.script._watchdog_recover(61, task_name='Commission')
        self.device.emulator_stop.assert_not_called()

    def test_watchdog_cannot_bypass_stuck_permission_or_threshold(self):
        self.script._watchdog_active = True
        self.script._watchdog_task_name = 'Commission'
        self.settings(WatchdogEnable=True, GameStuckRestart=False)
        with patch('module.device.platform.Platform') as platform:
            with self.assertRaises(SystemExit):
                self.script._watchdog_recover(61, task_name='Commission')
        platform.assert_not_called()

    def test_watchdog_scheduled_restart_obeys_own_switches_and_sensitive_tasks(self):
        self.script._watchdog_active = True
        self.script._watchdog_task_name = 'Commission'
        self.settings(HandleError=False, WatchdogEnable=False)
        self.data['Alas']['EmulatorManagement'].update(ScheduledEmulatorRestart=True, ForceScheduledRestart=True)
        self.data['Commission'] = {'Scheduler': {'Sensitive': True}}
        self.write()
        with patch('module.device.platform.Platform') as platform:
            self.script._watchdog_recover(3601, reason='force_scheduled_restart', task_name='Commission')
            platform.assert_not_called()
            platform.return_value.emulator_stop.return_value = True
            self.data['Commission']['Scheduler']['Sensitive'] = False
            self.write()
            self.script._watchdog_recover(3601, reason='force_scheduled_restart', task_name='Commission')
            platform.return_value.emulator_stop.assert_called_once_with()
        self.assertTrue(self.script._forced_restart_pending)
        self.assertEqual(self.script._handle_task_error('Commission', EmulatorNotRunningError('主动中断')), 'recoverable')
        self.assertIsNone(self.script._pending_recovery)

    def test_restart_recovery_respects_configured_retry_budget(self):
        self.script.restart.side_effect = GameBugError('启动失败')
        self.data['RunParams']['Reboot']['Tries'] = 2
        self.write()
        self.assertEqual(self.script.run('Restart'), 'recoverable')
        with self.assertRaises(SystemExit):
            self.script.run('Restart')
        self.assertEqual(self.script.config.task_call.call_count, 1)
        self.device.emulator_stop.assert_not_called()

    def test_disabling_limit_only_releases_unchanged_enabled_fault_schedule(self):
        deadline = datetime(2026, 10, 24)
        changed = deadline + timedelta(hours=5)
        self.script.task_restart_delays = dict.fromkeys(('Commission', 'Research', 'Main'), deadline)
        self.script.task_restart_record = {'Commission': 2}
        self.data.update(Commission={'Scheduler': {'Enable': True, 'NextRun': str(deadline)}},
                         Research={'Scheduler': {'Enable': True, 'NextRun': str(changed)}},
                         Main={'Scheduler': {'Enable': False, 'NextRun': str(deadline)}})
        self.settings(TaskRestartLimit=0)
        self.script._sync_task_restart_limit()
        self.assertEqual(self.script.task_restart_delays, {})
        self.assertEqual(self.script.task_restart_record, {})
        self.assertEqual(self.script.config.cross_set.call_count, 1)
        self.assertEqual(self.script.config.cross_set.call_args.args[0], 'Commission.Scheduler.NextRun')
        self.script.config.task_delay.assert_not_called()

    def test_device_backends_stop_before_reconnect_or_recover_callback(self):
        self.settings(HandleError=False)
        for decorate in (retry, lambda func: retry_backend(func, recover=Mock(), label='test')):
            @decorate
            def read(device):
                return device.operation()
            device = Mock(config=self.script.config)
            device.operation.side_effect = ConnectionResetError('断线')
            with self.assertRaises(RecoveryStopped):
                read(device)
            device.operation.assert_called_once_with()
            device.adb_reconnect.assert_not_called()

    def test_backend_rechecks_live_switch_before_next_attempt(self):
        def fail_once():
            self.settings(HandleError=False)
            raise ConnectionResetError('运行中关闭')
        device = Mock(config=self.script.config)
        device.operation.side_effect = fail_once
        recover = Mock()
        @retry_backend(recover=recover, label='test')
        def read(target):
            return target.operation()
        with self.assertRaises(RecoveryStopped):
            read(device)
        recover.assert_not_called()
        device.operation.assert_called_once_with()

    def test_unreadable_settings_fail_closed_without_changing_bound_config(self):
        self.path.write_text('{broken', encoding='utf-8')
        self.assertFalse(read_recovery_settings(self.script.config).value('Error_HandleError'))
        with self.assertRaises(RecoveryStopped):
            check_error_recovery(self.script.config, GameStuckError('卡死'))
        self.script.config.bind.assert_not_called()

    def test_daily_summary_can_be_enabled_and_recreated_without_task_reload(self):
        del self.script.__dict__['_sync_background_services']
        self.script._start_daily_summary_scheduler = Mock()
        self.script._sync_background_services()
        self.script._start_daily_summary_scheduler.assert_not_called()
        self.data['Alas']['DailySummary']['Enable'] = True
        self.write()
        self.script._sync_background_services()
        self.script._sync_background_services()
        self.assertEqual(self.script._start_daily_summary_scheduler.call_count, 2)
        self.script.config.load.assert_not_called()
        self.script.config.bind.assert_not_called()

    def test_stop_saves_cached_image_and_log_without_asking_device_for_new_frame(self):
        import os
        import numpy as np
        current = Path.cwd()
        os.chdir(self.folder)
        self.addCleanup(os.chdir, current)
        log = self.folder / 'source.log'
        log.write_text('原始故障日志\n', encoding='utf-8')
        self.script.config.Error_SaveError = True
        self.script.config.Error_LlmAnalysis = True
        self.device.screenshot_deque = [{'time': datetime(2026, 10, 23),
                                         'image': np.zeros((720, 1280, 3), dtype=np.uint8)}]
        del self.script.__dict__['save_error_log']
        self.settings(HandleError=False)
        with (patch('alas.logger.log_file', str(log)),
              patch('module.handler.sensitive_info.handle_sensitive_image', side_effect=lambda image: image),
              patch('module.handler.sensitive_info.handle_sensitive_logs', side_effect=lambda lines: lines),
              patch('module.llm.analyze_exception') as analyze):
            with self.assertRaises(SystemExit):
                self.script._stop_on_error(GameStuckError('原始异常'), '等待人工处理')
        saved = list((self.folder / 'log/error/isolated-test').glob('*'))
        self.assertEqual(len(saved), 1)
        self.assertEqual(len(list(saved[0].glob('*.png'))), 1)
        self.assertIn('原始故障日志', (saved[0] / 'log.txt').read_text(encoding='utf-8'))
        self.device.screenshot.assert_not_called()
        self.device.app_stop.assert_not_called()
        analyze.assert_not_called()

    def test_live_watchdog_loop_rereads_master_before_each_detection(self):
        self.script._watchdog_active = True
        self.script._watchdog_task_start = 1
        self.script._watchdog_task_name = 'Commission'
        self.script._watchdog_recover = Mock()
        checks = iter((False, True, False, None))
        def wait(interval):
            enabled = next(checks)
            if enabled is None:
                return True
            self.settings(WatchdogEnable=enabled)
            return False
        self.script._watchdog_stop = Mock()
        self.script._watchdog_stop.wait.side_effect = wait
        with patch('alas.time.monotonic', return_value=100):
            self.script._watchdog_loop()
        self.script._watchdog_recover.assert_called_once_with(99, reason='task_timeout', task_name='Commission')
        self.script.config.load.assert_not_called()

    def test_daily_summary_real_thread_is_recreated_after_disable(self):
        del self.script.__dict__['_sync_background_services']
        self.script._get_daily_summary_service = Mock()
        self.script._check_daily_summary = Mock()
        self.script._daily_summary_interval = Mock(return_value=0.01)
        self.data['Alas']['DailySummary']['Enable'] = True
        self.write()
        self.script._sync_background_services()
        old = self.script._daily_summary_thread
        self.addCleanup(self.script._stop_daily_summary_scheduler)
        self.assertTrue(old.is_alive())
        self.data['Alas']['DailySummary']['Enable'] = False
        self.write()
        old.join(2)
        self.assertFalse(old.is_alive())
        self.data['Alas']['DailySummary']['Enable'] = True
        self.write()
        self.script._sync_background_services()
        self.assertIsNot(old, self.script._daily_summary_thread)
        self.assertTrue(self.script._daily_summary_thread.is_alive())

    def test_active_game_start_and_stop_are_preserved_with_recovery_disabled(self):
        from module.device.device import Device
        from module.device.app_control import AppControl
        device = Device.__new__(Device)
        device.config = SimpleNamespace(Error_HandleError=False, Emulator_GameSettings=False, config_name='isolated')
        device.stuck_record_clear = Mock()
        device.click_record_clear = Mock()
        with (patch.object(AppControl, 'app_start') as start,
              patch.object(AppControl, 'app_stop') as stop,
              patch('module.api.account_service.restore_worker')):
            device.app_start()
            device.app_stop()
        start.assert_called_once_with()
        stop.assert_called_once_with()

    def test_manual_login_start_is_allowed_but_failed_attempt_is_not_retried(self):
        from module.handler.login import LoginHandler
        login = LoginHandler.__new__(LoginHandler)
        login.config = self.script.config
        login.device = Mock()
        login.handle_app_login = Mock()
        self.settings(HandleError=False)
        login.device.app_is_running_bounded.return_value = True
        login.app_restart()
        login.device.app_stop.assert_called_once_with()
        login.device.app_start.assert_called_once_with()
        login.device.reset_mock()
        login.device.app_is_running_bounded.return_value = False
        with self.assertRaises(RecoveryStopped):
            login.app_restart()
        login.device.app_stop.assert_called_once_with()
        login.device.app_start.assert_called_once_with()

    def test_manual_start_failure_enters_revocable_recovery_scope_and_cleans_it(self):
        from module.config.recovery import check_pending_recovery
        from module.handler.login import LoginHandler
        login = LoginHandler.__new__(LoginHandler)
        login.config = self.script.config
        login.device = self.device
        login.handle_app_login = Mock()
        self.device.config = self.script.config
        self.device.app_is_running_bounded.side_effect = [False, True]
        starts = []

        def start():
            check_pending_recovery(self.device)
            starts.append(True)

        def wait(seconds):
            if '_recovery_error' in self.device.__dict__:
                self.settings(HandleError=False)

        self.device.app_start.side_effect = start
        self.device.sleep.side_effect = wait
        with self.assertRaises(RecoveryStopped):
            login.app_restart()
        self.assertEqual(starts, [True])
        self.device.app_is_running_bounded.assert_called_once_with()
        login.handle_app_login.assert_not_called()
        self.assertNotIn('_recovery_error', self.device.__dict__)

    def test_platform_internal_retry_cannot_start_after_stop_failure_when_disabled(self):
        from module.device.platform.platform_windows import PlatformWindows
        from module.device.platform.platform_mac import PlatformMac
        self.settings(HandleError=False)
        for method in (PlatformWindows.emulator_stop.__wrapped__, PlatformMac.emulator_stop):
            platform = Mock(config=self.script.config)
            platform._emulator_function_wrapper.return_value = False
            with self.assertRaises(RecoveryStopped):
                method(platform)
            self.assertEqual(platform._emulator_function_wrapper.call_count, 1)
            platform._emulator_function_wrapper.assert_called_once_with(platform._emulator_stop)

    def test_adb_service_restart_honors_latest_own_switch(self):
        from module.device.connection import Connection
        self.data['Alas']['Emulator'] = {'AdbRestart': False}
        self.write()
        client = Mock(config=self.script.config)
        self.assertFalse(Connection.adb_restart(client))
        client.adb_client.server_kill.assert_not_called()

    def test_forced_scheduled_disconnect_from_backend_bypasses_only_its_error_stop(self):
        self.settings(HandleError=False)
        self.data['Alas']['EmulatorManagement']['ScheduledEmulatorRestart'] = True
        self.write()
        self.script._forced_restart_pending = True
        self.script.commission.side_effect = RecoveryStopped(ConnectionResetError('计划关闭模拟器'), '总开关已关闭')
        self.assertEqual(self.script.run('Commission'), 'recoverable')
        self.device.emulator_stop.assert_called_once_with()
        self.assertFalse(self.script._record_task_restart('Commission', 'recoverable'))
        self.assertEqual(self.script.task_restart_record, {})

    def test_restart_timeout_is_game_stuck_and_never_offline(self):
        from module.handler.login import LoginHandler
        login = LoginHandler.__new__(LoginHandler)
        finished = threading.Event()
        self.addCleanup(finished.set)
        with self.assertRaises(GameStuckError):
            login._call_with_restart_deadline(lambda: finished.wait(1), timeout=0.01, operation_name='模拟启停阻塞')

    def test_disabling_during_recovery_stops_before_next_device_action_and_restores_scope(self):
        from module.config.recovery import recovery_scope
        from module.device.device import Device
        from module.device.app_control import AppControl
        device = Device.__new__(Device)
        device.config = self.script.config
        device.stuck_record_clear = Mock()
        device.click_record_clear = Mock()
        with patch.object(AppControl, 'app_stop') as stop:
            with self.assertRaises(RecoveryStopped):
                with recovery_scope(self.script.config, device, GameStuckError('已进入恢复')):
                    self.settings(HandleError=False)
                    device.app_stop()
            stop.assert_not_called()
        self.assertNotIn('_recovery_error', device.__dict__)

    def test_settings_observer_stops_pending_recovery_even_without_timeout_monitoring(self):
        self.settings(HandleError=False, WatchdogEnable=False)
        self.script._pending_recovery = ('Commission', GameNotRunningError('等待恢复'))
        self.script._watchdog_active = False
        self.script._watchdog_stop = Mock()
        self.script._watchdog_stop.wait.return_value = False
        with self.assertRaises(SystemExit):
            self.script._watchdog_loop()
        self.device.emulator_stop.assert_not_called()
        self.script.config.task_call.assert_not_called()

    def test_unknown_page_only_reports_to_scheduler_and_never_restarts_in_ui(self):
        from module.ui.ui import UI
        ui = UI.__new__(UI)
        ui.config = self.script.config
        ui.device = self.device
        with patch('module.ui.ui.Timer') as timer:
            timer.return_value.start.return_value.reached.return_value = True
            with self.assertRaises(GamePageUnknownError):
                ui.ui_get_current_page()
        self.device.app_stop.assert_not_called()
        self.device.app_start.assert_not_called()

    def test_game_specific_fog_recovery_cannot_bypass_master(self):
        from module.os.map import OSMap
        owner = Mock(config=self.script.config, device=self.device)
        owner.appear.return_value = True
        self.settings(HandleError=False)
        with self.assertRaises(RecoveryStopped):
            OSMap.handle_fog_block(owner)
        self.device.app_stop.assert_not_called()
        self.device.app_start.assert_not_called()

    def test_local_ship_change_failure_cannot_mutate_or_reschedule_when_disabled(self):
        from module.campaign.fleet_selection import FleetSelectionMixin
        owner = Mock(config=self.script.config, change_vanguard_equip=False)
        owner.vanguard_change_execute.side_effect = RequestHumanTakeover('换船失败')
        self.settings(HandleError=False)
        before = self.path.read_bytes()
        with self.assertRaises(RecoveryStopped):
            FleetSelectionMixin._change_ship(owner, 'vanguard')
        owner.config.task_stop.assert_not_called()
        self.assertEqual(before, self.path.read_bytes())

    def test_sp_local_exception_cannot_delay_fault_task_when_disabled(self):
        from module.event.campaign_sp import CampaignSP
        from module.event.base import EventBase
        owner = CampaignSP.__new__(CampaignSP)
        owner.config = self.script.config
        self.settings(HandleError=False)
        with (patch('module.event.campaign_sp.os.path.exists', return_value=True),
              patch.object(EventBase, 'run', side_effect=RequestHumanTakeover('未知进入错误'))):
            with self.assertRaises(RecoveryStopped):
                owner.run()
        self.script.config.task_delay.assert_not_called()

    def test_app_status_does_not_turn_device_fault_into_game_not_running(self):
        from module.device.app_control import AppControl
        for error in (EmulatorNotRunningError('离线'), ConnectionResetError('ADB 断开'), RequestHumanTakeover('未授权')):
            target = Mock(config=self.script.config)
            target.adb_shell.side_effect = error
            with self.assertRaises(type(error)) as caught:
                AppControl.app_is_running_bounded(target)
            self.assertIs(caught.exception, error)

    def test_launch_exhaustion_requires_manual_action_without_multiplying_retry_rounds(self):
        from module.handler.login import LoginHandler
        login = LoginHandler.__new__(LoginHandler)
        login.config = self.script.config
        login.device = self.device
        login.handle_app_login = Mock()
        self.device.app_is_running_bounded.return_value = False
        self.data['RunParams']['Reboot']['Tries'] = 2
        self.write()
        # 不等待观察阶段；已耗尽的启动预算必须转换为人工处理，不再排队一轮启动。
        with patch('module.handler.login.time.monotonic', side_effect=[0, 1000]):
            with self.assertRaises(RequestHumanTakeover):
                login.app_restart()
        self.assertEqual(self.device.app_start.call_count, 2)
        login.handle_app_login.assert_not_called()

    def test_watchdog_does_not_repeat_emulator_stop_when_io_remains_blocked(self):
        self.script._watchdog_active = True
        self.script._watchdog_task_start = 1
        self.script._watchdog_interrupt = (1, 0)
        self.script._watchdog_stop = Mock()
        self.script._watchdog_stop.wait.return_value = False
        with patch('alas.time.monotonic', return_value=100):
            with self.assertRaises(SystemExit):
                self.script._watchdog_loop()
        self.device.emulator_stop.assert_not_called()

    def test_completed_watchdog_interruption_does_not_stop_a_new_task_round(self):
        self.script._watchdog_active = True
        self.script._watchdog_task_start = 2
        self.script._watchdog_interrupt = (1, 0)
        self.script._watchdog_stop = Mock()
        self.script._watchdog_stop.wait.side_effect = [False, True]
        with patch('alas.time.monotonic', return_value=100):
            self.script._watchdog_loop()
        self.assertIsNone(self.script._watchdog_interrupt)
        self.script.save_error_log.assert_not_called()

    def test_launch_retry_budget_can_increase_and_decrease_during_operation(self):
        from module.handler.login import LoginHandler
        for initial, updated, expected_attempts in ((1, 3, 2), (3, 1, 1)):
            with self.subTest(initial=initial, updated=updated):
                self.data['RunParams']['Reboot']['Tries'] = initial
                self.write()
                login = LoginHandler.__new__(LoginHandler)
                login.config = self.script.config
                login.device = Mock()
                login.handle_app_login = Mock()
                checks = 0
                def status():
                    nonlocal checks
                    checks += 1
                    if checks == 1:
                        self.data['RunParams']['Reboot']['Tries'] = updated
                        self.write()
                        return False
                    return True
                login.device.app_is_running_bounded.side_effect = status
                with patch('module.handler.login.time.monotonic', side_effect=[0, 1000]):
                    if updated > initial:
                        login.app_restart()
                        login.handle_app_login.assert_called_once_with()
                    else:
                        with self.assertRaises(RequestHumanTakeover):
                            login.app_restart()
                self.assertEqual(login.device.app_start.call_count, expected_attempts)

    def test_mcp_manual_restart_works_offline_with_all_error_switches_disabled(self):
        from module.mcp.device_worker import perform
        self.settings(HandleError=False, AdbOfflineRestart=False, GameStuckRestart=False)
        with (patch('module.config.config.AzurLaneConfig', return_value=self.script.config),
              patch('module.device.device.Device') as device_class,
              patch('module.device.platform.Platform') as platform):
            platform.return_value.emulator_stop.return_value = True
            platform.return_value.emulator_start.return_value = True
            self.assertIn('Success', perform('restart_emulator', 'isolated-test')['text'])
        device_class.assert_not_called()
        platform.assert_called_once_with(self.script.config, connect=False)

    def test_mcp_manual_restart_does_not_report_false_platform_result_as_success(self):
        from module.mcp.device_worker import perform
        with (patch('module.config.config.AzurLaneConfig', return_value=self.script.config),
              patch('module.device.platform.Platform') as platform):
            platform.return_value.emulator_stop.return_value = False
            with self.assertRaises(RuntimeError):
                perform('restart_emulator', 'isolated-test')
        platform.return_value.emulator_start.assert_not_called()

    def test_explicit_emulator_manager_skips_game_initialization(self):
        self.settings(HandleError=False, AdbOfflineRestart=False)
        self.script.__dict__.pop('device')
        self.script._channel_float_done = False
        self.script.handle_channel_float = Mock(side_effect=AssertionError('不应访问游戏'))
        self.script.emulator_manager = Mock()
        self.assertTrue(self.script.run('EmulatorManager'))
        self.script.emulator_manager.assert_called_once_with()
        self.script.handle_channel_float.assert_not_called()
        self.assertNotIn('device', self.script.__dict__)

    def test_sp_cannot_treat_human_error_as_completed_when_recovery_is_enabled(self):
        from module.event.campaign_sp import CampaignSP
        from module.event.base import EventBase
        owner = CampaignSP.__new__(CampaignSP)
        owner.config = self.script.config
        error = RequestHumanTakeover('舰队属性不满足，不能认定关卡已完成')
        with (patch('module.event.campaign_sp.os.path.exists', return_value=True),
              patch.object(EventBase, 'run', side_effect=error)):
            with self.assertRaises(RequestHumanTakeover) as caught:
                owner.run()
        self.assertIs(caught.exception, error)
        self.script.config.task_delay.assert_not_called()

    def test_actual_worker_exit_does_not_exit_parent_or_unrelated_worker(self):
        context = multiprocessing.get_context('spawn')
        other_stop = context.Event()
        other = context.Process(target=other_stop.wait, args=(30,))
        other.start()
        self.addCleanup(lambda: other.join(5))
        self.addCleanup(other_stop.set)
        for background in (False, True):
            with self.subTest(background=background):
                scene = self.folder / 'saved-scene.txt'
                if scene.exists():
                    scene.unlink()
                queue = context.Queue()
                worker = context.Process(target=_worker_exit_probe, args=(str(self.folder), background, queue))
                worker.start()
                worker.join(25)
                if worker.is_alive():
                    worker.terminate()
                    worker.join(5)
                    self.fail('故障 worker 未及时退出')
                self.assertEqual(worker.exitcode, 1)
                self.assertTrue(scene.exists())
                self.assertFalse((self.folder / 'unexpected-recovery.txt').exists())
                self.assertTrue(other.is_alive())
                if not background:
                    from module.runtime.worker_events import ExitEvent, WorkerResult
                    event = queue.get(timeout=5)
                    self.assertIsInstance(event, ExitEvent)
                    self.assertEqual(event.result, WorkerResult.ERROR)
                queue.close()
                queue.join_thread()


if __name__ == '__main__':
    unittest.main()
