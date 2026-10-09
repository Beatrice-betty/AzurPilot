"""首次启动先备份，再只读转换旧存储；成功切换后不再导入原件。"""
import csv
import math
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
from contextlib import ExitStack, closing, contextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from module.config.transaction import config_transaction
from module.persistence.database import VERSION, create_schema, register_instance
from module.persistence.scheduler import save_persistent, save_program, write_observation
from module.persistence.snapshots import insert, read_month, read_ship, save_month, save_ship

DATABASE_KINDS = {'azurstats_local.db': 'statistics', 'cl1_data.db': 'cl1',
                  'storage_statistics.db': 'storage', 'daily_summary.db': 'daily'}
COPY_TABLES = ('resource_snapshots', 'resource_flows', 'resource_balances', 'opsi_items',
               'storage_scans', 'storage_items', 'daily_summary_task_runs', 'daily_summary_cl1_events',
               'daily_summary_periods', 'daily_summary_collection_state', 'daily_summary_collection_gaps')


class MigrationError(RuntimeError):
    """源数据仍保留，用户恢复环境后可重试。"""


def source_files(database):
    directory, root = database.directory, database.directory.parent
    result = {directory / name: kind for name, kind in DATABASE_KINDS.items()}
    old_cl1 = root / 'log' / 'cl1' / 'cl1_data.db'
    if not (directory / 'cl1_data.db').is_file():
        result[old_cl1] = 'cl1'
    for path in (directory / 'scheduler').glob('*.sqlite3'):
        result[path] = 'scheduler'
    for kind in ('programs', 'variables', 'observations'):
        for path in (directory / 'scheduler' / kind).glob('*.json'):
            result[path] = 'scheduler_' + kind
    for path in (root / 'log' / 'cl1').glob('*/ship_exp_data.json'):
        result[path] = 'ships'
    for path in (root / 'log' / 'cl1').glob('*/cl1_monthly.json'):
        result[path] = 'archives'
    for path in (root / 'log').glob('azurstat_meowofficer_farming*.csv'):
        result[path] = 'farming'
    for kind, paths in database.legacy_sources.items():
        for path in paths:
            result[path] = kind
    for path in list(result):
        if result[path] in ('ships', 'archives', 'farming'):
            result[path.with_name(path.name + '.bak')] = 'preserved'
    return {path.absolute(): kind for path, kind in result.items() if path.is_file()}


def assert_no_workers(root):
    """迁移不终止进程；可写源数据的旧运行进程存在时拒绝切换。"""
    import psutil
    from module.runtime.process_control import process_matches
    for path in (root / 'cache' / 'webui-workers.json', root / 'config' / 'webui-workers.json'):
        if not path.exists():
            continue
        registry = json.loads(path.read_text(encoding='utf-8'))
        for record in registry.get('workers', {}).values():
            if isinstance(record, dict) and record.get('pid') != os.getpid() and process_matches(record):
                raise MigrationError(f'旧业务 worker 仍在运行（PID {record["pid"]}），请停止后重新启动')
    root_key = os.path.normcase(str(root.resolve()))
    for process in psutil.process_iter(['pid', 'cmdline']):
        if process.pid == os.getpid():
            continue
        command = process.info.get('cmdline') or []
        if not any(Path(part).name.lower() in ('alas.py', 'gui.py', 'tui.py', 'mcp_server_sse.py') for part in command):
            continue
        try:
            if os.path.normcase(str(Path(process.cwd()).resolve())) == root_key:
                raise MigrationError(f'旧运行入口仍在使用该安装（PID {process.pid}），请停止后重试')
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


def snapshot_database(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=10)) as original, closing(sqlite3.connect(target)) as copy:
        original.backup(copy)



@contextmanager
def freeze_database(path):
    """取得源库写锁但不改数据，阻止转换期间出现新提交。"""
    with closing(sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=10)) as connection:
        connection.execute('BEGIN IMMEDIATE')
        try:
            yield
        finally:
            connection.rollback()

def fingerprint(path):
    digest = hashlib.sha256()
    for candidate in (path, path.with_name(path.name + '-wal')):
        digest.update(candidate.name.encode('utf-8'))
        if candidate.exists():
            with candidate.open('rb') as file:
                for chunk in iter(lambda: file.read(1024 * 1024), b''):
                    digest.update(chunk)
    return digest.hexdigest()


def backup_sources(database, sources, target):
    root = database.directory.parent
    copies, manifest = {}, []
    for path, kind in sorted(sources.items(), key=lambda pair: str(pair[0])):
        if path.is_symlink():
            raise MigrationError('迁移源不能是符号链接')
        relative = path.relative_to(root) if path.is_relative_to(root) else Path('external') / hashlib.sha256(str(path).encode()).hexdigest()[:16] / path.name
        copy = target / relative
        copy.parent.mkdir(parents=True, exist_ok=True)
        before = fingerprint(path)
        if kind in ('statistics', 'cl1', 'storage', 'daily', 'scheduler'):
            snapshot_database(path, copy)
        else:
            shutil.copy2(path, copy)
        if fingerprint(path) != before:
            raise MigrationError('迁移源在备份期间发生变化，请停止旧写入者后重试')
        copies[path] = copy
        manifest.append({'path': str(relative), 'kind': kind, 'digest': before})
    # 安全恢复材料只原样备份；不读取外部密钥，也不改变认证身份。
    for folder in ('opsi_secure', 'stock-exchange'):
        source = database.directory / folder
        if not source.exists():
            continue
        for path in source.rglob('*'):
            if path.is_symlink():
                raise MigrationError('安全恢复材料包含符号链接')
            if not path.is_file() or path.name.endswith(('.lock', '-wal', '-shm', '-journal')):
                continue
            copy = target / 'config' / folder / path.relative_to(source)
            copy.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix in ('.db', '.sqlite3'):
                snapshot_database(path, copy)
            else:
                shutil.copy2(path, copy)
    for path in database.directory.glob('*/config.db'):
        snapshot_database(path, target / 'config' / path.relative_to(database.directory))
    for path in database.directory.glob('*.json'):
        if not path.name.startswith('template'):
            copy = target / 'config' / path.name
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, copy)
    (target / 'sources.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return copies, manifest


class LegacyDecoder:
    """复用旧解密原语，不调用改写、隔离或退役旧密钥的流程。"""

    def __init__(self, root):
        from module.statistics import opsi_secure
        self.secure = opsi_secure
        self.root = root
        existing = opsi_secure._STORE
        self.store = existing if existing and existing.root == root.resolve() else opsi_secure.StatsStore(root)
        self._legacy_keys = None

    def payload(self, kind, raw, context):
        if self.secure.is_ciphertext(raw):
            result = self.store.vault_keys().decrypt_record(kind, raw, context)
        else:
            result = json.loads(raw)
        if not isinstance(result, dict):
            raise MigrationError(f'旧 {kind} 载荷无法解码，原件和恢复材料已保留')
        return result

    def cl1(self, row):
        data = None
        if row.get('data_json'):
            try:
                data = json.loads(row['data_json'])
            except (ValueError, TypeError):
                pass
        if not isinstance(data, dict) and row.get('encrypted_blob'):
            if self._legacy_keys is None:
                from module.base.device_id import get_device_id, get_old_device_id
                from module.statistics.cl1_legacy import derive_legacy_key
                self._legacy_keys = [derive_legacy_key(item) for item in {get_device_id(), get_old_device_id()} if item]
            from module.statistics.cl1_legacy import decrypt_legacy_payload
            for key in self._legacy_keys:
                try:
                    data = decrypt_legacy_payload(row['encrypted_blob'], key)
                    break
                except (ValueError, TypeError, UnicodeError):
                    continue
        if not isinstance(data, dict):
            raise MigrationError('旧月度快照无法解码')
        if row.get('secure_json'):
            data.update(self.payload('cl1', row['secure_json'], self.secure.row_context('cl1', row)))
        return data

    def file(self, kind, original, copy):
        data = json.loads(copy.read_text(encoding='utf-8'))
        if isinstance(data, dict) and (data.get(self.secure.WRAPPER_KEY) or data.get(self.secure.LEGACY_WRAPPER_KEY)):
            payload = data.get('payload')
            data = payload if isinstance(payload, dict) else self.payload(kind, payload, self.secure.file_context(self.root, kind, original))
        if not isinstance(data, dict):
            raise MigrationError('旧文件快照不是字典')
        return data



def verify_snapshot(expected, actual):
    """包括特殊浮点的逐字段对照；丢失或改变任一兼容值都拒绝切换。"""
    if type(expected) is dict:
        valid = type(actual) is dict and expected.keys() == actual.keys() and all(verify_snapshot(value, actual[key]) for key, value in expected.items())
    elif type(expected) is list:
        valid = type(actual) is list and len(expected) == len(actual) and all(verify_snapshot(a, b) for a, b in zip(expected, actual))
    elif type(expected) is float and math.isnan(expected):
        valid = type(actual) is float and math.isnan(actual)
    else:
        valid = expected == actual
    if not valid:
        raise MigrationError('转换后的快照与原业务结果不一致，未切换总库')
    return True

def import_database(connection, path, kind, original, decoder):
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as source:
        source.row_factory = sqlite3.Row
        tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if kind == 'scheduler':
            import_scheduler(connection, source, tables, original.stem)
            return
        expected_tables = {'statistics': {'resource_snapshots', 'resource_flows', 'resource_balances', 'opsi_items'},
                           'cl1': {'cl1_data'}, 'storage': {'storage_scans', 'storage_items'},
                           'daily': {name for name in COPY_TABLES if name.startswith('daily_summary_')}}
        unknown = tables - expected_tables[kind] - {'sqlite_sequence', 'sqlite_stat1', 'sqlite_stat4'}
        unknown = {name for name in unknown if not name.startswith('__opsi_')}
        if unknown or not tables.intersection(expected_tables[kind]):
            raise MigrationError(f'旧 {kind} 数据库的表结构不符合迁移约定')
        if kind == 'cl1' and 'cl1_data' in tables:
            for row in source.execute('SELECT * FROM cl1_data ORDER BY rowid'):
                row = dict(row)
                if read_month(connection, row['instance'], row['month']) is not None:
                    raise MigrationError('旧数据库存在重复月份，请先明确数据来源')
                decoded = decoder.cl1(row)
                save_month(connection, row['instance'], row['month'], decoded)
                verify_snapshot(decoded, read_month(connection, row['instance'], row['month']))
        for table in COPY_TABLES:
            if table not in tables:
                continue
            columns = {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}
            for row in source.execute(f'SELECT * FROM {table} ORDER BY rowid'):
                values = dict(row)
                for column, payload_kind in (('opsi_payload', 'res'), ('secure_payload', 'loot' if table == 'opsi_items' else 'daily')):
                    raw = values.pop(column, None)
                    if raw:
                        values.update(decoder.payload(payload_kind, raw, decoder.secure.row_context(payload_kind, values)))
                if table == 'daily_summary_periods' and decoder.secure.is_ciphertext(values.get('report_text')):
                    payload = decoder.payload('reports', values['report_text'], decoder.secure.report_context(values['instance'], values['period_key']))
                    if not isinstance(payload.get('text'), str):
                        raise MigrationError('旧日报正文无法解码')
                    values['report_text'] = payload['text']
                register_instance(connection, values.get('instance'))
                if set(values) - columns:
                    raise MigrationError(f'旧 {table} 含无法投影的业务列，未切换总库')
                projected = {key: value for key, value in values.items() if key in columns}
                target_id = insert(connection, table, projected)
                inserted = dict(connection.execute(f'SELECT * FROM {table} WHERE rowid=?', (target_id,)).fetchone())
                verify_snapshot(projected, {key: inserted[key] for key in projected})
        # 已清理的旧流水可能仍在消费水位之前；新 ID 必须越过旧序列和水位。
        if 'sqlite_sequence' in tables:
            autoincrement = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND sql LIKE '%AUTOINCREMENT%'")}
            for row in source.execute('SELECT name,seq FROM sqlite_sequence'):
                if row['name'] in COPY_TABLES and row['name'] in autoincrement:
                    if type(row['seq']) is not int or row['seq'] < 0:
                        raise MigrationError('旧数据库的自增序列无效')
                    if connection.execute('SELECT 1 FROM sqlite_sequence WHERE name=?', (row['name'],)).fetchone():
                        connection.execute('UPDATE sqlite_sequence SET seq=MAX(seq,?) WHERE name=?', (row['seq'], row['name']))
                    else:
                        connection.execute('INSERT INTO sqlite_sequence(name,seq) VALUES(?,?)', (row['name'], row['seq']))
        cursor = connection.execute('SELECT MAX(cursor) FROM resource_balances').fetchone()[0]
        if cursor is not None:
            if connection.execute("SELECT 1 FROM sqlite_sequence WHERE name='resource_flows'").fetchone():
                connection.execute("UPDATE sqlite_sequence SET seq=MAX(seq,?) WHERE name='resource_flows'", (cursor,))
            else:
                connection.execute("INSERT INTO sqlite_sequence(name,seq) VALUES('resource_flows',?)", (cursor,))


def import_scheduler(connection, source, tables, instance):
    if 'scheduler_programs' in tables:
        if source.execute('PRAGMA user_version').fetchone()[0] != VERSION or source.execute('PRAGMA foreign_key_check').fetchone():
            raise MigrationError('旧调度切片的版本或引用无效')
        from module.persistence.scheduler import copy_scheduler
        copy_scheduler(source, connection, instance)
        return
    if not tables.intersection({'programs', 'variables', 'records', 'runtime', 'observations', 'action_point_history', 'action_point_chain_owner'}):
        raise MigrationError('旧调度库没有已知业务或安全表')
    known = {'programs', 'variables', 'records', 'runtime', 'observations', 'action_point_history',
             'action_point_chain', 'action_point_chain_owner', 'sqlite_sequence', 'sqlite_stat1', 'sqlite_stat4'}
    if tables - known:
        raise MigrationError('旧调度库包含未约定的业务表')
    for table in ('programs', 'runtime'):
        if table in tables and source.execute(f'SELECT 1 FROM {table} WHERE id<>1').fetchone():
            raise MigrationError('旧调度库的单例记录发生结构冲突')
    if 'programs' in tables:
        row = source.execute('SELECT * FROM programs WHERE id=1').fetchone()
        if row:
            save_program(connection, instance, dict(mode=row['mode'], draft=json.loads(row['draft']),
                active=json.loads(row['active']) if row['active'] else None, generation=row['generation']), row['revision'])
    from module.persistence.scheduler import read_program, read_persistent, read_observations
    program = read_program(connection, instance)
    if program:
        from module.scheduler.models import ProgramDocument
        verify_snapshot(ProgramDocument.model_validate(json.loads(row['draft'])).model_dump(), program['draft'])
    persistent = {name: {row['name']: json.loads(row['value']) for row in source.execute(f'SELECT * FROM {name}')}
                  for name in ('variables', 'records') if name in tables}
    if 'runtime' in tables:
        row = source.execute('SELECT * FROM runtime WHERE id=1').fetchone()
        if row and row['in_flight']:
            persistent['inFlight'] = row['in_flight']
    if persistent:
        save_persistent(connection, instance, persistent)
        expected = dict(variables=persistent.get('variables', {}), records=persistent.get('records', {}))
        if persistent.get('inFlight'):
            expected['inFlight'] = persistent['inFlight']
        verify_snapshot(expected if any(expected.values()) else {}, read_persistent(connection, instance))
    if 'observations' in tables:
        for row in source.execute('SELECT * FROM observations'):
            write_observation(connection, instance, row['resource'], dict(Value=row['value'], Limit=row['resource_limit'], Total=row['total']), row['observed_at'], row['source'])


def import_archive(connection, original, copy, decoder):
    data = decoder.file('archives', original, copy)
    instance = original.parent.name
    for month in sorted(key for key in data if re.fullmatch(r'\d{4}-\d{2}', key)):
        if read_month(connection, instance, month) is not None:
            continue
        record = data[month]
        if isinstance(record, dict):
            save_month(connection, instance, month, record)
            verify_snapshot(record, read_month(connection, instance, month))
        else:
            from module.statistics.cl1_database import Cl1Database
            projected = Cl1Database()._empty_data(month)
            projected.update(battle_count=record, akashi_encounters=data.get(month + '-akashi', 0),
                akashi_ap=data.get(month + '-akashi-ap', 0), akashi_ap_entries=data.get(month + '-akashi-ap-entries', []))
            save_month(connection, instance, month, projected)
            verify_snapshot(projected, read_month(connection, instance, month))


def import_farming(connection, original, copy, decoder):
    raw = copy.read_bytes()
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        # 历史 CSV 的中文标题可能由平台默认编码写出，数值行统一为 ASCII。
        text = 'legacy header\n' + b'\n'.join(raw.splitlines()[1:]).decode('ascii')
    if decoder.secure.is_ciphertext(text):
        rows = decoder.payload('loot', text, decoder.secure.file_context(decoder.root, 'loot', original))['rows']
    else:
        rows = list(csv.reader(io.StringIO(text)))[1:]
    match = re.search(r'\.instance-([0-9a-f]{64})\.csv$', original.name)
    scope = 'instance-' + match[1] if match else 'global'
    instance = device = None
    if match:
        from module.base.device_id import get_device_id, get_old_device_id
        known = [path.stem for path in (decoder.root / 'config').glob('*.json') if not path.name.startswith('template')]
        for candidate_device in {get_device_id(), get_old_device_id()} - {None, ''}:
            for candidate in known:
                if hashlib.sha256(f'{candidate_device}\0{candidate}'.encode()).hexdigest() == match[1]:
                    instance, device = candidate, candidate_device
    register_instance(connection, instance)
    if len(rows) != 6:
        raise MigrationError('旧收益 CSV 缺少六个侵蚀等级')
    for record in rows:
        if len(record) != 7:
            raise MigrationError('旧收益 CSV 列数不符合约定')
        numbers = list(map(float, record))
        if numbers[0] != int(numbers[0]) or numbers[1] != int(numbers[1]):
            raise MigrationError('旧收益 CSV 等级或时间不是整数')
        insert(connection, 'farming_aggregates', dict(scope_key=scope, hazard_level=int(numbers[0]), instance=instance,
            device_id=device, source_kind='legacy', source_file=str(original.relative_to(decoder.root) if original.is_relative_to(decoder.root) else original), recorded_at=int(numbers[1]),
            effective_rounds=numbers[2], average_yellow_coin=numbers[3], average_plate=numbers[4], average_abyssal=numbers[5], average_obscure=numbers[6]))


def migrate(database):
    directory = database.directory
    directory.mkdir(parents=True, exist_ok=True)
    sources = source_files(database)
    assert_no_workers(directory.parent)
    temporary = directory / ('azurpilot.' + uuid4().hex + '.tmp')
    backup = directory / 'storage-backups' / ('pre-v1-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid4().hex[:8])
    decoder = LegacyDecoder(directory.parent)
    try:
        with ExitStack() as locks:
            for path in sorted(sources, key=str):
                locks.enter_context(config_transaction(path))
            for path in directory.glob('*/config.db'):
                locks.enter_context(config_transaction(path))
            for path in (directory / 'stock-exchange', directory / 'stock-exchange' / 'registry.json'):
                locks.enter_context(config_transaction(path))
            for path in sorted(directory.glob('*.json'), key=str):
                locks.enter_context(config_transaction(path))
            for path, kind in sorted(sources.items(), key=lambda pair: str(pair[0])):
                if kind in ('statistics', 'cl1', 'storage', 'daily', 'scheduler'):
                    locks.enter_context(freeze_database(path))
            backup.mkdir(parents=True)
            copies, manifest = backup_sources(database, sources, backup)
            digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
            with closing(sqlite3.connect(temporary)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute('PRAGMA foreign_keys=ON')
                connection.execute('PRAGMA journal_mode=WAL')
                connection.execute('PRAGMA synchronous=FULL')
                create_schema(connection)
                connection.execute('BEGIN IMMEDIATE')
                ordered = sorted(sources.items(), key=lambda pair: (pair[1] not in ('statistics', 'cl1', 'storage', 'daily', 'scheduler'), str(pair[0])))
                for original, kind in ordered:
                    copy = copies[original]
                    if kind in ('statistics', 'cl1', 'storage', 'daily', 'scheduler'):
                        import_database(connection, copy, kind, original, decoder)
                    elif kind == 'ships':
                        instance = original.parent.name
                        if read_ship(connection, instance) is None:
                            decoded = decoder.file('ships', original, copy)
                            save_ship(connection, instance, decoded)
                            verify_snapshot(decoded, read_ship(connection, instance))
                    elif kind == 'archives':
                        import_archive(connection, original, copy, decoder)
                    elif kind == 'farming':
                        import_farming(connection, original, copy, decoder)
                    elif kind.startswith('scheduler_'):
                        instance, section = original.stem, kind.removeprefix('scheduler_')
                        data = json.loads(copy.read_text(encoding='utf-8'))
                        if section == 'programs':
                            if not connection.execute('SELECT 1 FROM scheduler_programs WHERE instance=?', (instance,)).fetchone():
                                save_program(connection, instance, data, data.get('revision', uuid4().hex))
                        elif section == 'variables':
                            if not connection.execute('SELECT 1 FROM scheduler_runtime WHERE instance=?', (instance,)).fetchone():
                                save_persistent(connection, instance, data)
                        else:
                            for name, value in data.items():
                                if not connection.execute('SELECT 1 FROM scheduler_observations WHERE instance=? AND resource=?', (instance, name)).fetchone():
                                    write_observation(connection, instance, name, value, value['observedAt'], value['source'])
                insert(connection, 'storage_migrations', dict(version=VERSION, applied_at=datetime.now().isoformat(), source_digest=digest))
                if connection.execute('PRAGMA foreign_key_check').fetchone() or connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise MigrationError('临时总库未通过完整性检查')
                connection.commit()
                connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            for row, original in zip(manifest, sorted(sources, key=str)):
                if fingerprint(original) != row['digest']:
                    raise MigrationError('转换期间源数据发生变化，未切换总库')
            if source_files(database) != sources:
                raise MigrationError('转换期间旧来源发生变化，未切换总库')
            os.replace(temporary, database.path)
            database._write_marker(digest)
    except BaseException:
        for suffix in ('', '-wal', '-shm'):
            temporary.with_name(temporary.name + suffix).unlink(missing_ok=True)
        raise
