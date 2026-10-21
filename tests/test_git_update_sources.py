"""离线验证原有 UA 重试、国内镜像切换与 GitHub 兜底。"""

import unittest
from unittest.mock import Mock, call, patch

import requests

from deploy.config import GITHUB_REPOSITORY, ExecutionError
from deploy.git import GitManager


GITCODE = 'https://gitcode.com/ddl2/AzurPilot'
CNB = 'https://cnb.cool/AzurPilot/AzurPilot'
GITHUB = f'{GITHUB_REPOSITORY}.git'


class GitFetchFallbackTest(unittest.TestCase):
    def setUp(self):
        self.manager = object.__new__(GitManager)
        self.manager.git = 'git'
        self.manager.Repository = GITCODE
        self.current_url = GITCODE
        self.fetches = []
        self.switches = []
        self.success_at = {}
        self.manager.git_user_agent = Mock(side_effect=(f'git/2.52.{i}' for i in range(100)))
        self.manager.get_remote_url = Mock(side_effect=lambda source: self.current_url)
        self.manager.execute = Mock(side_effect=self.execute)
        self.sleeps = patch('deploy.git.time.sleep')
        self.sleep = self.sleeps.start()
        self.addCleanup(self.sleeps.stop)
        self.logs = patch('deploy.git.logger')
        self.logs.start()
        self.addCleanup(self.logs.stop)

    def execute(self, command, allow_failure=False):
        if ' remote set-url ' in command:
            self.current_url = command.rsplit(' ', 1)[1]
            self.switches.append(self.current_url)
            return True
        if ' fetch ' in command:
            self.assertTrue(allow_failure, '单次 fetch 失败必须返回重试流程')
            self.fetches.append((self.current_url, command))
            attempts = sum(url == self.current_url for url, _ in self.fetches)
            return attempts == self.success_at.get(self.current_url)
        self.fail(f'非预期命令：{command}')

    def test_primary_success_does_not_switch_source(self):
        self.success_at[GITCODE] = 1
        self.manager._fetch_with_retry('origin', 'master')
        self.assertEqual([GITCODE], [url for url, _ in self.fetches])
        self.assertEqual([], self.switches)
        self.sleep.assert_not_called()

    def test_failed_fetch_retries_with_another_user_agent(self):
        self.success_at[GITCODE] = 2
        self.manager._fetch_with_retry('origin', 'master')
        self.assertEqual([GITCODE, GITCODE], [url for url, _ in self.fetches])
        self.assertNotEqual(self.fetches[0][1], self.fetches[1][1])
        self.assertEqual([], self.switches)
        self.sleep.assert_called_once()

    def test_original_peer_mirror_still_precedes_github(self):
        self.success_at[CNB] = 1
        self.manager._fetch_with_retry('origin', 'master')
        self.assertEqual([GITCODE] * 5 + [CNB], [url for url, _ in self.fetches])
        self.assertEqual([CNB], self.switches)

    def test_github_is_tried_after_both_domestic_mirrors(self):
        self.success_at[GITHUB] = 1
        self.manager._fetch_with_retry('origin', 'dev')
        self.assertEqual([GITCODE] * 5 + [CNB] * 5 + [GITHUB], [url for url, _ in self.fetches])
        self.assertEqual([CNB, GITHUB], self.switches)
        self.assertEqual(GITHUB, self.manager.Repository)
        self.assertTrue(all(command.endswith(' fetch origin dev') for _, command in self.fetches))

    def test_cnb_primary_preserves_original_source_order(self):
        self.current_url = self.manager.Repository = CNB
        self.success_at[GITHUB] = 1
        self.manager._fetch_with_retry('origin', 'master', max_retry=2)
        self.assertEqual([CNB] * 2 + [GITCODE] * 2 + [GITHUB], [url for url, _ in self.fetches])

    def test_all_sources_exhaust_original_retry_count(self):
        with self.assertRaises(ExecutionError):
            self.manager._fetch_with_retry('origin', 'master')
        self.assertEqual([GITCODE] * 5 + [CNB] * 5 + [GITHUB] * 5,
                         [url for url, _ in self.fetches])
        self.assertEqual([CNB, GITHUB], self.switches)

    def test_custom_repository_is_not_replaced(self):
        self.current_url = self.manager.Repository = 'https://example.invalid/custom.git'
        with self.assertRaises(ExecutionError):
            self.manager._fetch_with_retry('origin', 'master')
        self.assertEqual(5, len(self.fetches))
        self.assertEqual([], self.switches)

    def test_successful_fetch_keeps_original_reset_and_pull(self):
        self.manager._fetch_with_retry = Mock()
        self.manager.execute = Mock(return_value=True)
        with patch('deploy.git.os.path.exists', return_value=False):
            self.manager.git_repository_init(GITCODE, branch='dev', proxy='http://proxy:8080')
        commands = [item.args[0] for item in self.manager.execute.call_args_list]
        reset = next(i for i, command in enumerate(commands) if ' reset --hard origin/dev' in command)
        pull = next(i for i, command in enumerate(commands) if ' pull --ff-only origin dev' in command)
        self.assertLess(reset, pull)
        self.assertTrue(any(' remote set-url origin ' + GITCODE in command for command in commands))
        self.assertTrue(any(' config --local http.proxy http://proxy:8080' in command for command in commands))
        self.manager._fetch_with_retry.assert_called_once_with('origin', 'dev')

    def test_original_init_recovery_is_preserved(self):
        self.manager._fetch_with_retry = Mock()
        self.manager.execute = Mock(side_effect=[False] + [True] * 20)
        self.manager.remove = Mock()
        with patch('deploy.git.os.path.exists', return_value=False):
            self.manager.git_repository_init(GITCODE)
        self.manager.remove.assert_has_calls([
            call('./.git/config'), call('./.git/index'), call('./.git/HEAD'),
        ])
        commands = [item.args[0] for item in self.manager.execute.call_args_list]
        self.assertTrue(any(' config --local --unset http.proxy' in command for command in commands))


class CloudForcePolicyTest(unittest.TestCase):
    def test_valid_force_policy_and_invalid_responses(self):
        for body, expected in ((b'true', True), (b'false', False), (b'{}', None), (b'<html>error</html>', None)):
            with self.subTest(body=body):
                response = requests.Response()
                response.status_code = 200
                response._content = body
                with patch('deploy.git.requests.get', return_value=response) as get:
                    self.assertIs(expected, GitManager.cloud_force_update_enabled())
                get.assert_called_once()

    def test_cloud_failures_are_not_fatal(self):
        for error in (requests.exceptions.SSLError('WRONG_VERSION_NUMBER'),
                      requests.ConnectionError('DNS failure'), requests.Timeout('timeout'),
                      requests.HTTPError('503 Service Unavailable')):
            with self.subTest(error=error), patch('deploy.git.requests.get', side_effect=error) as get:
                self.assertIsNone(GitManager.cloud_force_update_enabled())
                get.assert_called_once()


if __name__ == '__main__':
    unittest.main()
