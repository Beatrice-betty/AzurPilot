"""云端三态、重定向故障与启动本地回退验证。"""
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, PropertyMock, patch

import requests

from deploy.config import ExecutionError
from deploy.git import CLOUD_UPDATE_CONTROL_URL, GitManager


class CloudUpdateTest(unittest.TestCase):
    def test_both_switches_preserve_three_states_and_legacy_spelling(self):
        for method in (GitManager.cloud_auto_update_enabled, GitManager.cloud_force_update_enabled):
            for body, expected in (
                ('true', True), ('false', False), ('"TRUE"', True), ('ture', True),
                ('fales', False), ('null', None), ('1', None), ('{}', None),
                ('<html>error</html>', None), ('', None),
            ):
                with self.subTest(method=method.__name__, body=body):
                    response = requests.Response()
                    response.status_code = 200
                    response._content = body.encode()
                    with patch('deploy.git.requests.get', return_value=response) as get:
                        self.assertIs(expected, method())
                    self.assertEqual(1, get.call_count)
                    self.assertFalse(get.call_args.kwargs['allow_redirects'])

    def test_network_errors_are_unknown_and_attempted_once(self):
        for method in (GitManager.cloud_auto_update_enabled, GitManager.cloud_force_update_enabled):
            for error in (
                requests.Timeout('request timed out'),
                requests.ConnectionError('DNS getaddrinfo failed'),
                requests.exceptions.SSLError('TLS handshake failed'),
                requests.TooManyRedirects('redirect loop'),
            ):
                with self.subTest(method=method.__name__, error=error):
                    with patch('deploy.git.requests.get', side_effect=error) as get, patch('deploy.git.logger.warning') as warn:
                        self.assertIsNone(method())
                    get.assert_called_once()
                    self.assertIn(str(error), warn.call_args.args[0])

    def test_http_errors_and_redirects_are_unknown(self):
        for status in (301, 302, 307, 308, 403, 404, 500, 503):
            with self.subTest(status=status):
                response = requests.Response()
                response.status_code = status
                response._content = b'true'
                response.headers['Location'] = CLOUD_UPDATE_CONTROL_URL
                with patch('deploy.git.requests.get', return_value=response), patch('deploy.git.logger.warning') as warn:
                    self.assertIsNone(GitManager.cloud_auto_update_enabled())
                self.assertIn(str(status), warn.call_args.args[0])

    def test_real_self_redirect_sends_only_one_request(self):
        calls = []

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append(self.path)
                self.send_response(301)
                self.send_header('Location', f'http://127.0.0.1:{self.server.server_port}{self.path}')
                self.send_header('Content-Length', '0')
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), RedirectHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch('deploy.git.CLOUD_UPDATE_CONTROL_URL', f'http://127.0.0.1:{server.server_port}/api/updata'):
                self.assertIsNone(GitManager.cloud_auto_update_enabled())
            self.assertEqual(['/api/updata'], calls)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


class GitInstallTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.manager = object.__new__(GitManager)
        self.manager.root_filepath = str(self.root)
        self.manager.GitOverCdn = False
        for name in ('gui.py', 'alas.py', 'pyproject.toml', 'uv.lock', 'module/api/app.py'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()

    def test_unknown_or_disabled_skips_git_and_cdn(self):
        for state in (None, False):
            with self.subTest(state=state), patch.object(self.manager, 'cloud_auto_update_enabled', return_value=state) as cloud, patch.object(self.manager, 'git_repository_init') as update, patch.object(GitManager, 'goc_client', new_callable=PropertyMock) as cdn:
                self.manager.git_install()
                cloud.assert_called_once()
                update.assert_not_called()
                cdn.assert_not_called()

    def test_unknown_is_not_logged_as_server_disabled(self):
        with patch.object(self.manager, 'cloud_auto_update_enabled', return_value=None), patch('deploy.git.logger.info') as info:
            self.manager.git_install()
        self.assertFalse(any('disabled' in str(call) for call in info.call_args_list))

    def test_enabled_runs_original_git_update(self):
        with patch.object(self.manager, 'cloud_auto_update_enabled', return_value=True), patch.object(self.manager, 'git_repository_init') as update:
            self.manager.git_install()
        update.assert_called_once()

    def test_enabled_runs_cdn_first_and_falls_back_to_git(self):
        self.manager.GitOverCdn = True
        for success in (True, False):
            with self.subTest(cdn_success=success), patch.object(self.manager, 'cloud_auto_update_enabled', return_value=True), patch.object(GitManager, 'goc_client', new_callable=PropertyMock) as cdn, patch.object(self.manager, 'git_repository_init') as update:
                cdn.return_value.update.return_value = success
                self.manager.git_install()
                cdn.return_value.update.assert_called_once()
                self.assertEqual(0 if success else 1, update.call_count)

    def test_network_failures_fall_back_without_retries(self):
        for error in (requests.Timeout('timeout'), requests.ConnectionError('DNS failed'), requests.exceptions.SSLError('SSL failed')):
            with self.subTest(error=error), patch('deploy.git.requests.get', side_effect=error) as get, patch.object(self.manager, 'git_repository_init') as update:
                self.manager.git_install()
                get.assert_called_once()
                update.assert_not_called()

    def test_missing_source_has_actionable_error(self):
        (self.root / 'gui.py').unlink()
        with patch.object(self.manager, 'cloud_auto_update_enabled', return_value=None):
            with self.assertRaisesRegex(ExecutionError, 'gui.py'):
                self.manager.git_install()

    def test_installer_continues_after_cloud_failure(self):
        with patch('deploy.patch.pre_checks'):
            from deploy.installer import Installer
        installer = object.__new__(Installer)
        installer.root_filepath = str(self.root)
        installer.cloud_auto_update_enabled = Mock(return_value=None)
        stages = []
        for name in ('alas_kill', 'pip_install', 'app_update', 'adb_install'):
            setattr(installer, name, Mock(side_effect=lambda step=name: stages.append(step)))
        with patch('deploy.atomic.atomic_failure_cleanup'):
            installer.install()
        self.assertEqual(['alas_kill', 'pip_install', 'app_update', 'adb_install'], stages)
        installer.cloud_auto_update_enabled.assert_called_once()


if __name__ == '__main__':
    unittest.main()