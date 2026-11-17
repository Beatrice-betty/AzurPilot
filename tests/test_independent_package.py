"""离线验证独立更新的选源、包完整性和文件恢复。"""

import base64
import hashlib
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from deploy import independent


class TestIndependentPackage(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.key = Ed25519PrivateKey.generate()
        self.current = {
            'schema': 1, 'revision': '1' * 40, 'sequence': 10,
            'dependencies': 'a' * 64, 'frontendSha256': 'b' * 64,
            'publicKey': base64.b64encode(self.key.public_key().public_bytes_raw()).decode(),
        }
        independent.write_json(self.root / independent.RECEIPT, self.current)

    def test_default_mirror_delay_uses_github_head_and_tries_domestic_download_first(self):
        with patch.object(independent, '_remote_revision', side_effect=['1' * 40, '2' * 40]) as remote:
            offer = independent.check(self.root)
        self.assertEqual('2' * 40, offer.release['revision'])
        self.assertEqual(independent.GITCODE_REPOSITORY, offer.repository)
        self.assertEqual([call.args[1] for call in remote.call_args_list], list(independent.SOURCES))
        with patch.object(independent, 'git', side_effect=[None, subprocess.TimeoutExpired('fetch', 1), None, None]) as git:
            repository, checkout = independent._fetch_source(self.root, self.root, offer)
        first, fallback = git.call_args_list[1::2]
        self.assertEqual(repository, independent.GITHUB_REPOSITORY)
        self.assertEqual([first.args[-2], fallback.args[-2]], list(independent.SOURCES))
        self.assertEqual(checkout, fallback.args[1])
        self.assertNotEqual(first.args[1], checkout)

    def test_deploy_repository_and_branch_select_unsigned_git_update(self):
        config = self.root / 'config'
        config.mkdir()
        repository = 'https://git.example.com/other/AzurPilot.git'
        deploy = config / 'deploy.yaml'
        deploy.write_text(f'Deploy:\n  Git:\n    Repository: "{repository}"\n    Branch: preview/test\n',
                          encoding='utf-8')
        with patch.object(independent, '_remote_revision', return_value='2' * 40) as remote:
            offer = independent.check(self.root)
        remote.assert_called_once_with(self.root, repository, 'preview/test')
        self.assertEqual(offer.release, {'revision': '2' * 40})
        with patch.object(independent, 'git', side_effect=[None, subprocess.TimeoutExpired('fetch', 1)]) as git:
            with self.assertRaisesRegex(RuntimeError, '所选仓库'):
                independent._fetch_source(self.root, self.root, offer)
            self.assertEqual(git.call_count, 2)
            self.assertEqual(git.call_args.args[-2], repository)
        deploy.write_text(f'Repository: {repository}\nBranch: other\n', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, '更新源已更改'):
            independent.prepare(offer, self.root)

    def test_cached_github_download_clears_stale_locks_and_retries_over_http1(self):
        revision = '2' * 40
        offer = independent.GitOffer({'revision': revision}, independent.GITHUB_REPOSITORY, 'dev')
        stale_locks = [self.root / 'shallow.lock', self.root / f'{independent.SOURCE_REF}.lock']
        stale_locks[1].parent.mkdir(parents=True)
        for lock in stale_locks:
            lock.touch()

        def git_fetch(root, cwd, *args):
            self.assertEqual(cwd, self.root)
            self.assertTrue(all(not lock.exists() for lock in stale_locks))
            if args[0] == 'fetch':
                for lock in stale_locks:
                    lock.touch()
                raise subprocess.TimeoutExpired('fetch', 1)

        with patch.object(independent, 'git', side_effect=git_fetch) as git:
            repository, checkout = independent._fetch_source(self.root, self.root, offer, cache=self.root)
        first, retry = git.call_args_list
        self.assertEqual(repository, offer.repository)
        self.assertEqual(checkout, self.root)
        self.assertEqual(first.args[-2:], (offer.repository, f'+{revision}:{independent.SOURCE_REF}'))
        self.assertEqual(retry.args[2:], ('-c', 'http.version=HTTP/1.1', *first.args[2:]))

    def test_git_update_reuses_objects_and_rebuilds_webui_only_when_changed(self):
        executable = shutil.which('git')
        if not executable:
            self.skipTest('Git unavailable')
        env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)

        def run_git(root, cwd, *args, stdout=subprocess.PIPE, timeout=20):
            return subprocess.run(
                [executable, '-c', 'fetch.unpackLimit=1', *args], cwd=cwd, env=env,
                stdout=stdout, stderr=subprocess.PIPE, check=True, timeout=timeout,
            )

        upstream = self.root / 'upstream'
        upstream.mkdir()
        run_git(self.root, upstream, 'init', '-q')
        for folder in ('assets', 'deploy', 'frontend/src'):
            (upstream / folder).mkdir(parents=True)
        (upstream / 'assets/asset.bin').write_bytes(os.urandom(256 * 1024))
        (upstream / 'deploy/independent.py').touch()
        (upstream / 'frontend/src/index.ts').write_bytes(b'first webui')
        for name in ('uv.lock', 'pyproject.toml'):
            (upstream / name).touch()
            (self.root / name).touch()
        self.current['dependencies'] = independent.dependency_id(self.root)
        independent.write_json(self.root / independent.RECEIPT, self.current)
        independent.write_json(self.root / independent.FILES, {
            name: independent.digest(self.root / name) for name in ('uv.lock', 'pyproject.toml')
        })

        def build_frontend(root, temp, staged):
            name = 'frontend/dist/index.html'
            output = staged / name
            output.parent.mkdir(parents=True)
            output.write_bytes((temp / 'build/frontend/src/index.ts').read_bytes())
            return {name: independent.digest(output)}

        def update(contents):
            (upstream / 'gui.py').write_bytes(contents)
            run_git(self.root, upstream, 'add', '.')
            run_git(self.root, upstream, '-c', 'user.name=Test', '-c', 'user.email=test@example.test',
                    'commit', '-qm', 'test update')
            revision = run_git(self.root, upstream, 'rev-parse', 'HEAD').stdout.decode().strip()
            offer = independent.GitOffer({'revision': revision}, upstream.as_uri(), 'dev')
            ready = independent.prepare(offer, self.root)
            self.assertEqual((ready / 'app/gui.py').read_bytes(), contents)
            self.assertEqual((ready / 'app/assets/asset.bin').read_bytes(),
                             (upstream / 'assets/asset.bin').read_bytes())
            independent.apply(ready, self.root)
            independent.confirm(self.root)
            self.assertFalse(ready.exists())
            self.assertFalse(list((self.root / independent.WORK).glob('download-*')))

        with (
            patch.object(independent, 'git', side_effect=run_git),
            patch.object(independent, 'update_source', return_value=(upstream.as_uri(), 'dev')),
            patch.object(independent, 'probe') as probe,
            patch.object(independent, '_build_frontend', side_effect=build_frontend) as build,
        ):
            update(b'first version')
            cache, = (self.root / independent.WORK).glob('source-*.git')
            packs = cache / 'objects/pack'
            previous = set(packs.glob('*.pack'))
            downloaded = sum(path.stat().st_size for path in previous)
            self.assertGreater(downloaded, 256 * 1024)

            update(b'second version')
            added = sum(path.stat().st_size for path in set(packs.glob('*.pack')) - previous)
            self.assertGreater(added, 0)
            self.assertLess(added, downloaded // 10)
            self.assertEqual(build.call_count, 1)
            self.assertEqual((self.root / 'frontend/dist/index.html').read_bytes(), b'first webui')

            (upstream / 'frontend/src/index.ts').write_bytes(b'second webui')
            update(b'third version')
            self.assertEqual(build.call_count, 2)
            self.assertEqual((self.root / 'frontend/dist/index.html').read_bytes(), b'second webui')
            self.assertEqual(probe.call_count, 3)

    @unittest.skipUnless(independent.os.name == 'nt', 'Windows MinGit timeout')
    def test_git_timeout_terminates_windows_process_tree_before_waiting(self):
        timeout = subprocess.TimeoutExpired('fetch', 1)
        with patch.object(independent.subprocess, 'Popen') as popen, patch.object(
            independent.subprocess, 'run',
        ) as taskkill:
            child = popen.return_value.__enter__.return_value
            child.pid = 100
            child.wait.side_effect = timeout
            child.poll.return_value = None
            with self.assertRaises(subprocess.TimeoutExpired):
                independent.git(self.root, self.root, 'fetch', timeout=1)
        taskkill.assert_called_once()
        self.assertEqual(taskkill.call_args.args[0][1:], ['/PID', '100', '/T', '/F'])
        child.kill.assert_called_once()

    def test_gitcode_frontend_uses_same_release_commit_as_manifest(self):
        revision = 'a' * 40
        manifest = independent.canonical({'release': {
            'revision': '2' * 40, 'frontendSha256': hashlib.sha256(b'frontend archive').hexdigest(),
        }})
        zip_path = self.root / 'frontend.zip'
        with patch.object(independent, 'gitcode_checkout', return_value=self.root), patch.object(
            independent, 'fetch_gitcode_release', return_value=revision,
        ) as fetch_git, patch.object(
            independent, 'gitcode_release_file', side_effect=[manifest, b'frontend archive'],
        ) as read_git, patch.object(independent, 'fetch') as raw:
            self.assertTrue(independent._matching_frontend(
                self.root, independent.GITCODE_REPOSITORY, '2' * 40, zip_path,
            ))
        self.assertEqual(b'frontend archive', zip_path.read_bytes())
        fetch_git.assert_called_once_with(self.root, self.root)
        self.assertEqual(
            [call.args[2:4] for call in read_git.call_args_list],
            [(revision, 'release.json'), (revision, 'frontend.zip')],
        )
        raw.assert_not_called()

    def test_unavailable_release_keeps_installed_receipt(self):
        with patch.object(independent, '_remote_revision', side_effect=TimeoutError):
            with self.assertRaisesRegex(RuntimeError, '当前版本'):
                independent.check(self.root)
        self.assertEqual(self.current, independent.read_json(self.root / independent.RECEIPT))

    def ready(self):
        app = self.root / 'gui.py'
        app.write_text('old', encoding='utf-8')
        config = self.root / 'config'
        config.mkdir()
        (config / 'player.json').write_text('old data', encoding='utf-8')
        independent.write_json(self.root / independent.FILES, {'gui.py': independent.digest(app)})
        prepared = self.root / independent.WORK / 'ready-sample'
        (prepared / 'app/assets').mkdir(parents=True)
        (prepared / 'app/gui.py').write_text('new', encoding='utf-8')
        (prepared / 'app/assets/new.txt').write_text('new asset', encoding='utf-8')
        independent.write_json(prepared / 'version.json', {**self.current, 'revision': '2' * 40, 'sequence': 11})
        independent.write_json(prepared / 'files.json', {
            name: independent.digest(prepared / 'app' / name)
            for name in ('gui.py', 'assets/new.txt')
        })
        return prepared

    def test_trial_failure_restores_version_and_preserves_failed_data_copy(self):
        ready = self.ready()
        independent.apply(ready, self.root)
        self.assertEqual('new', (self.root / 'gui.py').read_text())
        (self.root / 'config/player.json').write_text('new data', encoding='utf-8')
        self.assertTrue(independent.recover(self.root))
        self.assertFalse(independent.recover(self.root))
        self.assertEqual('old', (self.root / 'gui.py').read_text())
        self.assertEqual('old data', (self.root / 'config/player.json').read_text())
        self.assertEqual('new data', (ready / 'config-after-failed-update/player.json').read_text())
        self.assertFalse((ready / 'previous').exists())
        self.assertFalse((ready / 'app').exists())
        self.assertFalse((self.root / 'assets/new.txt').exists())
        self.assertEqual(self.current, independent.read_json(self.root / independent.RECEIPT))

    def test_trial_keeps_consistent_database_backup_until_confirmed(self):
        ready = self.ready()
        database = self.root / 'config/azurpilot.db'
        writer = sqlite3.connect(database)
        self.addCleanup(writer.close)
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('CREATE TABLE history (value TEXT)')
        writer.execute('INSERT INTO history VALUES (?)', ('saved',))
        writer.commit()
        independent.apply(ready, self.root)
        independent.discard_ready(ready, self.root)
        backup = sqlite3.connect(ready / 'previous/config/azurpilot.db')
        try:
            self.assertEqual([('saved',)], backup.execute('SELECT value FROM history').fetchall())
        finally:
            backup.close()
        independent.confirm(self.root)
        self.assertFalse(independent.trial_pending(self.root))
        self.assertFalse(ready.exists())

    def test_local_edit_refuses_replacement(self):
        ready = self.ready()
        (self.root / 'gui.py').write_text('local edit', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, '本机修改'):
            independent.apply(ready, self.root)
        self.assertEqual('local edit', (self.root / 'gui.py').read_text())
        self.assertFalse(independent.trial_pending(self.root))
        self.assertFalse(ready.exists())
