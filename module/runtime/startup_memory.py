"""启动时记忆运行：退出时记下正在运行的实例，下次启动据此恢复。

开关与上次记录都是运行态，存放在部署配置同目录的 startup_memory.json（该路径在 .gitignore 内）。
"""
import json
from pathlib import Path
from typing import Any, Iterable

from module.logger import logger
from module.runtime.setting import State

MEMORY_NAME = 'startup_memory.json'


def memory_path() -> Path:
    """与部署配置同目录，跟随应用自己的 root。"""
    file = getattr(State.deploy_config, 'file', None)
    return Path(file).with_name(MEMORY_NAME) if file else Path('config') / MEMORY_NAME


def _names(value: Any) -> list[str]:
    return [name for name in value if isinstance(name, str)] if isinstance(value, list) else []


def _read() -> dict[str, list[str]]:
    """文件缺失或损坏都按未启用处理，不让记忆影响启动。"""
    try:
        data = json.loads(memory_path().read_text(encoding='utf-8'))
    except FileNotFoundError:
        data = None
    except (OSError, ValueError):
        logger.exception('启动时记忆运行的文件无法读取，按未启用处理')
        data = None
    if not isinstance(data, dict):
        return {'remember': [], 'last': []}
    return {'remember': _names(data.get('remember')), 'last': _names(data.get('last'))}


def _write(remember: list[str], last: list[str]) -> None:
    path = memory_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({'remember': remember, 'last': last}, ensure_ascii=False, indent=2)
        path.write_text(payload, encoding='utf-8')
    except OSError:
        logger.exception('启动时记忆运行的文件无法写入')


def get_startup_remember(instance: str) -> bool:
    return instance in _read()['remember']


def set_startup_remember(instance: str, remember: bool) -> bool:
    from module.runtime.deploy_settings import is_demo_mode
    if is_demo_mode():
        raise PermissionError('演示模式下不能修改启动时记忆运行')

    data = _read()
    names = data['remember']
    if remember:
        if instance not in names:
            names.append(instance)
    else:
        names = [name for name in names if name != instance]
    _write(names, data['last'])
    return instance in names


def remembered_runs() -> list[str]:
    """上次退出时正在运行、且现在仍启用记忆的实例。"""
    data = _read()
    return [name for name in data['last'] if name in data['remember']]


def record_running(instances: Iterable[str]) -> None:
    """记下退出那一刻仍在运行的实例，只保留启用记忆的那些。"""
    remember = _read()['remember']
    if not remember:
        return
    _write(remember, sorted({name for name in instances if name in remember}))
