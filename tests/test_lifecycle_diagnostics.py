"""诊断输出必须独立落盘、保留运行历史，且不改变原有终止语义。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from module.runtime import diagnostics
from module.runtime import process_control


class TestLifecycleDiagnostics(unittest.TestCase):
    def setUp(self):
        # 会话初始化会设置环境变量；测试结束必须恢复，避免后续监督器单测
        # 因继承测试会话而启动真实诊断线程，干扰其模拟对象与临时目录。
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop(diagnostics.SESSION_ENV, None)

    def test_append_preserves_previous_session_records(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {diagnostics.SESSION_ENV: 'test-session'}):
            first = diagnostics.DiagnosticSession('gui-supervisor', root=directory)
            first.emit('process_start')
            second = diagnostics.DiagnosticSession('webui', root=directory)
            second.emit('process_start')
            rows = [json.loads(line) for line in first.path.read_text(encoding='utf-8').splitlines()]
            self.assertEqual([row['role'] for row in rows], ['gui-supervisor', 'webui'])
            self.assertIn('ppid', rows[0])
            self.assertIn('+', rows[0]['local_time'])
            self.assertTrue(rows[0]['utc_time'].endswith('+00:00'))

    def test_snapshot_and_stack_request_do_not_use_business_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            session = diagnostics.DiagnosticSession('webui', lambda: {'restart_event': True}, root=directory)
            session.request_path.write_text('request-1', encoding='utf-8')
            with session.stack_path.open('a', encoding='utf-8') as stack:
                session.stack_file = stack
                with patch.object(diagnostics.faulthandler, 'dump_traceback') as dump:
                    session.sample()
                    session.sample()
                    dump.assert_called_once_with(file=stack, all_threads=True)
            rows = [json.loads(line) for line in session.path.read_text(encoding='utf-8').splitlines()]
            self.assertTrue(rows[0]['restart_event'])
            self.assertEqual(rows[1]['event'], 'stack_capture')

    def test_snapshot_error_does_not_leak_exception_details(self):
        with tempfile.TemporaryDirectory() as directory:
            session = diagnostics.DiagnosticSession('webui', Mock(side_effect=RuntimeError('secret-token')), root=directory)
            session.sample()
            content = session.path.read_text(encoding='utf-8')
            self.assertNotIn('secret-token', content)
            self.assertIn('RuntimeError', content)

    def test_diagnostic_disk_error_does_not_raise(self):
        with tempfile.TemporaryDirectory() as directory:
            session = diagnostics.DiagnosticSession('webui', root=directory)
            session.path = Path(directory) / 'missing' / 'log.jsonl'
            session.emit('process_start')

    def test_exit_code_keeps_windows_status(self):
        fields = diagnostics.exit_code_fields(-1073741819)
        self.assertEqual(fields['exit_code_hex'], '0xC0000005')

    def test_real_child_captures_stacks_before_abrupt_exit(self):
        # 独立临时子进程验证真实 faulthandler 和直接落盘；不启动 WebUI 或游戏。
        script = '''
import os, sys
from module.runtime.diagnostics import DiagnosticSession
session = DiagnosticSession('test-worker', lambda: {'restart_event': False}, root=sys.argv[1])
session.request_path.write_text('test-request', encoding='utf-8')
session.start()
session.stop_event.wait(0.2)
session.stop_event.set()
session.thread.join(2)
os._exit(23)
'''
        with tempfile.TemporaryDirectory() as directory:
            child = subprocess.run([sys.executable, '-c', script, directory], timeout=10,
                                   capture_output=True)
            self.assertEqual(child.returncode, 23, child.stderr)
            rows = [json.loads(line) for path in Path(directory).glob('*.jsonl')
                    for line in path.read_text(encoding='utf-8').splitlines()]
            self.assertIn('stack_capture', [row['event'] for row in rows])
            self.assertTrue(all(row['pid'] != os.getpid() for row in rows))
            stacks = next(Path(directory).glob('*.stacks.txt')).read_text(encoding='utf-8')
            self.assertIn('diagnostics.py', stacks)
            self.assertIn('PID=', stacks)

    def test_kill_logs_intent_before_signal_and_preserves_failure(self):
        calls = []
        process = Mock(pid=12345)
        process.kill.side_effect = lambda: calls.append('kill')
        with patch.object(process_control, 'emit', side_effect=lambda event, **kw: calls.append(event)):
            process_control.trace_kill(process, 'test', created_at=1.0)
        self.assertEqual(calls, ['terminate_intent', 'kill', 'terminate_result'])
        process.kill.side_effect = PermissionError('denied')
        with self.assertRaises(PermissionError):
            process_control.trace_kill(process, 'test', created_at=1.0)


if __name__ == '__main__':
    unittest.main()
