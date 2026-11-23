"""离线验证独立更新的选源、包完整性和文件恢复。"""

import base64
import hashlib
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing, nullcontext
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from deploy import independent, independent_runtime


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

    def test_healthy_gitcode_waits_without_checking_github(self):
        for revision in (self.current['revision'], '2' * 40):
            with (
                self.subTest(revision=revision),
                patch.object(independent, '_remote_revisions', return_value=(revision, None)) as remote,
                patch.object(independent, '_includes_installed', return_value=False),
            ):
                self.assertIsNone(independent.check(self.root))
            remote.assert_called_once_with(self.root, independent.GITCODE_REPOSITORY, 'dev',
                                           dependency_releases=False)

    def test_gitcode_download_failure_falls_back_to_the_same_commit(self):
        revision = '2' * 40
        with (
            patch.object(independent, '_remote_revisions', return_value=(revision, None)) as remote,
            patch.object(independent, '_includes_installed', return_value=True),
        ):
            offer = independent.check(self.root)
        remote.assert_called_once_with(self.root, independent.GITCODE_REPOSITORY, 'dev',
                                           dependency_releases=False)
        self.assertEqual(revision, offer.release['revision'])
        with patch.object(independent, 'git', side_effect=[None, subprocess.TimeoutExpired('fetch', 1), None, None]) as git:
            repository, checkout = independent._fetch_source(self.root, self.root, offer)
        first, fallback = git.call_args_list[1::2]
        self.assertEqual(repository, independent.GITHUB_REPOSITORY)
        self.assertEqual([first.args[-2], fallback.args[-2]], list(independent.SOURCES))
        self.assertEqual(first.args[-1], revision)
        self.assertEqual(fallback.args[-1], revision)
        self.assertEqual(checkout, fallback.args[1])
        self.assertNotEqual(first.args[1], checkout)

    def test_gitcode_check_failure_downloads_directly_from_github(self):
        with (
            patch.object(independent, '_remote_revisions', side_effect=[TimeoutError, ('2' * 40, None)]) as remote,
            patch.object(independent, '_includes_installed', return_value=True),
        ):
            offer = independent.check(self.root)
        self.assertEqual([call.args[1] for call in remote.call_args_list], list(independent.SOURCES))
        self.assertEqual(offer.repository, independent.GITCODE_REPOSITORY)
        with patch.object(independent, 'git') as git:
            repository, _ = independent._fetch_source(self.root, self.root, offer)
        self.assertEqual(repository, independent.GITHUB_REPOSITORY)
        self.assertEqual(git.call_count, 2)
        self.assertEqual(git.call_args.args[-2], independent.GITHUB_REPOSITORY)

    def test_deploy_repository_and_branch_select_unsigned_git_update(self):
        config = self.root / 'config'
        config.mkdir()
        repository = 'https://git.example.com/other/AzurPilot.git'
        deploy = config / 'deploy.yaml'
        deploy.write_text(f'Deploy:\n  Git:\n    Repository: "{repository}"\n    Branch: preview/test\n',
                          encoding='utf-8')
        with patch.object(independent, '_remote_revisions', return_value=('2' * 40, None)) as remote:
            offer = independent.check(self.root)
        remote.assert_called_once_with(self.root, repository, 'preview/test', dependency_releases=False)
        self.assertEqual(offer.release, {'revision': '2' * 40})
        with patch.object(independent, 'git', side_effect=[None, subprocess.TimeoutExpired('fetch', 1)]) as git:
            with self.assertRaisesRegex(RuntimeError, '所选仓库'):
                independent._fetch_source(self.root, self.root, offer)
            self.assertEqual(git.call_count, 2)
            self.assertEqual(git.call_args.args[-2], repository)
        deploy.write_text(f'Repository: {repository}\nBranch: other\n', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, '更新源已更改'):
            independent.prepare(offer, self.root)

    def test_dependency_release_uses_the_tag_for_the_exact_branch_head(self):
        base, candidate = '2' * 40, '3' * 40
        stale_base, stale_candidate = '4' * 40, '5' * 40
        repository, branch = independent.GITHUB_REPOSITORY, 'preview/test'
        config = self.root / 'config'
        config.mkdir()
        (config / 'deploy.yaml').write_text(f'Repository: {repository}\nBranch: {branch}\n', encoding='utf-8')
        prefix = independent.DEPENDENCY_RELEASE_TAG
        ref = f'refs/heads/{branch}'
        listing = f'{stale_candidate}\t{prefix}{stale_base}\n{base}\t{ref}\n{candidate}\t{prefix}{base}\n'
        result = subprocess.CompletedProcess([], 0, stdout=listing.encode('ascii'))
        with (
            patch.object(independent, 'gitcode_checkout', return_value=self.root),
            patch.object(independent, 'git', return_value=result) as git,
            patch.object(independent_runtime, 'supports_hot_reload', side_effect=[False, True]),
        ):
            self.assertEqual(independent.check(self.root).release, {'revision': base})
            self.assertEqual(independent.check(self.root).release, {
                'revision': candidate, independent.DEPENDENCY_BASE_KEY: base,
            })
        self.assertEqual(git.call_args_list[0].args[-1:], (ref,))
        self.assertEqual(git.call_args_list[1].args[-2:], (ref, f'{prefix}*'))

    def test_missing_dependency_tag_keeps_the_installed_upgrade(self):
        base = '2' * 40
        self.current.update(branch='dev', **{independent.DEPENDENCY_BASE_KEY: base})
        independent.write_json(self.root / independent.RECEIPT, self.current)
        with patch.object(independent, '_remote_revisions', return_value=(base, None)):
            self.assertIsNone(independent.check(self.root))

    def test_dependency_release_parent_must_be_the_compatibility_commit(self):
        base, candidate = '2' * 40, '3' * 40
        other = '4' * 40
        # 只检查提交头，正文中的 parent 文本不能代替实际父提交。
        for header in (f'parent {other}', f'parent {base}\nparent {other}'):
            result = subprocess.CompletedProcess([], 0, stdout=f'{header}\n\nparent {base}\n'.encode('ascii'))
            with (
                self.subTest(header=header),
                patch.object(independent, 'git', return_value=result),
                self.assertRaisesRegex(ValueError, '兼容版本不匹配'),
            ):
                independent._validate_dependency_release(self.root, self.root, candidate, base)
        result = subprocess.CompletedProcess([], 0, stdout=f'parent {base}\n\nmessage\n'.encode('ascii'))
        with patch.object(independent, 'git', return_value=result):
            independent._validate_dependency_release(self.root, self.root, candidate, base)

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
        project = b'[project]\nname = "azurpilot"\nrequires-python = ">=3.14"\ndependencies = ["demo==1"]\n'
        lock = b'version = 1\n[[package]]\nname = "demo"\nversion = "1"\n'
        for name, contents in (('pyproject.toml', project), ('uv.lock', lock)):
            (upstream / name).write_bytes(contents)
            (self.root / name).write_bytes(contents)
        self.current['dependencies'] = independent.dependency_id(self.root)
        independent.write_json(self.root / independent.RECEIPT, self.current)
        independent.write_json(self.root / independent.FILES, {
            name: independent.digest(self.root / name) for name in ('uv.lock', 'pyproject.toml')
        })

        index = 'frontend/dist/index.html'
        asset = 'frontend/dist/assets/index.js'
        webui_root = b'<div id="root"></div>'
        branch = 'preview/test'

        def build_frontend(root, temp, staged, *, previous):
            content = (temp / 'build/frontend/src/index.ts').read_bytes()
            files = {index: webui_root + content, asset: b'unchanged frontend asset'}
            hashes = {name: hashlib.sha256(body).hexdigest() for name, body in files.items()}
            for name, body in files.items():
                if hashes[name] != previous.get(name):
                    output = staged / name
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(body)
            return hashes

        def update(contents, expected):
            (upstream / 'gui.py').write_bytes(contents)
            run_git(self.root, upstream, 'add', '.')
            run_git(self.root, upstream, '-c', 'user.name=Test', '-c', 'user.email=test@example.test',
                    'commit', '-qm', 'test update')
            revision = run_git(self.root, upstream, 'rev-parse', 'HEAD').stdout.decode().strip()
            offer = independent.GitOffer({'revision': revision}, upstream.as_uri(), branch)
            ready = independent.prepare(offer, self.root)
            self.addCleanup(independent.discard_ready, ready, self.root)
            self.assertEqual({path.relative_to(ready / 'app').as_posix()
                              for path in (ready / 'app').rglob('*') if path.is_file()}, expected)
            self.assertEqual((ready / 'app/gui.py').read_bytes(), contents)
            independent.apply(ready, self.root)
            independent.confirm(self.root)
            installed = independent.read_json(self.root / independent.RECEIPT)
            self.assertEqual((installed['branch'], installed['revision']), (branch, revision))
            self.assertEqual(installed['dependencies'], self.current['dependencies'])
            self.assertFalse(ready.exists())
            self.assertFalse(list((self.root / independent.WORK).glob('download-*')))
            if (upstream / 'assets/asset.bin').is_file():
                self.assertEqual((self.root / 'assets/asset.bin').read_bytes(),
                                 (upstream / 'assets/asset.bin').read_bytes())
            return revision

        with (
            patch.object(independent, 'git', side_effect=run_git),
            patch.object(independent, 'update_source', return_value=(upstream.as_uri(), branch)),
            patch.object(independent, '_build_frontend', side_effect=build_frontend) as build,
        ):
            first = update(b'version = 1\n', {'gui.py', 'assets/asset.bin', 'deploy/independent.py', index, asset})
            cache, = (self.root / independent.WORK).glob('source-*.git')
            packs = cache / 'objects/pack'
            previous = set(packs.glob('*.pack'))
            downloaded = sum(path.stat().st_size for path in previous)
            self.assertGreater(downloaded, 256 * 1024)

            project += b'\n[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
            (upstream / 'pyproject.toml').write_bytes(project)
            (upstream / 'uv.lock').write_bytes(b'# formatting only\n' + lock)
            second = update(b'version = 2\n', {'gui.py', 'pyproject.toml', 'uv.lock'})
            self.assertEqual((self.root / 'pyproject.toml').read_bytes(), project)
            self.assertNotEqual(independent.dependency_id(self.root), self.current['dependencies'])
            added = sum(path.stat().st_size for path in set(packs.glob('*.pack')) - previous)
            self.assertGreater(added, 0)
            self.assertLess(added, downloaded // 10)
            self.assertEqual(build.call_count, 1)
            self.assertEqual((self.root / index).read_bytes(), webui_root + b'first webui')
            run_git(self.root, upstream, 'config', 'uploadpack.allowFilter', 'true')
            self.assertFalse(independent._includes_installed(self.root, upstream.as_uri(), second, first))
            self.assertTrue(independent._includes_installed(self.root, upstream.as_uri(), first, second))

            (upstream / 'frontend/src/index.ts').write_bytes(b'second webui')
            (upstream / 'assets/obsolete.txt').write_bytes(b'remove me later')
            update(b'version = 3\n', {'gui.py', 'assets/obsolete.txt', index})
            self.assertEqual(build.call_count, 2)
            self.assertEqual((self.root / index).read_bytes(), webui_root + b'second webui')

            # 缓存被清掉时也只写摘要变化的文件，并处理删除。
            cache.rename(cache.with_suffix('.saved'))
            (upstream / 'assets/obsolete.txt').unlink()
            update(b'version = 4\n', {'gui.py'})
            self.assertFalse((self.root / 'assets/obsolete.txt').exists())
            self.assertEqual(build.call_count, 2)

            # 自定义源的 Git 归档属性也能改变相同 blob 导出的内容。
            (upstream / '.gitattributes').write_text('assets/revision.txt export-subst\n', encoding='utf-8')
            (upstream / 'assets/revision.txt').write_bytes(b'$Format:%H$')
            update(b'version = 5\n', {'gui.py', 'assets/revision.txt'})
            latest = update(b'version = 6\n', {'gui.py', 'assets/revision.txt'})
            self.assertEqual((self.root / 'assets/revision.txt').read_text(), latest)

    def test_prepare_refuses_invalid_dependencies_or_legacy_code_with_new_dependencies(self):
        project = b'[project]\nname = "azurpilot"\nrequires-python = ">=3.14"\ndependencies = ["demo==1"]\n'
        lock = b'version = 1\n[[package]]\nname = "demo"\nversion = "1"\n'
        installed = {'pyproject.toml': project, 'uv.lock': lock}
        for name, contents in installed.items():
            (self.root / name).write_bytes(contents)
        self.current['dependencies'] = independent.dependency_id(self.root)
        independent.write_json(self.root / independent.RECEIPT, self.current)
        previous = {name: independent.digest(self.root / name) for name in installed}
        independent.write_json(self.root / independent.FILES, previous)
        repository, branch = independent.update_source(self.root)
        offer = independent.GitOffer({'revision': '2' * 40}, repository, branch)

        def stage_changes(root, checkout, revision, installed_revision, previous, staged, **kwargs):
            staged.mkdir()
            path = staged / name
            path.write_bytes(contents)
            return {**previous, name: independent.digest(path), 'gui.py': '', 'deploy/independent.py': ''}, True

        with (
            patch.object(independent, '_source_checkout', return_value=nullcontext((repository, self.root))),
            patch.object(independent, '_uses_export_attributes', return_value=False),
            patch.object(independent, '_stage_changes', side_effect=stage_changes) as stage,
        ):
            for name, contents in (
                ('pyproject.toml', project.replace(b'>=3.14', b'>=3.15')),
                ('pyproject.toml', project.replace(b'demo==1', b'demo==2')),
                ('uv.lock', lock.replace(b'version = "1"', b'version = "2"')),
            ):
                with (
                    self.subTest(name=name, contents=contents),
                    self.assertRaisesRegex(RuntimeError, '无法使用已安装的 Python 环境'),
                ):
                    independent.prepare(offer, self.root)
            name, contents = 'pyproject.toml', b'[project\n'
            with self.assertRaisesRegex(ValueError, 'pyproject.toml 格式无效'):
                independent.prepare(offer, self.root)
            stage.reset_mock()
            (self.root / name).write_bytes(project + b'# local changes\n')
            with self.assertRaisesRegex(RuntimeError, '本机依赖清单已经改变'):
                independent.prepare(offer, self.root)
            stage.assert_not_called()

        self.assertEqual(independent.read_json(self.root / independent.RECEIPT), self.current)
        self.assertFalse(list((self.root / independent.WORK).glob('ready-*')))

    def test_dependency_update_only_activates_at_apply_and_restores_previous_environment_on_failure(self):
        old_lock = b'version = 1\n[[package]]\nname = "demo"\nversion = "1"\n'
        new_lock = old_lock.replace(b'version = "1"', b'version = "2"')
        installed = {
            'pyproject.toml': b'[project]\nrequires-python = ">=3.14"\n',
            'uv.lock': old_lock,
            'gui.py': b'',
            'deploy/independent.py': b'',
            'deploy/independent_runtime.py': b'',
            'frontend/dist/index.html': b'<div id="root"></div>',
            'frontend/dist/assets/index.js': b'asset',
        }
        for name, contents in installed.items():
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(contents)
        self.current['dependencies'] = independent.dependency_id(self.root)
        independent.write_json(self.root / independent.RECEIPT, self.current)
        independent.write_json(self.root / independent.FILES,
                               {name: independent.digest(self.root / name) for name in installed})
        repository, branch = independent.update_source(self.root)
        base = self.current['revision']
        release = {'revision': '2' * 40, independent.DEPENDENCY_BASE_KEY: base}
        offer = independent.GitOffer(release, repository, branch)
        commit = subprocess.CompletedProcess([], 0, stdout=f'parent {base}\n\nrelease\n'.encode('ascii'))

        def stage_changes(root, checkout, revision, installed_revision, previous, staged, **kwargs):
            staged.mkdir()
            (staged / 'uv.lock').write_bytes(new_lock)
            return {**previous, 'uv.lock': independent.digest(staged / 'uv.lock')}, True

        def sync(root, name, project, current):
            self.assertEqual((project / 'uv.lock').read_bytes(), new_lock)
            self.assertEqual((root / 'uv.lock').read_bytes(), old_lock)
            self.assertEqual(independent.read_json(root / independent.RECEIPT), self.current)
            python = independent.installed_python(root, {'pythonEnvironment': name})
            python.parent.mkdir(parents=True)
            python.touch()
            return python

        def probe(root, trial, *, python):
            self.assertEqual((trial / 'uv.lock').read_bytes(), new_lock)
            self.assertTrue((trial / 'frontend/dist/index.html').is_file())
            self.assertTrue(python.is_file())

        with (
            patch.object(independent, '_source_checkout', return_value=nullcontext((repository, self.root))),
            patch.object(independent, '_uses_export_attributes', return_value=True),
            patch.object(independent, 'git', return_value=commit),
            patch.object(independent, '_stage_changes', side_effect=stage_changes),
            patch.object(independent_runtime, 'sync_environment', side_effect=sync) as sync_environment,
            patch.object(independent_runtime, 'supports_hot_reload', return_value=False) as hot_reload,
            patch.object(independent, 'probe', side_effect=probe) as start,
        ):
            with self.assertRaisesRegex(RuntimeError, '先退出并重新启动'):
                independent.prepare(offer, self.root, hot_reload=True)
            sync_environment.assert_not_called()
            hot_reload.return_value = True
            start.side_effect = RuntimeError('candidate probe failed')
            with self.assertRaisesRegex(RuntimeError, 'candidate probe failed'):
                independent.prepare(offer, self.root, hot_reload=True)
            self.assertFalse(list((self.root / independent.PYTHON_ENVIRONMENTS).iterdir()))
            start.side_effect = probe
            ready = independent.prepare(offer, self.root, hot_reload=True)

        self.assertEqual((self.root / 'uv.lock').read_bytes(), old_lock)
        independent.apply(ready, self.root)
        receipt = independent.read_json(self.root / independent.RECEIPT)
        self.assertEqual(receipt[independent.DEPENDENCY_BASE_KEY], base)
        python = independent.installed_python(self.root)
        self.assertTrue(python.is_file())
        self.assertEqual((self.root / 'uv.lock').read_bytes(), new_lock)
        self.assertTrue(independent.recover(self.root))
        self.assertFalse(python.exists())
        self.assertEqual(independent.read_json(self.root / independent.RECEIPT), self.current)
        self.assertEqual((self.root / 'uv.lock').read_bytes(), old_lock)

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
        with patch.object(independent, '_remote_revisions', side_effect=TimeoutError) as remote:
            with self.assertRaisesRegex(RuntimeError, '当前版本'):
                independent.check(self.root)
        self.assertEqual([call.args[1] for call in remote.call_args_list], list(independent.SOURCES))
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

    def test_interrupted_write_restores_files_already_replaced(self):
        ready = self.ready()
        restore = independent.restore_file

        def interrupt(source, destination):
            if destination == self.root / 'gui.py':
                self.assertTrue((self.root / 'assets/new.txt').is_file())
                raise OSError('interrupted during replacement')
            restore(source, destination)

        with patch.object(independent, 'restore_file', side_effect=interrupt):
            with self.assertRaisesRegex(OSError, 'interrupted during replacement'):
                independent.apply(ready, self.root)
        self.assertEqual('old', (self.root / 'gui.py').read_text())
        self.assertFalse((self.root / 'assets/new.txt').exists())
        self.assertFalse(independent.trial_pending(self.root))
        self.assertFalse(ready.exists())

    def test_failed_start_without_config_changes_discards_duplicate_config(self):
        ready = self.ready()
        independent.apply(ready, self.root)
        self.assertTrue(independent.recover(self.root))
        self.assertEqual('old data', (self.root / 'config/player.json').read_text())
        self.assertFalse(ready.exists())

    def test_startup_cleans_duplicate_after_legacy_recovery_removed_previous(self):
        ready = self.ready()
        independent.write_json(ready / 'base.json', self.current['revision'])
        independent.apply(ready, self.root)
        with patch.object(independent, '_unchanged_failed_config', return_value=False):
            independent.recover(self.root)
        self.assertFalse((ready / 'previous').exists())
        self.assertTrue((ready / 'config-after-failed-update').exists())
        independent.clean_abandoned(self.root)
        self.assertFalse(ready.exists())

    def test_startup_recovers_pending_and_cleans_only_managed_abandoned_files(self):
        pending = self.ready()
        work = pending.parent
        abandoned = work / 'ready-abandoned'
        independent.write_json(abandoned / 'base.json', self.current['revision'])
        (abandoned / 'source.zip').touch()
        legacy = work / 'ready-legacy'
        legacy.mkdir()
        active = work / 'ready-active'
        independent.write_json(active / 'base.json', self.current['revision'])

        with patch.dict(independent._prepared_updates, {active: None}):
            independent.apply(pending, self.root)
            independent.clean_abandoned(self.root)
            self.assertTrue(abandoned.exists())
            self.assertTrue(pending.exists())

            (self.root / 'config/player.json').write_text('keep failed run data', encoding='utf-8')
            independent.recover(self.root)
            independent.clean_abandoned(self.root)
            self.assertFalse(abandoned.exists())
            self.assertTrue(active.exists())
        self.assertTrue(legacy.exists())
        self.assertFalse((pending / 'previous').exists())
        self.assertTrue((pending / 'config-after-failed-update/player.json').exists())

    def test_update_keeps_consistent_database_backup_after_confirming(self):
        self.root = self.root / 'install'
        independent.write_json(self.root / independent.RECEIPT, self.current)
        ready = self.ready()
        database = self.root / 'config/azurpilot.db'
        retained = self.root.parent / 'install_Data_Backup/before-update/azurpilot.db'
        writer = sqlite3.connect(database)
        self.addCleanup(writer.close)
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('CREATE TABLE history (value TEXT)')
        writer.execute('CREATE TABLE storage_migrations (version INTEGER)')
        writer.execute('INSERT INTO storage_migrations VALUES (2)')
        writer.execute('INSERT INTO history VALUES (?)', ('saved',))
        writer.commit()
        independent.apply(ready, self.root)
        independent.discard_ready(ready, self.root)
        with closing(sqlite3.connect(ready / 'previous/config/azurpilot.db')) as backup:
            self.assertEqual([('saved',)], backup.execute('SELECT value FROM history').fetchall())
        independent.confirm(self.root)
        self.assertFalse(independent.trial_pending(self.root))
        self.assertFalse(ready.exists())
        writer.execute('DELETE FROM history')
        writer.commit()
        empty = independent.backup_config(database.parent, self.root / 'empty-backup')
        independent._preserve_database_snapshot(self.root, empty)
        with closing(sqlite3.connect(f'{retained.as_uri()}?mode=ro', uri=True)) as backup:
            self.assertEqual([('saved',)], backup.execute('SELECT value FROM history').fetchall())

    def test_update_refuses_missing_database_from_migrated_installation(self):
        ready = self.ready()
        (self.root / 'config/azurpilot.migrated').touch()
        with self.assertRaisesRegex(RuntimeError, '缺少 azurpilot.db'):
            independent.apply(ready, self.root)
        self.assertEqual('old', (self.root / 'gui.py').read_text(encoding='utf-8'))
        self.assertEqual(self.current, independent.read_json(self.root / independent.RECEIPT))
        self.assertFalse(independent.trial_pending(self.root))

    def test_update_refuses_database_that_disappears_while_copying_config(self):
        ready = self.ready()
        database = self.root / 'config/azurpilot.db'
        database.touch()
        copytree = shutil.copytree

        def move_database_after_copy(source, destination, **kwargs):
            result = copytree(source, destination, **kwargs)
            if source == database.parent:
                database.rename(self.root / 'moved.db')
            return result

        with patch.object(independent.shutil, 'copytree', side_effect=move_database_after_copy):
            with self.assertRaises(sqlite3.OperationalError):
                independent.apply(ready, self.root)
        self.assertEqual('old', (self.root / 'gui.py').read_text(encoding='utf-8'))
        self.assertEqual(self.current, independent.read_json(self.root / independent.RECEIPT))
        self.assertFalse(independent.trial_pending(self.root))

    def test_update_manifest_cannot_remove_business_database(self):
        ready = self.ready()
        name = 'config/azurpilot.db'
        database = self.root / name
        database.write_bytes(b'local data')
        manifest = self.root / independent.FILES
        independent.write_json(manifest, {**independent.read_json(manifest), name: independent.digest(database)})
        with self.assertRaisesRegex(ValueError, '更新包路径无效'):
            independent.apply(ready, self.root)
        self.assertEqual(b'local data', database.read_bytes())
        self.assertFalse(independent.trial_pending(self.root))

    def test_already_installed_files_only_update_the_version_record(self):
        ready = self.ready()
        for name in ('gui.py', 'assets/new.txt'):
            independent.backup_file(ready / 'app' / name, self.root / name)
        with (
            patch.object(independent, 'restore_file') as write_file,
            patch.object(independent, 'backup_config') as backup_config,
        ):
            independent.apply(ready, self.root)
        write_file.assert_not_called()
        backup_config.assert_not_called()
        self.assertEqual('2' * 40, independent.read_json(self.root / independent.RECEIPT)['revision'])
        self.assertEqual(independent.digest(self.root / 'gui.py'),
                         independent.read_json(self.root / independent.FILES)['gui.py'])
        self.assertFalse(independent.trial_pending(self.root))
        self.assertFalse(ready.exists())

    def test_rollback_keeps_matching_files_that_were_already_present(self):
        ready = self.ready()
        independent.backup_file(ready / 'app/gui.py', self.root / 'gui.py')
        independent.apply(ready, self.root)
        journal = independent.read_json(self.root / independent.WORK / 'transaction.json')
        self.assertEqual(['assets/new.txt'], journal['changed'])
        self.assertTrue(independent.recover(self.root))
        self.assertEqual('new', (self.root / 'gui.py').read_text())
        self.assertFalse((self.root / 'assets/new.txt').exists())
        self.assertEqual(self.current, independent.read_json(self.root / independent.RECEIPT))

    def test_local_edit_refuses_replacement(self):
        ready = self.ready()
        (self.root / 'gui.py').write_text('local edit', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, '本机修改'):
            independent.apply(ready, self.root)
        self.assertEqual('local edit', (self.root / 'gui.py').read_text())
        self.assertFalse(independent.trial_pending(self.root))
        self.assertFalse(ready.exists())
