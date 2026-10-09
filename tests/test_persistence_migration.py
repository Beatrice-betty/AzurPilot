"""总库首次转换与恢复验证，所有旧源、密钥和安全材料均为临时夹具。"""
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from module.persistence.database import BusinessDatabase, register_instance
from module.persistence.migration import MigrationError, assert_no_workers
from module.persistence.snapshots import read_month, read_ship, save_month
from module.scheduler.store import ProgramStore, ConflictError
from module.statistics import opsi_secure
from tests.opsi_test_support import install_store
from tests.test_opsi_secure import copy_fixture, legacy_blob, legacy_ring, make_cl1_db


class MigrationProcessTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.interpreter = self.root / '.venv' / 'Scripts' / 'python.exe'
        self.base_interpreter = self.root / 'python' / 'python.exe'

    def process(self, pid, executable, arguments, *, cwd=None):
        command = [str(executable), *arguments]
        process = Mock()
        process.pid = pid
        process.info = {'pid': pid, 'cmdline': command}
        process.cmdline.return_value = command
        process.exe.return_value = str(executable)
        process.cwd.return_value = str(cwd or self.root)
        process.parents.return_value = []
        return process

    def scan(self, current, processes):
        with patch('psutil.Process', return_value=current), \
                patch('psutil.process_iter', return_value=processes), \
                patch('module.persistence.migration.sys.platform', 'win32'), \
                patch('module.persistence.migration.sys.executable', str(self.interpreter)):
            assert_no_workers(self.root)

    def test_uv_and_windows_redirector_do_not_block_current_entry(self):
        for entry in ('gui.py', 'alas.py', 'tui.py', 'mcp_server_sse.py'):
            for explicit_python in (False, True):
                with self.subTest(entry=entry, explicit_python=explicit_python):
                    current = self.process(os.getpid(), self.base_interpreter, [entry])
                    redirector = self.process(1000001, self.interpreter, [entry])
                    uv = self.process(1000002, self.root / 'uv.exe',
                                      ['run', *(['python'] if explicit_python else []), entry])
                    shell = self.process(1000003, self.root / 'powershell.exe', [])
                    current.parents.return_value = [redirector, uv, shell]
                    self.scan(current, [uv, redirector, current, shell])

    def test_another_entry_still_blocks_with_the_same_command(self):
        current = self.process(os.getpid(), self.base_interpreter, ['gui.py'])
        redirector = self.process(1000001, self.interpreter, ['gui.py'])
        old = self.process(1000002, self.base_interpreter, ['gui.py'])
        current.parents.return_value = [redirector]
        with self.assertRaisesRegex(MigrationError, 'PID 1000002'):
            self.scan(current, [redirector, old])

    def test_python_ancestor_running_an_entry_is_not_exempt(self):
        current = self.process(os.getpid(), self.base_interpreter, ['gui.py'])
        parent = self.process(1000001, self.base_interpreter, ['gui.py'])
        current.parents.return_value = [parent]
        with self.assertRaisesRegex(MigrationError, 'PID 1000001'):
            self.scan(current, [parent])

    def test_matching_interpreter_with_different_arguments_is_not_a_redirector(self):
        current = self.process(os.getpid(), self.base_interpreter, ['gui.py'])
        parent = self.process(1000001, self.interpreter, ['alas.py'])
        current.parents.return_value = [parent]
        with self.assertRaisesRegex(MigrationError, 'PID 1000001'):
            self.scan(current, [parent])

    def test_registered_worker_is_checked_before_launcher_exemptions(self):
        current = self.process(os.getpid(), self.base_interpreter, ['gui.py'])
        redirector = self.process(1000001, self.interpreter, ['gui.py'])
        current.parents.return_value = [redirector]
        registry = self.root / 'cache' / 'webui-workers.json'
        registry.parent.mkdir()
        registry.write_text(json.dumps({'workers': {'inst': {'pid': redirector.pid}}}), encoding='utf-8')
        with patch('module.runtime.process_control.process_matches', return_value=True):
            with self.assertRaisesRegex(MigrationError, '旧业务 worker.*PID 1000001'):
                self.scan(current, [redirector])

    def test_unverified_parent_is_not_exempt(self):
        import psutil
        current = self.process(os.getpid(), self.base_interpreter, ['gui.py'])
        parent = self.process(1000001, self.interpreter, ['gui.py'])
        parent.exe.side_effect = psutil.AccessDenied(parent.pid)
        current.parents.return_value = [parent]
        with self.assertRaisesRegex(MigrationError, 'PID 1000001'):
            self.scan(current, [parent])

    def test_entry_from_another_installation_does_not_block(self):
        current = self.process(os.getpid(), self.base_interpreter, ['gui.py'])
        other = self.process(1000001, self.base_interpreter, ['gui.py'], cwd=self.root / 'other')
        self.scan(current, [other])

    def test_uv_startup_migrates_temp_data_with_both_command_forms(self):
        uv = shutil.which('uv')
        if uv is None:
            self.skipTest('未安装 uv，无法验证实际启动链')
        project = Path(__file__).resolve().parents[1]
        script = (
            "import sys\nfrom pathlib import Path\n"
            f"sys.path.insert(0, {str(project)!r})\n"
            "from module.persistence.database import initialize\n"
            "database = initialize(Path(__file__).parent / 'config')\n"
            "print('migration-ready')\n"
        )
        for arguments in (['gui.py'], ['python', 'gui.py']):
            with self.subTest(arguments=arguments):
                root = self.root / ('direct' if len(arguments) == 1 else 'python')
                config = root / 'config'
                config.mkdir(parents=True)
                (root / 'gui.py').write_text(script, encoding='utf-8')
                source = config / 'cl1_data.db'
                with closing(sqlite3.connect(source)) as connection, connection:
                    connection.execute('CREATE TABLE cl1_data(instance TEXT,month TEXT,data_json TEXT)')
                    connection.execute('INSERT INTO cl1_data VALUES(?,?,?)',
                                       ('inst', '2026-09', '{"battle_count":7}'))
                original = source.read_bytes()
                result = subprocess.run([uv, 'run', '--no-sync', '--project', str(project), *arguments],
                                        cwd=root, capture_output=True, encoding='utf-8', errors='replace', timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('migration-ready', result.stdout)
                self.assertTrue((config / 'azurpilot.migrated').is_file())
                self.assertEqual(source.read_bytes(), original)
                with closing(sqlite3.connect(config / 'azurpilot.db')) as connection:
                    self.assertEqual(connection.execute('SELECT battle_count FROM cl1_months').fetchone()[0], 7)
                    self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
                    self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.config = self.root / 'config'
        self.config.mkdir()
        self.store = install_store(self, self.root)
        self.database = BusinessDatabase(self.config)

    def old_cl1(self, data):
        path = self.config / 'cl1_data.db'
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute('CREATE TABLE cl1_data(instance TEXT,month TEXT,data_json TEXT,secure_json TEXT,encrypted_blob BLOB)')
            connection.execute('INSERT INTO cl1_data VALUES(?,?,?,NULL,NULL)', ('inst', '2026-09', json.dumps(data)))
        return path

    def test_plain_sources_preserve_fields_ids_watermarks_and_database_priority(self):
        expected = {'battle_count': 7, 'commission_income_entries': [{'keep': True}],
                    'last_ap_notification': None, 'unknown': [None, {}, [], 2 ** 80]}
        source = self.old_cl1(expected)
        original = source.read_bytes()
        archive = self.root / 'log' / 'cl1' / 'inst' / 'cl1_monthly.json'
        archive.parent.mkdir(parents=True)
        archive.write_text(json.dumps({'2026-09': 999, '2026-08': {'battle_count': 2}}), encoding='utf-8')
        ships = archive.with_name('ship_exp_data.json')
        ships.write_text('{"target_level": null, "ships": [{"level": 0}], "battle_times": {"average": 17, "samples": [52]}}', encoding='utf-8')
        statistics = self.config / 'azurstats_local.db'
        with closing(sqlite3.connect(statistics)) as connection, connection:
            connection.executescript('''CREATE TABLE resource_flows(id INTEGER PRIMARY KEY, instance TEXT, ts TEXT,
                resource TEXT,amount INTEGER,task TEXT,operation TEXT,evidence TEXT,run_id TEXT,event_key TEXT);
                CREATE TABLE resource_balances(instance TEXT,resource TEXT,value INTEGER,ts TEXT,run_id TEXT,cursor INTEGER);
                CREATE TABLE opsi_items(id INTEGER PRIMARY KEY,imgid TEXT,item TEXT,amount INTEGER);''')
            connection.execute("INSERT INTO resource_flows VALUES(41,'inst','old','Oil',-10,'Task','Buy','confirmed',NULL,'event')")
            connection.execute("INSERT INTO resource_balances VALUES('inst','Oil',100,'old',NULL,41)")
            connection.execute("INSERT INTO opsi_items VALUES(19,'shared-image','PlateT4',2)")
        self.database.ensure_ready()
        with self.database.transaction(write=False) as connection:
            self.assertEqual(read_month(connection, 'inst', '2026-09'), expected)
            self.assertEqual(read_month(connection, 'inst', '2026-08'), {'battle_count': 2})
            self.assertEqual(read_ship(connection, 'inst')['battle_times'], {'average': 17, 'samples': [52]})
            self.assertEqual(connection.execute('SELECT id FROM resource_flows').fetchone()[0], 41)
            self.assertEqual(connection.execute('SELECT cursor FROM resource_balances').fetchone()[0], 41)
            row = connection.execute('SELECT id,instance FROM opsi_items').fetchone()
            self.assertEqual(tuple(row), (19, None))
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(source.read_bytes(), original)
        self.assertTrue(self.database.marker.exists())
        self.assertEqual(len(list((self.config / 'storage-backups').glob('*/sources.json'))), 1)

    def test_failed_decode_keeps_sources_and_does_not_switch_then_retries(self):
        path = self.old_cl1({'keep': True})
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("UPDATE cl1_data SET data_json='broken'")
        original = path.read_bytes()
        with self.assertRaises(MigrationError):
            self.database.ensure_ready()
        self.assertFalse(self.database.path.exists())
        self.assertFalse(self.database.marker.exists())
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(self.config.glob('azurpilot.*.tmp*')), [])
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute('UPDATE cl1_data SET data_json=?', ('{"keep": true}',))
        self.database.ensure_ready()
        with self.database.transaction(write=False) as connection:
            self.assertEqual(read_month(connection, 'inst', '2026-09'), {'keep': True})

    def test_conflicting_months_fail_without_repairing_original(self):
        path = self.old_cl1({'battle_count': 1})
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("INSERT INTO cl1_data VALUES('inst','2026-09','{}',NULL,NULL)")
        original = path.read_bytes()
        with self.assertRaises(MigrationError):
            self.database.ensure_ready()
        self.assertFalse(self.database.path.exists())
        self.assertEqual(path.read_bytes(), original)

    def test_running_old_worker_blocks_install_before_cutover(self):
        cache = self.root / 'cache'
        cache.mkdir()
        (cache / 'webui-workers.json').write_text(json.dumps({'workers': {'inst': {'pid': 12345}}}), encoding='utf-8')
        with patch('module.runtime.process_control.process_matches', return_value=True):
            with self.assertRaises(MigrationError):
                self.database.ensure_ready()
        self.assertFalse(self.database.path.exists())
        self.assertFalse(self.database.marker.exists())

    def test_marker_crash_repairs_from_migration_record_and_missing_database_never_reimports(self):
        self.old_cl1({'battle_count': 1})
        with patch.object(self.database, '_write_marker', side_effect=OSError('模拟切换后的崩溃')):
            with self.assertRaises(OSError):
                self.database.ensure_ready()
        self.assertTrue(self.database.path.exists())
        self.assertFalse(self.database.marker.exists())
        recovered = BusinessDatabase(self.config)
        recovered.ensure_ready()
        self.assertTrue(recovered.marker.exists())
        recovered.path.unlink()
        with self.assertRaises(FileNotFoundError):
            recovered.ensure_ready()

    def test_v1_encrypted_payload_does_not_retire_keys_or_rewrite_original(self):
        key = b'x' * 32
        legacy_ring(self.root, key)
        expected = {'battle_count': 8, 'ap_snapshots': [], 'siren_research_devices': {'cl1': 2, 'meow': {}}}
        path = self.old_cl1({'keep': True})
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute('UPDATE cl1_data SET secure_json=?', (legacy_blob(key, 'cl1', expected),))
        original = path.read_bytes()
        ring = self.config / 'opsi_secure' / 'keyring.json'
        material = ring.read_bytes()
        with patch.object(opsi_secure, '_dpapi', side_effect=lambda value, decrypt=False: value), \
                patch.object(opsi_secure, 'decrypt_all', side_effect=AssertionError('迁移不能改写旧数据')):
            self.database.ensure_ready()
        with self.database.transaction(write=False) as connection:
            self.assertEqual(read_month(connection, 'inst', '2026-09'), dict(expected, keep=True))
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(ring.read_bytes(), material)

    def test_v2_deployment_fixture_is_read_only_and_all_sources_migrate(self):
        root = copy_fixture(self)
        from module.persistence.migration import source_files, fingerprint
        install_store(self, root)
        database = BusinessDatabase(root / 'config')
        original = {path: fingerprint(path) for path in source_files(database)}
        material = (root / 'config' / 'opsi_secure' / 'state.json').read_bytes()
        with patch.object(opsi_secure, '_dpapi', side_effect=lambda value, decrypt=False: value):
            database.ensure_ready()
        with database.transaction(write=False) as connection:
            self.assertEqual(read_month(connection, 'alpha', '2026-09')['battle_count'], 128)
            self.assertGreater(connection.execute('SELECT COUNT(*) FROM opsi_items').fetchone()[0], 0)
            self.assertGreater(connection.execute('SELECT COUNT(*) FROM daily_summary_periods').fetchone()[0], 0)
            self.assertIsNotNone(read_ship(connection, 'alpha'))
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM farming_aggregates').fetchone()[0], 6)
        self.assertEqual({path: fingerprint(path) for path in original}, original)
        self.assertEqual((root / 'config' / 'opsi_secure' / 'state.json').read_bytes(), material)

    def test_wal_backup_contains_committed_rows(self):
        self.database.ensure_ready()
        with closing(self.database.connect(factory=sqlite3.Connection)) as writer:
            writer.execute('PRAGMA wal_autocheckpoint=0')
            writer.execute('BEGIN IMMEDIATE')
            save_month(writer, 'inst', '2026-09', {'battle_count': 9})
            writer.commit()
            self.assertTrue(self.database.path.with_name('azurpilot.db-wal').stat().st_size > 0)
            backup = self.root / 'backup.db'
            self.database.backup(backup)
            with closing(sqlite3.connect(backup)) as saved:
                self.assertEqual(saved.execute('SELECT battle_count FROM cl1_months').fetchone()[0], 9)

    def test_new_flow_ids_follow_a_watermark_even_when_old_rows_were_cleaned(self):
        path = self.config / 'azurstats_local.db'
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.executescript('''CREATE TABLE resource_flows(id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance TEXT,ts TEXT,resource TEXT,amount INTEGER,task TEXT,operation TEXT,evidence TEXT,run_id TEXT,event_key TEXT);
                CREATE TABLE resource_balances(instance TEXT,resource TEXT,value INTEGER,ts TEXT,run_id TEXT,cursor INTEGER);
                INSERT INTO resource_flows VALUES(100,'inst','old','Oil',-10,'Task','Buy','confirmed',NULL,'old');
                DELETE FROM resource_flows;
                INSERT INTO resource_balances VALUES('inst','Oil',100,'old',NULL,90);''')
        self.database.ensure_ready()
        with self.database.transaction() as connection:
            cursor = connection.execute("INSERT INTO resource_flows(instance,ts,resource,amount,task,operation,evidence,event_key) VALUES('inst','new','Oil',-2,'Task','Buy','confirmed','new')")
            self.assertEqual(cursor.lastrowid, 101)

    def test_old_scheduler_sqlite_and_json_keep_drafts_runtime_and_priority(self):
        from module.scheduler.templates import default_program
        document = default_program().model_dump()
        directory = self.config / 'scheduler'
        directory.mkdir()
        path = directory / 'inst.sqlite3'
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.executescript('''CREATE TABLE programs(id INTEGER PRIMARY KEY,mode TEXT,draft TEXT,active TEXT,
                generation INTEGER,revision TEXT); CREATE TABLE variables(name TEXT,value TEXT);
                CREATE TABLE records(name TEXT,value TEXT); CREATE TABLE runtime(id INTEGER PRIMARY KEY,in_flight TEXT);
                CREATE TABLE observations(resource TEXT,value REAL,resource_limit REAL,total REAL,observed_at TEXT,source TEXT);''')
            connection.execute('INSERT INTO programs VALUES(1,?,?,NULL,3,?)', ('native', json.dumps(document), 'revision'))
            connection.execute('INSERT INTO variables VALUES(?,?)', ('huge', str(2 ** 100)))
            connection.execute("INSERT INTO records VALUES('rotation','{\"node\": 2}')")
            connection.execute("INSERT INTO runtime VALUES(1,'Commission')")
        (directory / 'programs').mkdir()
        (directory / 'programs' / 'inst.json').write_text(json.dumps(ProgramStore.default()), encoding='utf-8')
        program = ProgramStore(store=self.database)
        self.assertEqual(program.get('inst')['revision'], 'revision')
        self.assertEqual(program.persistent('inst'), {'variables': {'huge': 2 ** 100}, 'records': {'rotation': {'node': 2}}, 'inFlight': 'Commission'})
        self.assertTrue(path.exists())

    def test_single_instance_slice_restore_and_rename_preserve_other_statistics(self):
        store = ProgramStore(store=self.database)
        for name in ('first', 'other'):
            current = store.get(name)
            store.update(name, current['revision'])
            store.save_persistent(name, {'variables': {'name': name}})
            store.observe(name, 'Oil', 20, 'old-time', 'fixture')
        with self.database.transaction() as connection:
            save_month(connection, 'first', '2026-09', {'battle_count': 5})
        backup = self.root / 'archive'
        other = store.get('other')
        store.archive('first', backup)
        self.assertFalse(store.exists('first'))
        self.assertEqual(store.get('other'), other)
        slice_path = backup / 'business' / 'first.sqlite3'
        with closing(sqlite3.connect(slice_path)) as connection:
            self.assertEqual(connection.execute('SELECT instance FROM scheduler_programs').fetchall(), [('first',)])
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM cl1_months').fetchone()[0], 0)
        store.restore('first', slice_path)
        store.relocate('first', 'renamed')
        self.assertEqual(store.persistent('renamed')['variables'], {'name': 'first'})
        self.assertFalse(store.exists('first'))
        self.assertEqual(store.get('other'), other)
        with self.database.transaction(write=False) as connection:
            self.assertEqual(read_month(connection, 'first', '2026-09'), {'battle_count': 5})
            self.assertIsNone(read_month(connection, 'renamed', '2026-09'))

    def test_slice_rejects_live_target_and_restore_does_not_overwrite_runtime(self):
        store = ProgramStore(store=self.database)
        store.save_persistent('inst', {'variables': {'keep': 1}})
        with self.assertRaises(ValueError):
            store.backup('inst', self.database.path)
        path = self.root / 'inst.sqlite3'
        store.backup('inst', path)
        with self.assertRaises(ConflictError):
            store.restore('inst', path)
        self.assertEqual(store.persistent('inst')['variables'], {'keep': 1})

    def test_explicit_statistics_export_roundtrip_and_late_conflict_rolls_back(self):
        from dev_tools.business_storage import check, export_statistics, import_statistics
        from module.persistence.snapshots import save_ship
        with self.database.transaction() as connection:
            save_month(connection, 'inst', '2026-09', {'keep': [None, {}, [], 2 ** 100]})
            save_ship(connection, 'inst', {'target_level': None, 'ships': [{'level': 0, 'extra': True}]})
        path = self.root / 'statistics.json'
        export_statistics(self.database, 'inst', path)
        other = BusinessDatabase(self.root / 'restored')
        import_statistics(other, 'inst', path)
        self.assertEqual(len(check(other)), 56)
        with other.transaction(write=False) as connection:
            self.assertEqual(read_month(connection, 'inst', '2026-09'), {'keep': [None, {}, [], 2 ** 100]})
            self.assertEqual(read_ship(connection, 'inst')['target_level'], None)
        data = json.loads(path.read_text(encoding='utf-8'))
        data['months'] = {'2026-08': {'battle_count': 2}, **data['months']}
        path.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaises(FileExistsError):
            import_statistics(other, 'inst', path)
        with other.transaction(write=False) as connection:
            self.assertIsNone(read_month(connection, 'inst', '2026-08'))
