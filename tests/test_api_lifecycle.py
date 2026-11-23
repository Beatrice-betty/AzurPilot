"""真实共享进程与 ASGI 生命周期的集成回归，隔离所有游戏及网络任务。"""
import os
import tempfile
import threading
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch, mock_open

from starlette.testclient import TestClient

from module.api.app import create_app
from module.api import lifecycle
from module.runtime.setting import State
from module.runtime.task_handler import TaskHandler
from tests.test_api import fixture


class ApiLifecycleTests(unittest.TestCase):
    def test_destroyed_vault_does_not_abort_webui_lifespan(self):
        self.run_lifecycle(fail_account=True)

    def test_independent_webui_starts_and_keeps_config_accessible_with_invalid_database(self):
        self.run_lifecycle(invalid_database=True)

    def test_independent_webui_with_new_empty_recovery_waits_for_manual_start(self):
        self.run_lifecycle(missing_database=True)

    def run_lifecycle(self, fail_start=False, fail_discord=False, fail_discord_start=False, fail_account=False,
                      invalid_database=False, missing_database=False):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = fixture(directory)
            executed = threading.Event()
            database = root / 'config/azurpilot.db'
            if invalid_database or missing_database:
                stack.enter_context(patch.dict(os.environ, {'AZURPILOT_INDEPENDENT': '1'}))
            if invalid_database:
                database.write_bytes(b'invalid SQLite database')
            if missing_database:
                (database.parent / 'azurpilot.migrated').write_text('2\noriginal\n', encoding='utf-8')

            def schedule():
                yield
                while True:
                    executed.set()
                    yield

            settings = SimpleNamespace(Run='', StartOcrServer=False, EnableRemoteAccess=False,
                                       DiscordRichPresence=fail_discord_start)
            tasks = TaskHandler()
            stack.enter_context(patch.object(State, '_deploy_config_', settings, create=True))
            stack.enter_context(patch.multiple(State, _init=False, _clearup=False, manager=None,
                                               process_registry=None, _restart_requested=False))
            stack.enter_context(patch('module.runtime.worker_registry.WORKER_REGISTRY_FILE', root / 'workers.json'))
            stack.enter_context(patch('module.runtime.worker_registry.LEGACY_WORKER_REGISTRY_FILE', root / 'old.json'))
            stack.enter_context(patch('module.runtime.updater.updater', SimpleNamespace(delay=0, schedule_update=schedule)))
            stack.enter_context(patch.object(lifecycle, 'task_handler', tasks))
            # 禁止读取用户的自动运行列表或接触设备进程；Manager 与调度线程真实运行。
            if fail_account:
                from module.api.protocol import ApiError
                broken = Mock(spec=lifecycle.ProcessManager, config_name='broken')
                healthy = Mock(spec=lifecycle.ProcessManager, config_name='healthy')
                broken.start.side_effect = ApiError('VAULT_DESTROYED', '合成旧版标记')
                stack.enter_context(patch.object(lifecycle.ProcessManager, 'get_manager',
                                               side_effect=lambda name: {'broken': broken, 'healthy': healthy}[name]))
                stack.enter_context(patch('module.runtime.process_manager.list_mod_instance'))
                stack.enter_context(patch('module.runtime.process_manager.get_config_mod', return_value='alas'))
                stack.enter_context(patch('module.runtime.process_manager.open',
                                         mock_open(read_data='broken\nhealthy\n'), create=True))
                stack.enter_context(patch('module.runtime.process_manager.os.remove'))
            else:
                restart = stack.enter_context(patch.object(lifecycle.ProcessManager, 'restart_processes'))
            stack.enter_context(patch.object(lifecycle.ProcessManager, 'running_instances', return_value=[]))
            if fail_discord:
                stack.enter_context(patch('module.runtime.discord_presence.async_close_discord_rpc',
                                          new=AsyncMock(side_effect=RuntimeError('模拟 Discord 清理失败'))))
            if fail_discord_start:
                stack.enter_context(patch('module.runtime.discord_presence.init_discord_rpc',
                                          side_effect=RuntimeError('模拟 Discord 启动失败')))
            if fail_start:
                stack.enter_context(patch.object(tasks, 'start', side_effect=RuntimeError('模拟启动失败')))
            app = create_app(root=root, password='', mount_mcp=False)
            if fail_start:
                with self.assertRaisesRegex(RuntimeError, '模拟启动失败'), TestClient(app):
                    pass
            else:
                with TestClient(app) as client:
                    self.assertTrue(executed.wait(timeout=2))
                    self.assertIsNotNone(State.manager)
                    self.assertEqual(200, client.get('/healthz').status_code)
                    with client.websocket_connect('/api/v1/ws') as socket:
                        self.assertEqual('session', socket.receive_json()['topic'])
                        socket.send_json({'v': 1, 'type': 'request', 'id': 'smoke',
                                          'method': 'system.ping', 'params': {}})
                        self.assertEqual({'pong': True}, socket.receive_json()['result'])
                        if invalid_database:
                            for method in ('config.get', 'overview.get', 'scheduler.start'):
                                socket.send_json({'v': 1, 'type': 'request', 'id': method, 'method': method,
                                                  'params': {'instance': 'testpilot'}})
                                response = socket.receive_json()
                                if method == 'scheduler.start':
                                    self.assertEqual(response['error']['code'], 'STORAGE_UNAVAILABLE')
                                else:
                                    self.assertTrue(response['ok'])
                                    if method == 'overview.get':
                                        self.assertIn('storageNotice', response['result'])
                            self.assertEqual(database.read_bytes(), b'invalid SQLite database')
                        if missing_database:
                            socket.send_json({'v': 1, 'type': 'request', 'id': 'overview', 'method': 'overview.get',
                                              'params': {'instance': 'testpilot'}})
                            self.assertIn('手动启动', socket.receive_json()['result']['storageNotice'])
                if invalid_database or missing_database:
                    restart.assert_not_called()
            self.assertIsNone(State.manager)
            self.assertTrue(State._clearup)
            self.assertFalse(tasks._thread and tasks._thread.is_alive())

    def test_real_manager_and_background_scheduler_start_and_stop(self):
        self.run_lifecycle()

    def test_partial_startup_failure_releases_manager(self):
        self.run_lifecycle(fail_start=True)

    def test_discord_cleanup_failure_releases_real_manager(self):
        self.run_lifecycle(fail_discord=True)

    def test_discord_startup_failure_keeps_webui_available(self):
        self.run_lifecycle(fail_discord_start=True)
