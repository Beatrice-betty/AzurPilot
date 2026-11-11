"""本地统计标识跨升级保持稳定。"""

import json
import sqlite3
from contextlib import closing
from uuid import UUID

from module.base.device_id import _local_id, get_device_id, read_legacy_device_ids
from module.persistence.database import get_database, use_database


def test_existing_statistics_id_is_read_without_rewriting(tmp_path):
    path = tmp_path / 'log' / 'device_id.json'
    path.parent.mkdir()
    saved = json.dumps({'device_id': 'a' * 32, '_generated_by': 'legacy'}).encode()
    path.write_bytes(saved)
    with use_database(get_database(tmp_path / 'config')):
        assert get_device_id() == 'a' * 32
    assert path.read_bytes() == saved


def test_new_statistics_id_is_random_and_stable(tmp_path):
    directory = tmp_path / 'config'
    first = _local_id(directory)
    assert UUID(first).version == 4
    _local_id.cache_clear()
    assert _local_id(directory) == first
    assert _local_id(tmp_path / 'another' / 'config') != first


def test_saved_backup_id_can_be_used_for_legacy_stats(tmp_path):
    backup = tmp_path / 'log' / 'device_id.json.bak'
    backup.parent.mkdir()
    backup.write_text(json.dumps({'device_id': 'b' * 32}), encoding='utf-8')
    assert read_legacy_device_ids(tmp_path) == ('b' * 32,)
    assert _local_id(tmp_path / 'config') == 'b' * 32


def test_restore_from_database_when_log_id_is_missing(tmp_path):
    directory = tmp_path / 'config'
    directory.mkdir()
    with closing(sqlite3.connect(directory / 'azurpilot.db')) as connection, connection:
        connection.execute('CREATE TABLE opsi_items (device_id TEXT)')
        connection.execute('CREATE TABLE farming_aggregates (device_id TEXT)')
        connection.executemany('INSERT INTO opsi_items VALUES (?)', [('a' * 32,), ('a' * 32,)])
        connection.executemany('INSERT INTO farming_aggregates VALUES (?)', [(None,), ('',)])
    assert _local_id(directory) == 'a' * 32
