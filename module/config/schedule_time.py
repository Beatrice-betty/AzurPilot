"""任务排程保存实际时刻，配置界面仍使用本地时间。"""

import os
import time
from datetime import datetime, timezone

from module.config.config_generated import GeneratedConfig
from module.config.transaction import config_transaction
from module.config.utils import DEFAULT_TIME, read_file, write_file


if os.name == 'nt':
    from ctypes import CDLL

    _tzset = CDLL('ucrtbase')._tzset
    _tzset.restype = None
else:
    _tzset = getattr(time, 'tzset', None)


def refresh_local_timezone():
    """刷新进程的本地时区缓存。Windows 的 CRT 首次读取后会缓存时区。"""
    if _tzset is not None:
        _tzset()


def is_next_run_path(keys):
    path = keys.split('.') if isinstance(keys, str) else keys
    return isinstance(path, (list, tuple)) and len(path) == 3 and path[1] == 'Scheduler' and path[2] == 'NextRun'


def parse_next_run(value):
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return value if isinstance(value, datetime) else None


def _to_utc(value):
    try:
        return value.astimezone(timezone.utc)
    except (OSError, OverflowError, ValueError):
        # Windows 的本地时间转换不支持某些很早或很晚的日期。
        return None


def schedule_utc(value):
    """将排程时间或当前时间转换为可以按实际先后比较的 UTC 时间。"""
    return _to_utc(value) or value.replace(tzinfo=timezone.utc)


def schedule_local(value):
    """保留任务使用的本地 datetime 接口，包括夏令时回拨时的 fold。"""
    if not isinstance(value, datetime) or value.tzinfo is None:
        return value
    try:
        return datetime.fromtimestamp(value.timestamp())
    except (OSError, OverflowError, ValueError):
        return value.replace(tzinfo=None)


def normalize_next_run(value):
    """固定已排定的实际时刻，保留代表立即运行的默认值和无法转换的旧值。"""
    moment = parse_next_run(value)
    if moment is None or moment in (DEFAULT_TIME, GeneratedConfig.Scheduler_NextRun):
        return value
    return _to_utc(moment) or value


def encode_next_run(value):
    value = normalize_next_run(value)
    return str(value) if isinstance(value, datetime) else value


def display_next_run(value):
    moment = parse_next_run(value)
    if moment is not None and moment.tzinfo is not None:
        return str(schedule_local(moment))
    return value


def map_next_runs(data, convert):
    """仅转换实例各任务的 Scheduler.NextRun，返回是否有字段变化。"""
    if not isinstance(data, dict):
        return False
    changed = False
    for groups in data.values():
        if not isinstance(groups, dict):
            continue
        scheduler = groups.get('Scheduler')
        if not isinstance(scheduler, dict) or 'NextRun' not in scheduler:
            continue
        value = convert(scheduler['NextRun'])
        if value != scheduler['NextRun']:
            scheduler['NextRun'] = value
            changed = True
    return changed


def read_schedule_config(path):
    """首次读取旧实例时固定已有排程，只写回实际变化的 NextRun。"""
    data = read_file(path)
    if map_next_runs(data, encode_next_run):
        with config_transaction(path):
            data = read_file(path)
            if map_next_runs(data, encode_next_run):
                write_file(path, data)
    return data
