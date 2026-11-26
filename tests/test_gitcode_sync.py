"""验证国内镜像的分支、标签及提交核对。"""

import base64
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _require_git_bash(test_case):
    git = shutil.which('git')
    bash = Path(git).resolve().parent.parent / 'bin/bash.exe' if os.name == 'nt' and git else shutil.which('bash')
    if not git or not bash or not Path(bash).exists():
        test_case.skipTest('环境未提供 Git 和 Bash')
    probe = subprocess.run([str(bash), '-c', 'exit 0'], capture_output=True)
    if os.name == 'nt' and b'NtCreateDirectoryObject' in probe.stderr:
        test_case.skipTest('Windows 环境未授予 Git Bash 所需权限')
    test_case.assertEqual(probe.returncode, 0, probe.stderr.decode(errors='replace'))
    return git, str(bash)


class TestRepositoryMirrors(unittest.TestCase):
    def test_all_branches_and_tags_are_fetched_from_github(self):
        for name, host, secret, target, username in (
            ('sync-cnb.yml', 'cnb.cool', 'CNB_TOKEN', 'https://cnb.cool/Beatrice-betty/AzurPilot', 'cnb'),
            ('sync2.yml', 'gitcode.com', 'GITCODE_TOKEN', 'https://gitcode.com/Beatrice-betty/AzurPilot.git',
             'Beatrice-betty'),
            ('sync-gitlink.yml', 'gitlink.org.cn', 'GITLINK_TOKEN',
             'https://gitlink.org.cn/BeatriceBetty/AzurPilot.git', 'BeatriceBetty'),
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
                self.assertIn(f'sync-repo-mirror.sh {target} {username}', job['steps'][-1]['run'])

        git, bash = _require_git_bash(self)
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
            result = subprocess.run([bash, '-c', script, '--', mirror.as_uri()], cwd=checkout,
                                    capture_output=True, encoding='utf-8', errors='replace', timeout=45)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(git_run(checkout, 'ls-remote', '--refs', 'origin'),
                             git_run(checkout, 'ls-remote', '--refs', mirror.as_uri()))


class TestGitCodeSync(unittest.TestCase):
    def test_full_push_retry(self):
        script = (ROOT / '.github/scripts/sync-repo-mirror.sh').read_text(encoding='utf-8')
        _, bash = _require_git_bash(self)
        revision = '4' * 40
        stub = r'''
set -e
git() {
    if [[ "$1" == "fetch" ]]; then
        printf '%s\n' "$*" >> fetch.log
    elif [[ "$1" == "rev-parse" ]]; then
        printf 'false\n'
    elif [[ "$1" == "push" ]]; then
        printf '%s\n' "$*" >> push.log
        printf '%s\n' "${GIT_CONFIG_VALUE_1:-}" >> agents.log
        printf '%s|%s|%s\n' "${GIT_CONFIG_COUNT:-}" "${GIT_CONFIG_KEY_0:-}" "${GIT_CONFIG_VALUE_0:-}" >> auth.log
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
                    [bash, '-c', stub + script, '--',
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

        with self.subTest(host='gitlink.org.cn', mode='missing-token'), tempfile.TemporaryDirectory() as directory:
            env = {k: v for k, v in os.environ.items() if k != 'MIRROR_TOKEN'}
            result = subprocess.run(
                [bash, '-c', stub + script, '--',
                 'https://gitlink.org.cn/BeatriceBetty/AzurPilot.git', 'BeatriceBetty'],
                cwd=directory, capture_output=True, encoding='utf-8', errors='replace', env=env,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn('请配置 gitlink.org.cn 的仓库写入凭据', result.stdout + result.stderr)

        with self.subTest(host='gitlink.org.cn', mode='sync'), tempfile.TemporaryDirectory() as directory:
            expected_auth = base64.b64encode(b'BeatriceBetty:test-token').decode('ascii')
            result = subprocess.run(
                [bash, '-c', stub + script, '--',
                 'https://gitlink.org.cn/BeatriceBetty/AzurPilot.git', 'BeatriceBetty'],
                cwd=directory, capture_output=True, encoding='utf-8', errors='replace',
                env={**os.environ, 'FAILURES': '0', 'REVISION': revision, 'MIRROR_TOKEN': 'test-token'},
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn(f'::add-mask::{expected_auth}', result.stdout)
            attempts = (Path(directory) / 'push.log').read_text().splitlines()
            auth_lines = (Path(directory) / 'auth.log').read_text().splitlines()
            self.assertEqual(1, len(attempts))
            self.assertIn('gitlink.org.cn/BeatriceBetty/AzurPilot.git', attempts[0])
            self.assertIn('refs/azurpilot-mirror/heads/*:refs/heads/*', attempts[0])
            self.assertIn('refs/azurpilot-mirror/tags/*:refs/tags/*', attempts[0])
            self.assertNotIn('test-token', attempts[0])
            self.assertEqual(
                f'1|http.https://gitlink.org.cn/.extraHeader|Authorization: Basic {expected_auth}',
                auth_lines[0],
            )
