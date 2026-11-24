import hashlib
import io
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from deploy import independent, independent_runtime


class TestIndependentRuntime(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.python = self.root / independent.BUNDLED_PYTHON
        self.candidate = {'pythonEnvironment': 'ready-candidate'}
        self.candidate_python = independent.installed_python(self.root, self.candidate)

    def test_relaunch_uses_new_interpreter_and_preserves_arguments(self):
        receipt = self.root / independent.RECEIPT
        independent.write_json(receipt, {})

        def run(command, **kwargs):
            self.assertEqual(command[-2:], ['--port', '24680'])
            self.assertEqual(kwargs['env'][independent_runtime._LAUNCH_CHILD], '1')
            self.assertNotIn(independent_runtime._HOT_RELOAD, kwargs['env'])
            if command[0] == self.python:
                independent.write_json(receipt, self.candidate)
                return subprocess.CompletedProcess(command, independent_runtime.RELAUNCH)
            self.assertEqual(command[0], self.candidate_python)
            return subprocess.CompletedProcess(command, 0)

        with patch.object(independent_runtime.subprocess, 'run', side_effect=run) as launch:
            self.assertEqual(independent_runtime.launch_console(['--port', '24680'], root=self.root), 0)
        self.assertEqual(launch.call_count, 2)

    def test_failed_candidate_start_recovers_through_original_bundled_script(self):
        journal = self.root / independent.WORK / 'transaction.json'
        independent.write_json(journal, {'phase': 'trial'})
        receipt = self.root / independent.RECEIPT
        independent.write_json(receipt, self.candidate)

        def run(command, **kwargs):
            if command[0] == self.candidate_python:
                raise OSError('candidate executable unavailable')
            self.assertEqual(command[0], self.python)
            if command[-1] == 'recover':
                self.assertEqual(command[-2], self.root / 'runtime/independent_update.py')
                journal.unlink()
                independent.write_json(receipt, {})
            return subprocess.CompletedProcess(command, 0)

        with patch.object(independent_runtime.subprocess, 'run', side_effect=run) as launch:
            self.assertEqual(independent_runtime.launch_console([], root=self.root), 0)
        self.assertEqual(launch.call_count, 3)

    def test_sync_uses_locked_dependencies_in_the_candidate_environment(self):
        project = self.root / 'project'
        project.mkdir()
        uv = self.root / 'uv.exe'
        options = ['--python', str(self.python), '--no-python-downloads']

        def run(command, **kwargs):
            self.assertEqual(command[:2], [uv, 'sync'])
            self.assertIn('--locked', command)
            self.assertIn('--no-dev', command)
            self.assertEqual(command[-3:], options)
            self.assertEqual(kwargs['cwd'], project)
            self.assertEqual(kwargs['env']['UV_PROJECT_ENVIRONMENT'], str(self.candidate_python.parent.parent))
            self.candidate_python.parent.mkdir(parents=True)
            self.candidate_python.touch()

        with (
            patch.dict(os.environ, {'UV_PROJECT_ENVIRONMENT': 'existing-environment'}),
            patch.object(independent_runtime, '_uv_executable', return_value=uv),
            patch.object(independent_runtime, '_python_options', return_value=options),
            patch.object(independent_runtime.subprocess, 'run', side_effect=run),
        ):
            self.assertEqual(independent_runtime.sync_environment(self.root, 'ready-candidate', project, {}),
                             self.candidate_python)

    def test_python_requirement_selects_managed_interpreter_if_packaged_version_is_incompatible(self):
        project = self.root / 'project'
        project.mkdir()
        (project / 'pyproject.toml').write_text('[project]\nrequires-python = ">=3.15,<3.16"\n', encoding='utf-8')
        self.python.parent.mkdir(parents=True)
        self.python.touch()
        with patch.object(independent_runtime.subprocess, 'check_output', return_value='Python 3.14.10\n'):
            self.assertEqual(independent_runtime._python_options(self.root, project, {}),
                             ['--python', '>=3.15,<3.16', '--managed-python'])

    def test_missing_uv_executable_can_use_a_verified_wheel_from_the_installed_lock(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as archive:
            archive.writestr('uv-1.data/scripts/uv.exe', b'uv executable fixture')
        checksum = hashlib.sha256(data.getvalue()).hexdigest()
        lock = self.root / 'uv.lock'
        lock.write_text(
            '[[package]]\nname = "uv"\nwheels = [\n'
            f' {{ url = "https://example.test/uv-1-win_amd64.whl", hash = "sha256:{checksum}" }},\n]\n',
            encoding='utf-8',
        )
        with patch.object(independent_runtime, 'fetch', return_value=b'wrong wheel'):
            with self.assertRaisesRegex(RuntimeError, '校验失败'):
                independent_runtime._uv_executable(self.root, {}, self.root)
            self.assertFalse((self.root / 'uv.exe').exists())
        with patch.object(independent_runtime, 'fetch', return_value=data.getvalue()):
            exe = independent_runtime._uv_executable(self.root, {}, self.root)
        self.assertEqual(exe.read_bytes(), b'uv executable fixture')

    def test_busy_candidate_environment_is_only_removed_after_its_interpreter_exits(self):
        environment = self.candidate_python.parent.parent
        environment.mkdir(parents=True)
        with patch.object(independent.sys, 'prefix', str(environment)):
            independent._remove_update_tree(environment)
        self.assertTrue(environment.is_dir())
        independent._remove_update_tree(environment)
        self.assertFalse(environment.exists())
