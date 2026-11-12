"""验证单向镜像的分支来源、强推及提交核对。"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class TestGitCodeSync(unittest.TestCase):
    def test_checkout_and_push_retry(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/sync2.yml').read_text(encoding='utf-8'))
        job = workflow['jobs']['repo-sync']
        self.assertIn('Beatrice-betty/AzurPilot', job['if'])
        self.assertEqual(0, job['steps'][0]['with']['fetch-depth'])
        push = next(step['run'] for step in job['steps'] if step.get('name') == 'sync')
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
        printf '%s\n' "$REVISION"
    elif [[ "$*" == *" push "* ]]; then
        printf '%s\n' "$*" >> push.log
        local count
        count="$(wc -l < push.log)"
        if [ "$count" -le "$FAILURES" ]; then
            return 128
        fi
    elif [[ "$*" == *" ls-remote "* ]]; then
        printf '%s\trefs/heads/dev\n' "$REVISION"
    fi
}
sleep() { :; }
'''
        for failures, expected_status, expected_attempts in ((2, 0, 3), (5, 1, 5)):
            with self.subTest(failures=failures), tempfile.TemporaryDirectory() as directory:
                result = subprocess.run(
                    [str(bash), '-c', stub + push], cwd=directory, capture_output=True,
                    env={**os.environ, 'FAILURES': str(failures), 'REVISION': revision,
                         'GITCODE_TOKEN': 'test-token', 'MIRROR_BRANCH': 'dev'},
                )
                self.assertEqual(expected_status, result.returncode, result.stderr.decode(errors='replace'))
                self.assertEqual('fetch --no-tags origin refs/heads/dev', (Path(directory) / 'fetch.log').read_text().strip())
                attempts = (Path(directory) / 'push.log').read_text().splitlines()
                self.assertEqual(expected_attempts, len(attempts))
                for attempt in attempts:
                    self.assertIn('http.userAgent=Mozilla/5.0', attempt)
                    self.assertIn('gitcode.com/Beatrice-betty/AzurPilot.git', attempt)
                    self.assertIn(f'+{revision}:refs/heads/dev', attempt)
                    self.assertNotIn('test-token', attempt)
