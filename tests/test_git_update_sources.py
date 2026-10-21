"""用真实临时 Git 仓库验证双源容灾、目标分支和本地数据保护。"""

import os
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from deploy.config import ExecutionError
from deploy.git import (
    GIT_CONNECT_TIMEOUT, GIT_FETCH_TIMEOUT, GIT_NETWORK_RETRIES,
    GIT_UPDATE_SOURCES, GitManager,
)
from deploy.git_over_cdn.client import GitOverCdnClient


class GitUpdateSourcesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.seed = self.root / 'seed'
        self.seed.mkdir()
        self.git(self.seed, 'init', '-b', 'master')
        self.git(self.seed, 'config', 'user.name', '更新测试')
        self.git(self.seed, 'config', 'user.email', 'test@example.invalid')
        self.git(self.seed, 'config', 'core.hooksPath', str(self.root / 'no-hooks'))
        (self.seed / 'code.txt').write_text('初始版本', encoding='utf-8')
        (self.seed / '.gitignore').write_text('config/\nlog/\n', encoding='utf-8')
        self.git(self.seed, 'add', 'code.txt', '.gitignore')
        self.git(self.seed, 'commit', '-m', '初始提交')
        self.base = self.git(self.seed, 'rev-parse', 'HEAD')
        self.local = self.root / 'local'
        self.git(self.root, 'clone', '--no-hardlinks', str(self.seed), str(self.local))
        self.git(self.local, 'config', 'user.name', '更新测试')
        self.git(self.local, 'config', 'user.email', 'test@example.invalid')
        self.git(self.local, 'config', 'core.hooksPath', str(self.root / 'no-hooks'))
        self.gitcode = self.root / 'gitcode.git'
        self.github = self.root / 'github.git'
        for remote in (self.gitcode, self.github):
            self.git(self.root, 'clone', '--bare', '--no-hardlinks', str(self.seed), str(remote))
        self.redirects = {}
        for (_, url), remote in zip(GIT_UPDATE_SOURCES, (self.gitcode, self.github)):
            self.redirect(url, remote)
        self.manager = object.__new__(GitManager)
        self.manager.git = shutil.which('git')
        self.manager.root_filepath = str(self.local)
        self.manager.Repository = GIT_UPDATE_SOURCES[1][1]
        self.manager.Branch = 'master'
        self.manager.GitProxy = None
        self.manager.SSLVerify = True
        self.manager.GitOverCdn = False
        self.manager._run_git = Mock(wraps=self.manager._run_git)
        self.origin = self.git(self.local, 'remote', 'get-url', 'origin')
        self.logs = patch('deploy.git.logger')
        self.logger = self.logs.start()
        self.addCleanup(self.logs.stop)
        self.sleeps = patch('deploy.git.time.sleep')
        self.sleeps.start()
        self.addCleanup(self.sleeps.stop)

    @staticmethod
    def git(folder, *args):
        result = subprocess.run(
            ['git', *args], cwd=folder, check=True, capture_output=True,
            text=True, encoding='utf-8', errors='replace', timeout=15,
            env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'},
        )
        return result.stdout.strip()

    def redirect(self, url, destination):
        if url in self.redirects:
            self.git(self.local, 'config', '--fixed-value', '--unset-all',
                     f'url.{self.redirects[url].as_uri()}.insteadOf', url)
        self.git(self.local, 'config', '--add', f'url.{destination.as_uri()}.insteadOf', url)
        self.redirects[url] = destination

    def advance(self, *remotes, branch='master'):
        (self.seed / 'code.txt').write_text(f'新版本 {branch}', encoding='utf-8')
        self.git(self.seed, 'add', 'code.txt')
        self.git(self.seed, 'commit', '-m', '更新版本')
        for remote in remotes:
            self.git(self.seed, 'push', str(remote), f'HEAD:refs/heads/{branch}')
        return self.git(self.seed, 'rev-parse', 'HEAD')

    def network_calls(self, command):
        return [call for call in self.manager._run_git.call_args_list if call.args[0] == command]

    def test_gitcode_success_does_not_contact_github_or_cloud(self):
        expected = self.advance(self.gitcode)
        with patch('deploy.git.requests.get', side_effect=requests.exceptions.SSLError('WRONG_VERSION_NUMBER')) as get:
            self.manager.git_install()
        get.assert_not_called()  # dev 已删除云端总开关，失败接口不会成为启动依赖。
        self.assertEqual(expected, self.git(self.local, 'rev-parse', 'HEAD'))
        self.assertEqual([GIT_UPDATE_SOURCES[0][1]], [call.args[-2] for call in self.network_calls('ls-remote')])
        self.assertEqual(1, len(self.network_calls('fetch')))
        self.assertEqual([], self.network_calls('pull'))
        self.assertEqual(self.origin, self.git(self.local, 'remote', 'get-url', 'origin'))
        self.assertEqual(GIT_UPDATE_SOURCES[1][1], self.manager.Repository)

    def test_gitcode_failure_switches_to_github_with_finite_retries(self):
        self.redirect(GIT_UPDATE_SOURCES[0][1], self.root / 'missing.git')
        expected = self.advance(self.github)
        self.manager.git_install()
        probes = self.network_calls('ls-remote')
        self.assertEqual([GIT_UPDATE_SOURCES[0][1]] * GIT_NETWORK_RETRIES + [GIT_UPDATE_SOURCES[1][1]],
                         [call.args[-2] for call in probes])
        self.assertEqual(expected, self.git(self.local, 'rev-parse', 'HEAD'))
        self.assertEqual(self.origin, self.git(self.local, 'remote', 'get-url', 'origin'))

    def test_fetch_failure_after_valid_probe_switches_to_github(self):
        expected = self.advance(self.github)
        original = self.manager._run_git._mock_wraps

        def fail_gitcode_fetch(*args, **kwargs):
            if args[0] == 'fetch' and GIT_UPDATE_SOURCES[0][1] in args:
                raise ExecutionError('TLS handshake failed')
            return original(*args, **kwargs)

        self.manager._run_git.side_effect = fail_gitcode_fetch
        self.manager.git_install()
        self.assertEqual(GIT_NETWORK_RETRIES + 1, len(self.network_calls('fetch')))
        self.assertEqual(expected, self.git(self.local, 'rev-parse', 'HEAD'))

    def test_all_sources_fail_preserving_worktree_and_refs(self):
        for _, url in GIT_UPDATE_SOURCES:
            self.redirect(url, self.root / 'missing.git')
        before_config = (self.local / '.git/config').read_bytes()
        with self.assertRaisesRegex(ExecutionError, '所有 Git 更新源均失败'):
            self.manager.git_install()
        self.assertEqual(2 * GIT_NETWORK_RETRIES, len(self.network_calls('ls-remote')))
        self.assertEqual(self.base, self.git(self.local, 'rev-parse', 'HEAD'))
        self.assertEqual(before_config, (self.local / '.git/config').read_bytes())
        self.assertEqual('', self.git(self.local, 'for-each-ref', 'refs/azurpilot/update'))

    def test_dirty_staged_and_user_config_are_preserved(self):
        self.advance(self.gitcode)
        for kind in ('unstaged', 'staged'):
            with self.subTest(kind=kind):
                (self.local / 'code.txt').write_bytes(b'local edit')
                if kind == 'staged':
                    self.git(self.local, 'add', 'code.txt')
                config = self.local / 'config/deploy.yaml'
                config.parent.mkdir(exist_ok=True)
                config.write_bytes(b'Repository: custom\n')
                status = self.git(self.local, 'status', '--porcelain')
                staged = self.git(self.local, 'diff', '--cached')
                self.manager.git_install()
                self.assertEqual(self.base, self.git(self.local, 'rev-parse', 'HEAD'))
                self.assertEqual(status, self.git(self.local, 'status', '--porcelain'))
                self.assertEqual(staged, self.git(self.local, 'diff', '--cached'))
                self.assertEqual(b'Repository: custom\n', config.read_bytes())
                self.assertEqual(b'local edit', (self.local / 'code.txt').read_bytes())

    def test_untracked_launcher_does_not_block_safe_fast_forward(self):
        expected = self.advance(self.gitcode)
        (self.local / 'AzurPilot.exe').write_bytes(b'local launcher')
        self.manager.git_install()
        self.assertEqual(expected, self.git(self.local, 'rev-parse', 'HEAD'))
        self.assertEqual(b'local launcher', (self.local / 'AzurPilot.exe').read_bytes())

    def test_untracked_collision_is_not_overwritten(self):
        (self.local / 'custom.txt').write_bytes(b'user file')
        (self.seed / 'custom.txt').write_bytes(b'remote file')
        self.git(self.seed, 'add', 'custom.txt')
        self.git(self.seed, 'commit', '-m', '模拟未跟踪文件冲突')
        self.git(self.seed, 'push', str(self.gitcode), 'master')
        with self.assertRaises(ExecutionError):
            self.manager.git_install()
        self.assertEqual(self.base, self.git(self.local, 'rev-parse', 'HEAD'))
        self.assertEqual(b'user file', (self.local / 'custom.txt').read_bytes())

    def test_git_command_timeout_terminates_instead_of_hanging(self):
        self.manager.git = sys.executable
        with self.assertRaisesRegex(ExecutionError, 'Git 操作超时'):
            self.manager._run_git('-c', 'import time; time.sleep(60)', timeout=0.2)

    def test_first_install_in_empty_directory_uses_configured_branch(self):
        empty = self.root / 'empty'
        empty.mkdir()
        self.manager.root_filepath = str(empty)
        with patch.object(GitManager, '_update_sources', return_value=(('GitCode', str(self.gitcode)),)):
            self.manager.git_install()
        self.assertEqual('master', self.git(empty, 'branch', '--show-current'))
        self.assertEqual(self.base, self.git(empty, 'rev-parse', 'HEAD'))
        self.assertEqual('初始版本', (empty / 'code.txt').read_text(encoding='utf-8'))

    def run_installer(self):
        """保留真实安装器调用链，仅替换环境安装和进程操作。"""
        self.stages = []
        stubs = {'deploy.patch': types.SimpleNamespace(pre_checks=lambda: None)}
        for name, class_name, method in (
            ('adb', 'AdbManager', 'adb_install'), ('alas', 'AlasManager', 'alas_kill'),
            ('app', 'AppManager', 'app_update'), ('pip', 'PipManager', 'pip_install'),
        ):
            stub = types.ModuleType(f'deploy.{name}')
            setattr(stub, class_name, type(class_name, (), {
                method: lambda _self, _stage=method: self.stages.append(_stage),
            }))
            stubs[f'deploy.{name}'] = stub
        spec = importlib.util.spec_from_file_location(
            '_test_git_installer', Path(__file__).resolve().parents[1] / 'deploy/installer.py',
        )
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, stubs), patch('deploy.atomic.atomic_failure_cleanup'):
            spec.loader.exec_module(module)
            installer = object.__new__(module.Installer)
            installer.__dict__.update(self.manager.__dict__)
            installer.install()

    def test_github_fallback_continues_deployment(self):
        self.redirect(GIT_UPDATE_SOURCES[0][1], self.root / 'missing.git')
        expected = self.advance(self.github)
        self.run_installer()
        self.assertEqual(expected, self.git(self.local, 'rev-parse', 'HEAD'))
        self.assertEqual(['alas_kill', 'pip_install', 'app_update', 'adb_install'], self.stages)

    def test_exhausted_sources_stop_deployment_once(self):
        for _, url in GIT_UPDATE_SOURCES:
            self.redirect(url, self.root / 'missing.git')
        with self.assertRaises(SystemExit) as stopped:
            self.run_installer()
        self.assertEqual(1, stopped.exception.code)
        self.assertEqual([], self.stages)
        self.assertEqual(2 * GIT_NETWORK_RETRIES, len(self.network_calls('ls-remote')))

    def test_unrelated_mirror_history_falls_back_without_reset(self):
        self.git(self.gitcode, 'update-ref', '-d', 'refs/heads/master')
        unrelated = self.root / 'unrelated'
        unrelated.mkdir()
        self.git(unrelated, 'init', '-b', 'master')
        self.git(unrelated, 'config', 'user.name', '镜像测试')
        self.git(unrelated, 'config', 'user.email', 'test@example.invalid')
        self.git(unrelated, 'commit', '--allow-empty', '-m', '独立历史')
        self.git(unrelated, 'push', str(self.gitcode), 'master')
        expected = self.advance(self.github)
        self.manager.git_install()
        self.assertEqual(expected, self.git(self.local, 'rev-parse', 'HEAD'))
        self.assertEqual(2, len(self.network_calls('fetch')))

    def test_stale_mirror_falls_back_to_github(self):
        expected = self.advance(self.github)
        self.git(self.local, 'fetch', str(self.github), 'master')
        self.git(self.local, 'merge', '--ff-only', str(expected))
        self.manager.git_install()
        self.assertEqual(expected, self.git(self.local, 'rev-parse', 'HEAD'))
        self.assertEqual(2, len(self.network_calls('fetch')))

    def test_local_custom_commit_is_not_discarded(self):
        self.git(self.local, 'commit', '--allow-empty', '-m', '用户自定义提交')
        local = self.git(self.local, 'rev-parse', 'HEAD')
        self.advance(self.gitcode, self.github)
        with self.assertRaisesRegex(ExecutionError, '历史分叉'):
            self.manager.git_install()
        self.assertEqual(local, self.git(self.local, 'rev-parse', 'HEAD'))
        self.assertEqual('用户自定义提交', self.git(self.local, 'log', '-1', '--format=%s'))

    def test_configured_branch_with_different_history_is_selected(self):
        self.git(self.seed, 'checkout', '--orphan', 'dev')
        self.git(self.seed, 'rm', '-rf', '.')
        (self.seed / 'dev.txt').write_bytes(b'dev branch')
        self.git(self.seed, 'add', 'dev.txt')
        self.git(self.seed, 'commit', '-m', 'dev 独立历史')
        self.git(self.seed, 'push', str(self.gitcode), 'dev')
        expected = self.git(self.seed, 'rev-parse', 'HEAD')
        self.manager.Branch = 'dev'
        self.manager.git_install()
        self.assertEqual('dev', self.git(self.local, 'branch', '--show-current'))
        self.assertEqual(expected, self.git(self.local, 'rev-parse', 'HEAD'))
        self.assertEqual(self.base, self.git(self.local, 'rev-parse', 'refs/heads/master'))

    def test_custom_repository_is_not_replaced_by_official_sources(self):
        self.manager.Repository = str(self.github)
        expected = self.advance(self.github)
        self.manager.git_install()
        self.assertEqual([str(self.github)], [call.args[-2] for call in self.network_calls('ls-remote')])
        self.assertEqual(expected, self.git(self.local, 'rev-parse', 'HEAD'))

    def test_missing_branch_never_uses_stale_remote_tracking_ref(self):
        self.manager.Branch = 'missing-branch'
        self.git(self.local, 'update-ref', 'refs/remotes/origin/missing-branch', self.base)
        with self.assertRaises(ExecutionError):
            self.manager.git_install()
        self.assertEqual('master', self.git(self.local, 'branch', '--show-current'))
        self.assertEqual(self.base, self.git(self.local, 'rev-parse', 'HEAD'))

    def test_ignored_user_file_collision_aborts_without_overwriting(self):
        user_file = self.local / 'config/deploy.yaml'
        user_file.parent.mkdir()
        user_file.write_bytes(b'user config')
        upstream = self.seed / 'config/deploy.yaml'
        upstream.parent.mkdir()
        upstream.write_bytes(b'remote config')
        self.git(self.seed, 'add', '-f', 'config/deploy.yaml')
        self.git(self.seed, 'commit', '-m', '模拟上游同名文件')
        self.git(self.seed, 'push', str(self.gitcode), 'master')
        with self.assertRaises(ExecutionError):
            self.manager.git_install()
        self.assertEqual(b'user config', user_file.read_bytes())
        self.assertEqual(self.base, self.git(self.local, 'rev-parse', 'HEAD'))

    def test_cdn_uses_same_safe_apply_callback(self):
        expected = self.advance(self.gitcode)
        self.git(self.local, 'fetch', str(self.gitcode), 'master')
        client = GitOverCdnClient('https://cdn.example', str(self.local), git=self.manager.git)
        client.current_commit = self.base
        client.latest_commit = expected
        client.download_pack = Mock(return_value=True)
        self.manager.GitOverCdn = True
        with patch.object(GitManager, 'goc_client', client), patch.object(client, 'git_reset') as reset:
            self.manager.git_install()
        reset.assert_not_called()
        self.assertEqual(expected, self.git(self.local, 'rev-parse', 'HEAD'))

    def test_timeouts_and_proxy_options_are_command_scoped(self):
        self.manager.GitProxy = 'http://127.0.0.1:7890'
        before = (self.local / '.git/config').read_bytes()
        self.manager.git_install()
        probe = self.network_calls('ls-remote')[0]
        fetch = self.network_calls('fetch')[0]
        self.assertEqual(GIT_CONNECT_TIMEOUT, probe.kwargs['timeout'])
        self.assertEqual(GIT_FETCH_TIMEOUT, fetch.kwargs['timeout'])
        self.assertIn('http.proxy=http://127.0.0.1:7890', fetch.kwargs['options'])
        self.assertIn('http.sslVerify=true', fetch.kwargs['options'])
        self.assertEqual(before, (self.local / '.git/config').read_bytes())


class CloudForcePolicyTest(unittest.TestCase):
    def test_valid_force_policy_and_invalid_responses(self):
        for body, expected in ((b'true', True), (b'false', False), (b'ture', True),
                               (b'fales', False), (b'{}', None), (b'<html>error</html>', None)):
            with self.subTest(body=body):
                response = requests.Response()
                response.status_code = 200
                response._content = body
                with patch('deploy.git.requests.get', return_value=response) as get:
                    self.assertIs(expected, GitManager.cloud_force_update_enabled())
                get.assert_called_once()

    def test_cloud_transport_and_http_failures_are_recoverable(self):
        errors = (requests.exceptions.SSLError('WRONG_VERSION_NUMBER'),
                  requests.ConnectionError('DNS failure'), requests.Timeout('timeout'),
                  requests.HTTPError('503 Service Unavailable'))
        for error in errors:
            with self.subTest(error=error), patch('deploy.git.requests.get', side_effect=error) as get:
                self.assertIsNone(GitManager.cloud_force_update_enabled())
                get.assert_called_once()


if __name__ == '__main__':
    unittest.main()
