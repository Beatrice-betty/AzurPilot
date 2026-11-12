import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from deploy import config as deploy_config
from deploy import geo
from deploy import git as deploy_git
from deploy.Windows import config as windows_config
from deploy.Windows import git as windows_git


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
            for repository in ('cn', 'cnb', 'gitcode', config_module.GIT_OVER_CDN_REPOSITORY,
                               'https://github.com/wess09/AzurPilot.git'):
                with self.subTest(module=config_module.__name__, repository=repository):
                    config = self.make_config(config_module, repository)
                    self.assertEqual(config.Repository, config_module.GITCODE_REPOSITORY)
                    self.assertFalse(config.GitOverCdn)

    def test_explicit_github_and_custom_repository_are_preserved(self):
        for config_module in (deploy_config, windows_config):
            for repository in (config_module.GITHUB_REPOSITORY, 'https://example.invalid/custom'):
                with self.subTest(module=config_module.__name__, repository=repository):
                    self.assertEqual(self.make_config(config_module, repository).Repository, repository)

    def test_peer_fallback_repository(self):
        for module in (deploy_git, windows_git):
            self.assertEqual(
                module.peer_fallback_repository(deploy_config.GITCODE_REPOSITORY + '.git'),
                deploy_config.GITHUB_REPOSITORY,
            )
            self.assertIsNone(module.peer_fallback_repository('https://example.invalid/custom'))


if __name__ == '__main__':
    unittest.main()
