"""云端三态、本地回退与真实临时 Git 仓库的非破坏性更新验证。"""
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, PropertyMock, patch

import requests

from deploy.config import ExecutionError
from deploy.git import CLOUD_UPDATE_CONTROL_URL, GitManager
from deploy.git_over_cdn.client import GitOverCdnClient
from deploy.uv import venv_python


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
        environment = patch.dict(os.environ, {'UV_PROJECT_ENVIRONMENT': str(self.root / '.venv')})
        environment.start()
        self.addCleanup(environment.stop)
        self.manager = object.__new__(GitManager)
        self.manager.root_filepath = str(self.root)
        self.manager.git = 'git'
        self.manager.Branch = 'dev'
        self.manager.Repository = 'https://example.invalid/repo'
        self.manager.GitOverCdn = False
        for name in (
            'gui.py', 'alas.py', 'pyproject.toml', 'uv.lock', 'module/api/app.py',
            'module/runtime/process_manager.py', 'module/config/argument/args.json',
            'frontend/package.json',
        ):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        python = venv_python(self.root)
        python.parent.mkdir(parents=True, exist_ok=True)
        python.touch()
        self.probe = patch('deploy.git.subprocess.run', return_value=Mock(returncode=0, stdout='', stderr=''))
        self.run = self.probe.start()
        self.addCleanup(self.probe.stop)

    def test_unknown_or_disabled_uses_local_without_git_or_cdn(self):
        for state in (None, False):
            with self.subTest(state=state), patch.object(self.manager, 'cloud_auto_update_enabled', return_value=state) as cloud, patch.object(self.manager, 'git_repository_init') as update, patch.object(GitManager, 'goc_client', new_callable=PropertyMock) as cdn:
                self.assertFalse(self.manager.git_install())
                cloud.assert_called_once()
                update.assert_not_called()
                cdn.assert_not_called()

    def test_enabled_runs_existing_git_update_path(self):
        with patch.object(self.manager, 'cloud_auto_update_enabled', return_value=True), patch.object(self.manager, 'git_repository_init') as update:
            self.assertTrue(self.manager.git_install())
        update.assert_called_once()

    def test_enabled_runs_cdn_before_git(self):
        self.manager.GitOverCdn = True
        with patch.object(self.manager, 'cloud_auto_update_enabled', return_value=True), patch.object(GitManager, 'goc_client', new_callable=PropertyMock) as cdn, patch.object(self.manager, 'git_repository_init') as update:
            cdn.return_value.update.return_value = True
            self.assertTrue(self.manager.git_install())
            cdn.return_value.update.assert_called_once()
            update.assert_not_called()

    def test_manual_update_does_not_consult_cloud(self):
        with patch.object(self.manager, 'cloud_auto_update_enabled', side_effect=AssertionError('不能请求云端')), patch.object(self.manager, 'git_repository_init') as update:
            self.assertTrue(self.manager.git_install(manual=True))
        update.assert_called_once()

    def test_network_failures_fall_back_without_retries(self):
        for error in (requests.Timeout('timeout'), requests.ConnectionError('DNS failed'), requests.exceptions.SSLError('SSL failed')):
            with self.subTest(error=error), patch('deploy.git.requests.get', side_effect=error) as get, patch.object(self.manager, 'git_repository_init') as update:
                self.assertFalse(self.manager.git_install())
                get.assert_called_once()
                update.assert_not_called()

    def test_local_fallback_with_real_python_dependency_probe(self):
        self.probe.stop()
        with patch('deploy.uv.venv_python', return_value=Path(sys.executable)), patch('deploy.git.requests.get', side_effect=requests.Timeout('timeout')) as get:
            self.assertFalse(self.manager.git_install())
        get.assert_called_once()

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

    def test_dirty_worktree_blocks_manual_and_auto_updates(self):
        for manual in (False, True):
            with self.subTest(manual=manual), patch.object(self.manager, 'cloud_auto_update_enabled', return_value=True), patch.object(self.manager, 'local_changes_present', return_value=True), patch.object(self.manager, 'git_repository_init') as update, patch.object(GitManager, 'goc_client', new_callable=PropertyMock) as cdn:
                self.assertFalse(self.manager.git_install(manual=manual))
                update.assert_not_called()
                cdn.assert_not_called()

    def test_missing_source_has_actionable_error(self):
        (self.root / 'gui.py').unlink()
        with patch.object(self.manager, 'cloud_auto_update_enabled', return_value=None):
            with self.assertRaisesRegex(ExecutionError, 'gui.py'):
                self.manager.git_install()
        self.run.assert_not_called()

    def test_missing_interpreter_has_actionable_error(self):
        venv_python(self.root).unlink()
        with self.assertRaisesRegex(ExecutionError, 'Python 环境缺失'):
            self.manager.use_local_version()

    def test_broken_dependencies_and_probe_timeout_are_not_success(self):
        for failure in (Mock(returncode=1, stderr='ModuleNotFoundError: uvicorn'), subprocess.TimeoutExpired('python', 20)):
            with self.subTest(failure=failure):
                self.run.side_effect = failure if isinstance(failure, Exception) else None
                self.run.return_value = failure
                with self.assertRaisesRegex(ExecutionError, '本地启动环境校验失败'):
                    self.manager.use_local_version()


class SafeGitUpdateTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.manager = object.__new__(GitManager)
        self.manager.root_filepath = str(self.root)
        self.manager.git = 'git'
        self.manager.use_local_version = Mock(return_value=False)
        self.git('init', '-b', 'dev')
        self.git('config', 'user.name', '测试作者')
        self.git('config', 'user.email', 'test@example.invalid')
        (self.root / 'file').write_text('base')
        self.git('add', 'file')
        self.git('commit', '-m', '初始版本')
        self.base = self.git('rev-parse', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/dev', self.base)

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, check=True, capture_output=True,
                              text=True, encoding='utf-8', errors='replace').stdout.strip()

    def test_dirty_detection_covers_staged_and_unstaged_files(self):
        self.assertFalse(self.manager.local_changes_present())
        (self.root / 'file').write_text('user edits')
        self.assertTrue(self.manager.local_changes_present())
        self.git('add', 'file')
        self.assertTrue(self.manager.local_changes_present())

    def test_untracked_runtime_artifacts_do_not_block_updates(self):
        (self.root / 'launcher.exe').touch()
        self.assertFalse(self.manager.local_changes_present())

    def test_cdn_fast_forward_preserves_local_commit_when_target_diverges(self):
        self.git('commit', '--allow-empty', '-m', '本地私有提交')
        local = self.git('rev-parse', 'HEAD')
        client = GitOverCdnClient('https://example.invalid', folder=str(self.root), branch='dev', git='git')
        self.assertFalse(client.git_reset())
        self.assertEqual(local, self.git('rev-parse', 'HEAD'))

    def test_cdn_fast_forward_preserves_uncommitted_file_and_lock(self):
        (self.root / 'file').write_text('upstream')
        self.git('commit', '-am', '上游更新')
        remote = self.git('rev-parse', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/dev', remote)
        self.git('checkout', '--detach', self.base)
        (self.root / 'file').write_text('user edits')
        client = GitOverCdnClient('https://example.invalid', folder=str(self.root), branch='dev', git='git')
        self.assertFalse(client.git_reset())
        self.assertEqual('user edits', (self.root / 'file').read_text())
        self.assertEqual(self.base, self.git('rev-parse', 'HEAD'))
        lock = self.root / '.git/index.lock'
        lock.touch()
        self.assertFalse(client.git_reset())
        self.assertTrue(lock.exists())

    def test_cdn_fast_forward_applies_clean_update(self):
        (self.root / 'file').write_text('upstream')
        self.git('commit', '-am', '上游更新')
        remote = self.git('rev-parse', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/dev', remote)
        self.git('checkout', '--detach', self.base)
        client = GitOverCdnClient('https://example.invalid', folder=str(self.root), branch='dev', git='git')
        self.assertTrue(client.git_reset())
        self.assertEqual(remote, self.git('rev-parse', 'HEAD'))

    def test_git_update_preserves_private_commits_without_reset_or_pull(self):
        self.git('commit', '--allow-empty', '-m', '本地私有提交')
        local = self.git('rev-parse', 'HEAD')
        commands = []

        def execute(command, allow_failure=False):
            commands.append(command)
            result = subprocess.run(command, shell=True, cwd=self.root, capture_output=True)
            if result.returncode and not allow_failure:
                raise ExecutionError(command)
            return result.returncode == 0

        self.manager.execute = execute
        self.manager._fetch_with_retry = Mock()
        self.assertFalse(self.manager.git_repository_init(str(self.root), branch='dev'))
        self.assertEqual(local, self.git('rev-parse', 'HEAD'))
        self.assertFalse(any('reset --hard' in command or ' pull ' in command for command in commands))

    def test_untracked_collision_is_preserved_by_cdn_update(self):
        (self.root / 'new.py').write_text('upstream')
        self.git('add', 'new.py')
        self.git('commit', '-m', '新增代码')
        remote = self.git('rev-parse', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/dev', remote)
        self.git('checkout', '--detach', self.base)
        (self.root / 'new.py').write_text('user code')
        client = GitOverCdnClient('https://example.invalid', folder=str(self.root), branch='dev', git='git')
        self.assertFalse(client.git_reset())
        self.assertEqual('user code', (self.root / 'new.py').read_text())
        self.assertEqual(self.base, self.git('rev-parse', 'HEAD'))

    def test_first_install_can_fetch_and_merge_without_hard_reset(self):
        destination = self.root / 'installation'
        destination.mkdir()
        manager = object.__new__(GitManager)
        manager.root_filepath = str(destination)
        manager.git = 'git'
        commands = []

        def execute(command, allow_failure=False):
            commands.append(command)
            result = subprocess.run(command, shell=True, cwd=destination, capture_output=True)
            if result.returncode and not allow_failure:
                raise ExecutionError(command)
            return result.returncode == 0

        manager.execute = execute
        manager.git_repository_init(str(self.root), branch='dev')
        self.assertEqual('base', (destination / 'file').read_text())
        self.assertFalse(any('reset --hard' in command for command in commands))


if __name__ == '__main__':
    unittest.main()
