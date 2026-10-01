"""直接落盘的生命周期诊断，不依赖业务日志锁或跨进程 Manager。"""

import faulthandler
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

SESSION_ENV = 'AZURPILOT_DIAGNOSTIC_SESSION'
LOG_ROOT = Path(__file__).resolve().parents[2] / 'log' / 'diagnostics'
_session = None


def exit_code_fields(code):
    """保留 Windows 异常退出码的无符号十六进制表示。"""
    return {'exit_code': code, 'exit_code_hex': f'0x{code & 0xffffffff:08X}' if isinstance(code, int) else None}


def emit(event, **fields):
    """仅写已初始化进程的诊断；失败不能影响业务或递归调用日志器。"""
    if _session is not None and _session.pid == os.getpid():
        _session.emit(event, **fields)


class DiagnosticSession:
    """每个进程独立的追加日志、心跳及无局部变量的线程栈。"""

    def __init__(self, role, state=None, root=None):
        session = os.environ.get(SESSION_ENV, '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', session):
            session = uuid.uuid4().hex
            os.environ[SESSION_ENV] = session
        self.session = session
        self.pid = os.getpid()
        self.role = role
        self.state = state or (lambda: {})
        self.root = Path(root) if root is not None else LOG_ROOT
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / f'{session}-{self.pid}.jsonl'
        self.request_path = self.root / f'{session}.dump-request'
        self.stack_path = self.root / f'{session}-{self.pid}.stacks.txt'
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.last_request = None
        self.stack_file = None
        self.thread = None
        try:
            import psutil
            self.created_at = psutil.Process(self.pid).create_time()
        except Exception:
            self.created_at = None

    def emit(self, event, **fields):
        """追加并刷新单条结构化事件，禁止传入配置、命令行或异常原文。"""
        try:
            now = datetime.now().astimezone()
            row = dict(fields, event=event, session_id=self.session, role=self.role,
                       pid=self.pid, ppid=os.getppid(), created_at=self.created_at,
                       local_time=now.isoformat(), utc_time=now.astimezone(timezone.utc).isoformat(),
                       monotonic=time.monotonic())
            # 非阻塞取锁：诊断磁盘卡住时不能再拖死业务线程。
            if not self.lock.acquire(blocking=False):
                return
            try:
                with self.path.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(row, ensure_ascii=False) + '\n')
                    stream.flush()
            finally:
                self.lock.release()
        except Exception:
            pass

    def sample(self):
        """记录白名单状态，并按独立观察器请求捕获所有线程栈。"""
        try:
            state = self.state()
        except Exception as exc:
            state = {'state_error_type': type(exc).__name__}
        self.emit('heartbeat', **state)
        try:
            request = self.request_path.read_text(encoding='utf-8')[:160]
            if request != self.last_request and self.stack_file is not None:
                self.last_request = request
                self.emit('stack_capture', trigger_reason='observer_health_failure')
                self.stack_file.write(f'\n{datetime.now().astimezone().isoformat()} PID={self.pid}\n')
                self.stack_file.flush()
                faulthandler.dump_traceback(file=self.stack_file, all_threads=True)
        except OSError:
            pass

    def start(self):
        """诊断线程不接管进程退出或重启，原生异常由 faulthandler 留栈。"""
        self.stack_file = self.stack_path.open('a', encoding='utf-8')
        faulthandler.enable(file=self.stack_file, all_threads=True)
        self.emit('process_start')

        def loop():
            while not self.stop_event.is_set():
                self.sample()
                self.stop_event.wait(5)

        self.thread = threading.Thread(target=loop, daemon=True, name='lifecycle-diagnostics')
        self.thread.start()


def start_process(role, state=None):
    """启动一次进程诊断；独立工具或单测不因导入而创建文件。"""
    global _session
    if _session is not None and _session.pid == os.getpid():
        if state is not None:
            _session.state = state
        return
    try:
        _session = DiagnosticSession(role, state)
        _session.start()
    except Exception:
        _session = None
