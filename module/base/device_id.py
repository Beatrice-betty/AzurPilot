"""本地统计归属标识，兼容旧数据使用的设备 ID。"""

import json
import sqlite3
from contextlib import closing
from functools import cache
from uuid import uuid4

from deploy.atomic import atomic_write
from deploy.config_transaction import config_transaction


def _read_saved_id(path):
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    value = data.get('device_id') if isinstance(data, dict) else None
    return value if isinstance(value, str) and value else None


def read_legacy_device_ids(root):
    """读取旧标识和备份，供本地统计归属与密文解码使用。"""
    log = root / 'log'
    paths = (log / 'device_id.json', log / 'device_id.json.bak',
             log / 'cl1' / 'cl1_monthly.json', log / 'cl1' / 'cl1_monthly.json.bak')
    return tuple(dict.fromkeys(value for path in paths if (value := _read_saved_id(path))))


def _id_from_statistics(path):
    """只恢复唯一可识别的归属，避免合并不同设备的历史记录。"""
    if not path.is_file():
        return None
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as connection:
            rows = connection.execute("""
                SELECT device_id FROM (
                    SELECT DISTINCT device_id FROM opsi_items WHERE device_id <> '' LIMIT 2
                )
                UNION
                SELECT device_id FROM (
                    SELECT DISTINCT device_id FROM farming_aggregates WHERE device_id <> '' LIMIT 2
                )
                LIMIT 2
            """).fetchall()
    except sqlite3.Error:
        return None
    return rows[0][0] if len(rows) == 1 else None


@cache
def _local_id(directory):
    path = directory.parent / 'log' / 'device_id.json'
    value = _read_saved_id(path)
    if value:
        return value
    with config_transaction(path):
        value = _read_saved_id(path)
        if value:
            return value
        legacy = read_legacy_device_ids(directory.parent)
        value = next(iter(legacy), None) or _id_from_statistics(directory / 'azurpilot.db') or uuid4().hex
        atomic_write(str(path), json.dumps({'device_id': value}))
        return value


def get_device_id():
    """复用已有统计标识，首次安装时生成仅用于本地存储的随机值。"""
    from module.persistence.database import current_directory

    return _local_id(current_directory().resolve())
