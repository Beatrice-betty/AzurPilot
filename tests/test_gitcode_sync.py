"""验证国内镜像的分支、标签及提交核对。"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class TestRepositoryMirrors(unittest.TestCase):
    def test_all_branches_and_tags_are_fetched_from_github(self):
        for name, host, secret in (
            ('sync-cnb.yml', 'cnb.cool', 'CNB_TOKEN'),
            ('sync2.yml', 'gitcode.com', 'GITCODE_TOKEN'),
        ):
            with self.subTest(host=host):
                path = ROOT / '.github/workflows' / name
                workflow = yaml.load(path.read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
                self.assertEqual(workflow['on']['push'], {'branches': ['**'], 'tags': ['**']})
                self.assertIn('schedule', workflow['on'])
                job = workflow['jobs']['repo-sync']
                self.assertIn('Beatrice-betty/AzurPilot', job['if'])
                self.assertEqual(job['concurrency']['group'], f'azurpilot-mirror-{host}')
                self.assertIn(secret, job['steps'][-1]['env']['MIRROR_TOKEN'])
                self.assertIn(f'https://{host}/Beatrice-betty/AzurPilot', job['steps'][-1]['run'])

        git = shutil.which('git')
        bash = Path(git).resolve().parent.parent / 'bin/bash.exe' if os.name == 'nt' and git else shutil.which('bash')
        if not git or not bash or not Path(bash).exists():
            self.skipTest('环境未提供 Git 和 Bash')
        script = (ROOT / '.github/scripts/sync-repo-mirror.sh').read_text(encoding='utf-8')

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            origin = root / 'github'
            checkout = root / 'runner'
            mirror = root / 'mirror'

            def git_run(cwd, *args):
                return subprocess.run([git, *args], cwd=cwd, check=True, capture_output=True,
                                      text=True, encoding='utf-8').stdout.strip()

            git_run(root, 'init', '-b', 'dev', origin)
            git_run(origin, 'config', 'user.name', 'Mirror test')
            git_run(origin, 'config', 'user.email', 'mirror@example.invalid')
            git_run(origin, 'commit', '--allow-empty', '-m', 'Base')
            git_run(origin, 'branch', 'preview/custom')
            git_run(origin, 'commit', '--allow-empty', '-m', 'Latest dev')
            git_run(origin, 'tag', '-a', 'azurpilot-independent-deps/test', '-m', 'Update')
            git_run(root, 'init', '--bare', mirror)
            git_run(root, 'clone', '--depth=1', origin.as_uri(), checkout)
            result = subprocess.run([str(bash), '-c', script, '--', mirror.as_uri()], cwd=checkout,
                                    capture_output=True, encoding='utf-8', errors='replace', timeout=45)
            if os.name == 'nt' and 'NtCreateDirectoryObject' in result.stderr:
                self.skipTest('Windows 环境未授予 Git Bash 所需权限')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(git_run(checkout, 'ls-remote', '--refs', 'origin'),
                             git_run(checkout, 'ls-remote', '--refs', mirror.as_uri()))


class TestGitCodeSync(unittest.TestCase):
    def test_full_push_retry(self):
        script = (ROOT / '.github/scripts/sync-repo-mirror.sh').read_text(encoding='utf-8')
        git_path = shutil.which('git')
        bash = (Path(git_path).resolve().parent.parent / 'bin/bash.exe') if os.name == 'nt' and git_path else shutil.which('bash')
        if not bash or not Path(bash).exists():
            self.skipTest('环境未提供 Bash')
        probe = subprocess.run([str(bash), '-c', 'exit 0'], capture_output=True)
        if os.name == 'nt' and b'NtCreateDirectoryObject' in probe.stderr:
            self.skipTest('Windows 环境未授予 Git Bash 所需权限')
        self.assertEqual(probe.returncode, 0, probe.stderr.decode(errors='replace'))
        revision = '4' * 40
        stub = '''
set -e
git() {
    if [[ "$1" == "fetch" ]]; then
        printf '%s\n' "$*" >> fetch.log
    elif [[ "$1" == "rev-parse" ]]; then
        printf 'false\n'
    elif [[ "$1" == "push" ]]; then
        printf '%s\n' "$*" >> push.log
        printf '%s\n' "$GIT_CONFIG_VALUE_1" >> agents.log
        local count
        count="$(wc -l < push.log)"
        if [ "$count" -le "$FAILURES" ]; then
            return 128
        fi
        touch synchronized
    elif [[ "$1" == "ls-remote" ]]; then
        if [[ "$3" == "origin" || -f synchronized ]]; then
            printf '%s\trefs/heads/dev\n' "$REVISION"
        fi
    fi
}
sleep() { :; }
'''
        for failures, expected_status, expected_attempts in ((2, 0, 3), (5, 1, 5)):
            with self.subTest(failures=failures), tempfile.TemporaryDirectory() as directory:
                result = subprocess.run(
                    [str(bash), '-c', stub + script, '--',
                     'https://gitcode.com/Beatrice-betty/AzurPilot.git', 'Beatrice-betty'],
                    cwd=directory, capture_output=True,
                    env={**os.environ, 'FAILURES': str(failures), 'REVISION': revision,
                         'MIRROR_TOKEN': 'test-token'},
                )
                self.assertEqual(expected_status, result.returncode, result.stderr.decode(errors='replace'))
                self.assertIn('fetch --prune --no-tags origin', (Path(directory) / 'fetch.log').read_text())
                attempts = (Path(directory) / 'push.log').read_text().splitlines()
                agents = (Path(directory) / 'agents.log').read_text().splitlines()
                self.assertEqual(expected_attempts, len(attempts))
                self.assertEqual(expected_attempts, len(agents))
                for attempt, agent in zip(attempts, agents):
                    self.assertTrue(agent.startswith('Mozilla/5.0'))
                    self.assertIn('gitcode.com/Beatrice-betty/AzurPilot.git', attempt)
                    self.assertIn('refs/azurpilot-mirror/heads/*:refs/heads/*', attempt)
                    self.assertIn('refs/azurpilot-mirror/tags/*:refs/tags/*', attempt)
                    self.assertNotIn('test-token', attempt)
