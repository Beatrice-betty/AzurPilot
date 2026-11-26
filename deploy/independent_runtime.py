"""独立版的候选 Python 环境和与现有桌面启动器兼容的入口。"""

import hashlib
import io
import os
import subprocess
import sys
import time
import tomllib
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from deploy.independent import (
    BUNDLED_PYTHON,
    CREATE_FLAGS,
    PYTHON_ENVIRONMENTS,
    WORK,
    _environment_directory,
    fetch,
    installed_python,
)

RELAUNCH = 79  # 仅在下面的父子进程之间传递，不返回给桌面启动器。
_LAUNCH_CHILD = 'AZURPILOT_INDEPENDENT_LAUNCH_CHILD'
_HOT_RELOAD = 'AZURPILOT_INDEPENDENT_PYTHON_RELOAD'
_SYNC_TIMEOUT = 240


def is_launch_child():
    return os.environ.get(_LAUNCH_CHILD) == '1'


def mark_hot_reload():
    if is_launch_child():
        os.environ[_HOT_RELOAD] = '1'


def supports_hot_reload():
    return os.environ.get(_HOT_RELOAD) == '1'


def needs_relaunch(root):
    return supports_hot_reload() and installed_python(root) != Path(sys.executable).resolve()


def launch_console(args, *, root=None):
    """保持包内 Python 启动的父进程可用，以便切换或恢复 WebUI 的解释器。"""
    root = Path(root or Path.cwd()).resolve()
    journal = root / WORK / 'transaction.json'
    env = os.environ.copy()
    for name in (_HOT_RELOAD, 'PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', '__PYVENV_LAUNCHER__'):
        env.pop(name, None)
    env.update({_LAUNCH_CHILD: '1', 'PYTHONNOUSERSITE': '1'})
    while True:
        try:
            python = installed_python(root)
            result = subprocess.run([python, '-B', '-u', root / 'gui.py', *args], cwd=root, env=env)
            if result.returncode == RELAUNCH:
                if installed_python(root) != python:
                    continue
                raise RuntimeError('WebUI 请求切换 Python，但安装记录未改变')
            if result.returncode == 0 or not journal.is_file():
                return result.returncode
        except (OSError, ValueError, RuntimeError) as exc:
            print(f'[GUI] Python 环境启动失败 {exc}', file=sys.stderr, flush=True)
            if not journal.is_file():
                return 70

        print('[GUI] 新版未能完成启动，正在恢复上一版', file=sys.stderr, flush=True)
        result = subprocess.run([root / BUNDLED_PYTHON, '-B', root / 'runtime/independent_update.py', 'recover'],
                                cwd=root, env=env)
        if result.returncode or journal.is_file():
            return result.returncode or 70


def _checked_directory(root, relative):
    path = root / relative
    if path.resolve() != path:
        raise ValueError('Python 更新目录不能是链接')
    return path


def _uv_executable(root, current, tools):
    active = installed_python(root, current)
    bundled = root / BUNDLED_PYTHON.parent
    for exe in (active.parent / 'uv.exe', bundled / 'Scripts/uv.exe', bundled / 'Lib/site-packages/bin/uv.exe'):
        if exe.is_file():
            return exe

    # 旧完整包可能只有 uv 的 Python 模块，使用本机已验证锁文件中的 Windows wheel 引导。
    lock = tomllib.loads((root / 'uv.lock').read_text(encoding='utf-8'))
    for package in lock.get('package', []):
        if package['name'] != 'uv':
            continue
        for wheel in package.get('wheels', []):
            url = wheel['url']
            parsed = urlsplit(url)
            if parsed.scheme != 'https' or not parsed.path.endswith('-win_amd64.whl'):
                continue
            data = fetch(url, 40 * 1024 * 1024)
            checksum = hashlib.sha256(data).hexdigest()
            if wheel.get('hash') != f'sha256:{checksum}':
                raise RuntimeError('uv 安装工具校验失败，继续使用当前版')
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    member, = [name for name in archive.namelist() if name.endswith('.data/scripts/uv.exe')]
                    exe = tools / 'uv.exe'
                    exe.write_bytes(archive.read(member))
                    return exe
            except (ValueError, zipfile.BadZipFile) as exc:
                raise RuntimeError('uv 安装工具解包失败，继续使用当前版') from exc
    raise RuntimeError('未找到适用于此独立版的 uv 安装工具，继续使用当前版')


def _python_options(root, project, current):
    from packaging.specifiers import SpecifierSet

    metadata = tomllib.loads((project / 'pyproject.toml').read_text(encoding='utf-8'))
    requires = metadata.get('project', {}).get('requires-python', '')
    specifier = SpecifierSet(requires)
    for python in dict.fromkeys((installed_python(root, current), root / BUNDLED_PYTHON)):
        if not python.is_file():
            continue
        try:
            version = subprocess.check_output([python, '-I', '-B', '--version'], text=True, timeout=10,
                                              creationflags=CREATE_FLAGS).strip().removeprefix('Python ')
        except (OSError, subprocess.SubprocessError):
            continue
        if specifier.contains(version, prereleases=True):
            return ['--python', str(python), '--no-python-downloads']
    return ['--python', requires, '--managed-python'] if requires else ['--managed-python']


def sync_environment(root, name, project, current):
    """只在候选目录安装目标提交的生产依赖，保留当前环境供运行和回退。"""
    from deploy.uv import can_retry_locked_mirrors, command_output, redact_sensitive_text, sync_locked_mirrors

    environment = _environment_directory(root, name)
    storage = PYTHON_ENVIRONMENTS.parent
    cache = _checked_directory(root, storage / 'cache')
    interpreters = _checked_directory(root, storage / 'interpreters')
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith('UV_') and key.upper() not in ('VIRTUAL_ENV', 'CONDA_PREFIX', 'PYTHONPATH', 'PYTHONHOME')}
    if mirror := os.environ.get('UV_PYTHON_INSTALL_MIRROR'):
        env['UV_PYTHON_INSTALL_MIRROR'] = mirror
    env.update(UV_CACHE_DIR=str(cache), UV_PYTHON_INSTALL_DIR=str(interpreters),
               UV_PROJECT_ENVIRONMENT=str(environment), UV_NO_PROGRESS='1', PYTHONNOUSERSITE='1')
    uv = _uv_executable(root, current, project)
    python = environment / 'Scripts/python.exe'
    deadline = time.monotonic() + _SYNC_TIMEOUT
    print('正在准备候选版本的 Python 环境', flush=True)
    try:
        try:
            subprocess.run(
                [uv, 'sync', '--locked', '--no-dev', '--no-install-project',
                 '--link-mode', 'copy', *_python_options(root, project, current)],
                cwd=project, env=env, capture_output=True, encoding='utf-8', errors='replace',
                creationflags=CREATE_FLAGS, check=True, timeout=_SYNC_TIMEOUT,
            )
        except subprocess.CalledProcessError:
            if not python.is_file() or not can_retry_locked_mirrors(root, project, env):
                raise
            sync_locked_mirrors(uv, project, python, dict(env, UV_LINK_MODE='copy'), lock_option='--locked',
                                deadline=deadline, outputs=[], creationflags=CREATE_FLAGS)
    except subprocess.CalledProcessError as exc:
        details = redact_sensitive_text(exc.stderr or command_output(exc)).strip()[-1200:]
        raise RuntimeError(f'候选版 Python 依赖同步失败，继续使用当前版\n{details}') from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError('候选版 Python 依赖同步未完成，继续使用当前版') from exc
    if not python.is_file():
        raise RuntimeError('候选版未生成可用的 Python 环境，继续使用当前版')
    return python
