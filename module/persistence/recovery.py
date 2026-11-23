"""独立版在正式总库缺失时，从本机可信备份恢复或建立可用的空总库。"""
import json
import os
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime
from uuid import uuid4

from deploy.atomic import atomic_write
from module.config.transaction import config_transaction
from module.logger import logger
from module.persistence.database import SCHEMA_V1, VERSION, create_month_rows, create_schema, read_storage_version
from module.persistence.migration import assert_no_workers, fingerprint


def _backups(database):
    """只枚举本安装的每日备份、更新前快照和 v1 升级前快照。"""
    root = database.directory.parent
    name = database.path.name
    daily = (root / 'AzurPilot_Data_Backup').glob(f'????-??-??/{name}')
    update = root.with_name(root.name + '_Data_Backup') / 'before-update' / name
    upgrades = (database.directory / 'storage-backups').glob(f'pre-v2-*/{name}')
    found = []
    for path in (*daily, update, *upgrades):
        try:
            if path.resolve() == path and path.is_file():
                found.append((path.stat().st_mtime_ns, path))
        except OSError:
            logger.warning('[存储恢复] 无法访问备份：%s', path)
    return [path for _, path in sorted(found, reverse=True)]


def _recover_backup(database, staged):
    """保持备份原样，仅发布通过校验且与缺失总库对应的副本。"""
    expected = database.marker.read_text(encoding='utf-8', errors='replace').splitlines()
    for candidate in _backups(database):
        try:
            # 程序生成的备份是自包含快照。若发现写入日志，先保留给离线检查。
            if any(sidecar.stat().st_size for suffix in ('-wal', '-journal')
                   if (sidecar := candidate.with_name(candidate.name + suffix)).is_file()):
                continue
            with closing(sqlite3.connect(candidate.as_uri() + '?mode=ro&immutable=1', uri=True)) as connection:
                version, digest = read_storage_version(connection)
            matches = expected == [str(version), digest]
            if version == SCHEMA_V1 and candidate.parent.name.startswith('pre-v2-') and not matches:
                matches = expected == [str(VERSION), fingerprint(candidate)]
            if not matches:
                continue
            shutil.copyfile(candidate, staged)
            with closing(sqlite3.connect(staged.as_uri() + '?mode=ro&immutable=1', uri=True)) as connection:
                if (read_storage_version(connection) != (version, digest)
                        or connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok'
                        or connection.execute('PRAGMA foreign_key_check').fetchone()):
                    raise ValueError('备份未通过版本、完整性或外键检查')
            return str(candidate.relative_to(database.directory.parent.parent))
        except (OSError, ValueError, sqlite3.Error) as exc:
            staged.unlink(missing_ok=True)
            logger.warning('[存储恢复] 跳过无法使用的备份 %s：%s', candidate, exc)
    return None


def recover_missing_database(database):
    """在安装锁保护下准备数据库，保留原迁移标记和遗留 SQLite 文件。"""
    assert_no_workers(database.directory.parent, supervisor_pid=database.webui_supervisor_pid)
    archive = database.directory / 'storage-backups' / (
        'missing-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid4().hex[:8])
    if archive.parent.resolve() != archive.parent:
        raise ValueError('总库恢复目录不能通过链接写入')
    archive.mkdir(parents=True)
    staged = archive / 'recovered.db'
    try:
        logger.info('[存储恢复] 正式总库缺失，开始核对本地备份')
        shutil.copyfile(database.marker, archive / database.marker.name)
        source = _recover_backup(database, staged)
        if source is None:
            with closing(sqlite3.connect(staged)) as connection, connection:
                connection.execute('PRAGMA journal_mode=WAL')
                create_schema(connection)
                connection.execute('BEGIN IMMEDIATE')
                create_month_rows(connection)
                connection.execute('INSERT INTO storage_migrations VALUES(?,?,?)',
                                   (VERSION, datetime.now().isoformat(), uuid4().hex))
                connection.execute(f'PRAGMA user_version={VERSION}')
        with staged.open('rb+') as file:
            os.fsync(file.fileno())
        if database.path.exists() or database.path.is_symlink():
            raise FileExistsError('总库已由外部恢复，请重新读取')
        for suffix in ('-wal', '-shm', '-journal'):
            sidecar = database.path.with_name(database.path.name + suffix)
            try:
                sidecar.rename(archive / sidecar.name)
            except FileNotFoundError:
                pass
        # Windows 的 rename 和 POSIX 的 link 均拒绝覆盖刚由用户放回的总库。
        if os.name == 'nt':
            staged.rename(database.path)
        else:
            os.link(staged, database.path)
        return source or ''
    finally:
        for suffix in ('', '-wal', '-shm', '-journal'):
            staged.with_name(staged.name + suffix).unlink(missing_ok=True)


def record_recovery(database, digest, source):
    """用当前库的迁移摘要关联恢复提示，用户放回其他总库时自动消失。"""
    if source:
        message = f'普通业务数据库曾丢失，已从本地备份 {source} 恢复。备份之后的数据可能缺失。'
    else:
        message = '普通业务数据库曾丢失，未找到匹配的可用备份，已新建空库。原有的历史统计和调度方案需要从备份找回。'
    logger.warning('[存储恢复] %s', message)
    _write_recovery(database, {
        'marker': f'{VERSION}\n{digest}\n', 'message': message, 'awaiting_manual_start': not source,
    })


def _write_recovery(database, record):
    atomic_write(str(database.directory / 'storage-backups' / 'last-recovery.json'),
                 json.dumps(record, ensure_ascii=False))


def current_recovery(database):
    """恢复记录仅在当前正在使用的是该次恢复的库时生效。"""
    try:
        notice = json.loads((database.directory / 'storage-backups' / 'last-recovery.json').read_text(encoding='utf-8'))
        marker = database.marker.read_text(encoding='utf-8')
    except (OSError, ValueError):
        return {}
    if isinstance(notice, dict) and notice.get('marker') == marker and isinstance(notice.get('message'), str):
        return notice
    return {}


def record_manual_start(database):
    """空库可能缺少自定义调度方案，用户主动运行后才允许后续自动启动。"""
    if not current_recovery(database).get('awaiting_manual_start'):
        return
    with config_transaction(database.directory / '.azurpilot-install'):
        record = current_recovery(database)
        if record.get('awaiting_manual_start'):
            record['awaiting_manual_start'] = False
            _write_recovery(database, record)
