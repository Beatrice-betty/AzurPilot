import unittest
import tempfile
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

from deploy import config as deploy_config
from deploy import geo
from deploy import git as deploy_git
from deploy.Windows import config as windows_config
from deploy.utils import (
    CNB_REPOSITORY,
    GITCODE_REPOSITORY,
    GITHUB_REPOSITORY,
    GITLINK_REPOSITORY,
    official_repository,
    repository_candidates,
)


class TestIp9Location(unittest.TestCase):
    @patch('deploy.geo.requests.get')
    def test_returns_lowercase_country_code(self, get):
        response = get.return_value
        response.json.return_value = {'data': {'country_code': 'CN'}}

        self.assertEqual(geo.get_country_code(), 'cn')

        get.assert_called_once_with(
            geo.IP9_LOCATION_URL,
            timeout=5,
            headers={'User-Agent': 'AzurPilot'},
        )
        response.raise_for_status.assert_called_once_with()

    @patch('deploy.geo.requests.get')
    def test_invalid_response_returns_none(self, get):
        response = get.return_value
        response.json.return_value = {'data': {}}

        self.assertIsNone(geo.get_country_code())


class TestDeployLocation(unittest.TestCase):
    def make_config(self, config_module, repository=None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        template = root / 'template.yaml'
        file = root / 'deploy.yaml'
        text = f'Repository: {repository or config_module.GITHUB_REPOSITORY}\nBranch: master\n'
        template.write_text(text)
        file.write_text(text)
        template_patch = (patch.object(config_module, 'get_deploy_template', return_value=str(template))
                          if config_module is deploy_config
                          else patch.object(config_module, 'DEPLOY_TEMPLATE', str(template)))
        with template_patch, patch.object(config_module.DeployConfig, 'show_config'):
            return config_module.DeployConfig(str(file))

    def test_default_and_old_aliases_use_the_owned_domestic_mirror(self):
        for config_module in (deploy_config, windows_config):
            for repository in ('cn', 'cnb', 'gitcode', GITCODE_REPOSITORY, config_module.GIT_OVER_CDN_REPOSITORY,
                               'https://github.com/wess09/AzurPilot.git'):
                with self.subTest(module=config_module.__name__, repository=repository):
                    config = self.make_config(config_module, repository)
                    self.assertEqual(config.Repository, CNB_REPOSITORY)
                    self.assertFalse(config.GitOverCdn)

    def test_explicit_github_and_custom_repository_are_preserved(self):
        for config_module in (deploy_config, windows_config):
            for repository in (config_module.GITHUB_REPOSITORY, 'https://example.invalid/custom'):
                with self.subTest(module=config_module.__name__, repository=repository):
                    self.assertEqual(self.make_config(config_module, repository).Repository, repository)

    def test_repository_order(self):
        self.assertEqual(repository_candidates(official_repository(GITCODE_REPOSITORY + '.git')),
                         (CNB_REPOSITORY, GITCODE_REPOSITORY, GITLINK_REPOSITORY, GITHUB_REPOSITORY))
        self.assertEqual(repository_candidates(GITCODE_REPOSITORY),
                         (GITCODE_REPOSITORY, GITLINK_REPOSITORY, GITHUB_REPOSITORY))
        self.assertEqual(repository_candidates(official_repository(GITLINK_REPOSITORY + '.git')),
                         (GITLINK_REPOSITORY, GITHUB_REPOSITORY))
        self.assertEqual(official_repository('gitee'), CNB_REPOSITORY)
        self.assertEqual(repository_candidates('https://example.invalid/custom'), ('https://example.invalid/custom',))

    def test_fetch_updates_tracking_ref_only_with_the_github_commit(self):
        revision = 'a' * 40
        expected = subprocess.CompletedProcess([], 0, stdout=f'{revision}\trefs/heads/preview/test\n'.encode())
        old = subprocess.CompletedProcess([], 0, stdout=f'{"b" * 40}\trefs/heads/preview/test\n'.encode())
        with patch.object(deploy_git.subprocess, 'run', side_effect=[
            expected,
            old,
            expected, subprocess.TimeoutExpired('fetch', 1),
            expected, subprocess.TimeoutExpired('fetch', 1),
            None, None,
        ]) as run:
            self.assertTrue(deploy_git.fetch_verified_branch(
                'git', 'origin', 'preview/test', repository_candidates(CNB_REPOSITORY),
                user_agent=lambda: 'git/test', max_retry=1, delay=0, log=Mock(),
            ))
        commands = [call.args[0][3:] for call in run.call_args_list]
        self.assertEqual([parts[-2:] for parts in commands if parts[0] == 'fetch'],
                         [[GITCODE_REPOSITORY, revision], [GITLINK_REPOSITORY, revision], [GITHUB_REPOSITORY, revision]])
        self.assertEqual(commands[-1], ['update-ref', 'refs/remotes/origin/preview/test', revision])

    def test_github_lookup_failure_leaves_tracking_ref_untouched(self):
        with patch.object(deploy_git.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ls-remote', 1)) as run:
            self.assertFalse(deploy_git.fetch_verified_branch(
                'git', 'origin', 'dev', repository_candidates(CNB_REPOSITORY),
                user_agent=lambda: 'git/test', max_retry=3, delay=0, log=Mock(),
            ))
        run.assert_called_once()


if __name__ == '__main__':
    unittest.main()
