"""用进程内时区测试真实排程文件、界面和等待，避免修改操作系统设置。"""

import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from alas import AzurLaneAutoScript
from module.api.config_service import ConfigService
from module.api.protocol import ConfigChange
from module.api.runtime_service import RuntimeService
from module.config.config import AzurLaneConfig
from module.config.time_source import now
from module.scheduler.engine import Engine
from module.scheduler.models import ProgramDocument
from module.scheduler.runtime import SchedulerRuntime
from module.scheduler.templates import edge, node
from tests.test_api import fixture


class SchedulerTimezoneTests(unittest.TestCase):
    def setUp(self):
        self.tzset = getattr(time, 'tzset', None)
        if os.name == 'nt':
            from ctypes import CDLL

            self.tzset = CDLL('ucrtbase')._tzset
        if self.tzset is None:
            self.skipTest('此平台不支持进程内切换时区')
        self.addCleanup(self.set_zone, os.environ.get('TZ'), refresh=True)
        self.set_zone('CST-8', refresh=True)
        self.clock = datetime(2026, 11, 11, 15, 30, tzinfo=timezone.utc)
        self.midnight = datetime(2026, 11, 11, 16, tzinfo=timezone.utc)
        self.enterContext(patch('module.config.time_source.network_time.timestamp',
                                side_effect=lambda: self.clock.timestamp()))
        self.enterContext(patch.object(AzurLaneConfig, 'is_hoarding_task', False))
        self.root = fixture(self.enterContext(tempfile.TemporaryDirectory()))
        self.configs = ConfigService(self.root)
        self.path = self.configs.path('testpilot')
        for module in ('module.config.config', 'module.config.config_updater'):
            self.enterContext(patch(f'{module}.filepath_config', return_value=str(self.path)))
        self.enterContext(patch('module.api.runtime_service.monthly_statistics_resources', return_value=[]))

    def set_zone(self, zone, *, refresh=False):
        if zone is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = zone
        if refresh:
            self.tzset()

    def stored(self):
        return self.configs.read_json(self.path)['Daily']['Scheduler']['NextRun']

    def write_legacy_schedule(self):
        data = self.configs.read_json(self.path)
        data['Daily']['Scheduler'].update(Enable=True, NextRun='2026-11-12 00:00:00')
        data['Reward']['Scheduler']['NextRun'] = '2020-01-01 00:00:00'
        self.path.write_text(json.dumps(data), encoding='utf-8')

    def test_legacy_deadline_survives_live_timezone_change_and_reload(self):
        self.write_legacy_schedule()
        config = AzurLaneConfig('testpilot', task='Daily')
        self.assertEqual(self.stored(), str(self.midnight))
        self.assertEqual(config.Scheduler_NextRun, datetime(2026, 11, 12))
        self.assertEqual(self.configs.read_json(self.path)['Reward']['Scheduler']['NextRun'],
                         '2020-01-01 00:00:00')

        for zone, local in (
            ('JST-9', datetime(2026, 11, 12, 1)),
            ('NST3:30', datetime(2026, 11, 11, 12, 30)),
            ('CST-8', datetime(2026, 11, 12)),
        ):
            with self.subTest(zone=zone):
                self.set_zone(zone)
                config.get_next_task()
                self.assertEqual(config.Scheduler_NextRun, local)
                self.assertEqual(config.cross_get(['Daily', 'Scheduler', 'NextRun']), local)
                for loaded in (config, AzurLaneConfig('testpilot')):
                    loaded.get_next_task()
                    task = next(t for t in loaded.waiting_task if t.command == 'Daily')
                    self.assertEqual(task.deadline, self.midnight)
                    self.assertEqual(task.next_run, local)
                self.assertEqual(self.stored(), str(self.midnight))
        self.clock = self.midnight + timedelta(seconds=1)
        config.get_next_task()
        self.assertIn('Daily', [t.command for t in config.pending_task])

    def test_webui_startup_migrates_legacy_schedule_for_stopped_instance(self):
        self.write_legacy_schedule()
        self.configs.migrate_schedules()
        self.assertEqual(self.stored(), str(self.midnight))
        self.set_zone('JST-9')
        data = self.configs.get('testpilot')['values']
        self.assertEqual(data['Daily']['Scheduler']['NextRun'], '2026-11-12 01:00:00')

    def test_webui_edit_copy_and_queue_keep_same_instant(self):
        path = 'Daily.Scheduler.NextRun'
        self.configs.patch('testpilot', None, [
            ConfigChange(path=path, value='2026-11-12 00:00:00'),
            ConfigChange(path='Daily.Scheduler.Enable', value=True),
        ])
        self.assertEqual(self.stored(), str(self.midnight))
        self.set_zone('JST-9')
        config = self.configs.patch('testpilot', None, [ConfigChange(path='Hard.Scheduler.Enable', value=True)])
        self.assertEqual(config['values']['Daily']['Scheduler']['NextRun'], '2026-11-12 01:00:00')
        self.assertEqual(self.stored(), str(self.midnight))
        with patch('module.scheduler.store.ProgramStore'):
            self.configs.create('copy', source='testpilot')
        self.assertEqual(self.configs.read_json(self.configs.path('copy'))['Daily']['Scheduler']['NextRun'],
                         str(self.midnight))

        runtime = RuntimeService(self.configs)
        task = next(t for t in runtime.overview('testpilot')['tasks'] if t['name'] == 'Daily')
        self.assertEqual((task['state'], task['nextRun']), ('waiting', '2026-11-12 01:00:00'))
        self.clock = self.midnight + timedelta(seconds=1)
        self.set_zone('NST3:30')
        task = next(t for t in runtime.overview('testpilot')['tasks'] if t['name'] == 'Daily')
        self.assertEqual((task['state'], task['nextRun']), ('pending', '2026-11-11 12:30:00'))

    def test_server_refresh_saved_before_batch_commit_and_zone_change(self):
        config = AzurLaneConfig('testpilot', task='Daily')
        with patch('module.config.utils.server_.server', 'cn'), config.multi_set():
            config.task_delay(server_update=True)
            self.set_zone('JST-9')
        self.assertEqual(self.stored(), str(self.midnight))
        config.get_next_task()
        self.assertEqual(config.Scheduler_NextRun, datetime(2026, 11, 12, 1))

    def test_native_wait_does_not_end_early_when_clock_jumps_forward(self):
        script = AzurLaneAutoScript('testpilot')
        script.__dict__['config'] = Mock(should_reload=Mock(return_value=False))
        self.clock = self.midnight - timedelta(seconds=1)

        def advance(_):
            if self.clock < self.midnight:
                self.set_zone('JST-9', refresh=True)
                self.clock = self.midnight
            else:
                self.clock += timedelta(seconds=2)

        with patch('alas.time.sleep', side_effect=advance) as sleep:
            self.assertTrue(script.wait_until(datetime(2026, 11, 12)))
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(self.clock, self.midnight + timedelta(seconds=2))

    def test_card_wait_keeps_deadline_after_switching_zone(self):
        script = AzurLaneAutoScript('testpilot')
        script.__dict__['config'] = AzurLaneConfig('testpilot')
        document = ProgramDocument(entry='start', nodes=[
            node('start', 'entry'), node('wait', 'wait', seconds=10), node('run', 'execute', task='Daily'),
        ], edges=[edge('start', 'wait'), edge('wait', 'run')])
        with patch('module.scheduler.runtime.ProgramStore'):
            runtime = SchedulerRuntime(script, self.root / 'config')
        runtime.mode = 'takeover'
        runtime.engine = Engine(document)
        runtime.load_program = Mock()
        runtime.save = Mock()
        runtime.emit = Mock()
        runtime.context = Mock(side_effect=lambda: {'now': str(now()), 'tasks': [{'name': 'Daily'}]})
        start = self.clock

        def advance(deadline):
            self.assertLessEqual(deadline, start + timedelta(seconds=10))
            self.assertGreater(deadline, self.clock)
            self.clock = deadline
            if self.clock == start + timedelta(seconds=4):
                self.set_zone('JST-9', refresh=True)
                runtime.recovery_requested = True
            return True

        script.wait_until = Mock(side_effect=advance)
        self.assertEqual(runtime.next_task(), 'Restart')
        self.assertEqual(self.clock, start + timedelta(seconds=4))
        self.assertEqual(runtime.next_task(), 'Daily')
        self.assertEqual(self.clock, start + timedelta(seconds=10))
