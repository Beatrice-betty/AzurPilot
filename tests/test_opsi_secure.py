"""大世界统计数据文件加密（module/statistics/opsi_secure.py）的行为测试。

覆盖：首次就绪自动建立密钥、加解密往返、跨进程用同一密钥解封、
DPAPI 不可用时保持旧行为、密文被改动读不出、受保护代码或 keyring 被改动触发清空、
旧数据迁移（cl1 库、掉落明细库、资源快照、舰船经验 JSON、farming CSV）。
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from module.statistics import opsi_secure

NOW = 1_800_000_000.0


def make_cl1_db(path):
    """构造旧版明文 cl1 库（含大世界战斗数据与委托、科研等非大世界字段）。"""
    conn = sqlite3.connect(path)
    conn.execute(
        'CREATE TABLE cl1_data (instance TEXT, month TEXT, data_json TEXT, '
        'encrypted_blob BLOB, PRIMARY KEY (instance, month))'
    )
    data = {
        'battle_count': 120,
        'akashi_encounters': 3,
        'akashi_ap': 60,
        'akashi_ap_entries': [{'ts': '2026-09-01T10:00:00', 'amount': 20, 'base': 10, 'count': 2, 'source': 'cl1'}],
        'ap_snapshots': [{'ts': '2026-09-01T10:00:00', 'ap': 131, 'asset': 7500.5, 'source': 'cl1'}],
        'last_ap_notification': {'ts': '2026-09-01T10:00:00', 'ap': 131},
        'yellow_coin_snapshots': [{'ts': '2026-09-01T10:00:00', 'yellow_coin': 500, 'source': 'cl1'}],
        'coins_snapshots': [{'ts': '2026-09-01T10:00:00', 'yellow_coins': 500, 'purple_coins': 20, 'source': 'cl1'}],
        'coins_history_version': 2,
        'coins_cleanup_version': 1,
        'meow_battle_raw_count': 10,
        'meow_battle_count': 5.0,
        'meow_round_times': [{'duration': 60.5, 'hazard_level': 3}],
        'meow_battle_times': [20.5],
        'meow_hazard_stats': {'3': {'battle_raw_count': 4, 'effective_rounds': 2.0, 'round_times': [60.0], 'battle_times': [20.0]}},
        'siren_research_devices': {'cl1': 2, 'meow': {'3': 1}},
        'siren_research_device_entries': [{'ts': '2026-09-01T10:00:00', 'source': 'cl1', 'hazard_level': None}],
        'commission_income_entries': [{'ts': '2026-09-01T09:00:00', 'items': {'Gem': 5}, 'commission_count': 1, 'screenshots': []}],
        'research_drop_entries': [{'ts': '2026-09-01T09:00:00', 'project': 'D-737-MI', 'series': 9, 'items': {'Blueprint': 1}, 'imgid': 'x'}],
        'gem_commission_entries': [],
        'running_gem_commissions': [],
    }
    conn.execute(
        'INSERT INTO cl1_data VALUES (?, ?, ?, NULL)',
        ('inst', '2026-09', json.dumps(data, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()
    return data


def make_loot_db(path):
    """构造旧版明文掉落明细与资源快照库（同一个 azurstats_local.db）。"""
    conn = sqlite3.connect(path)
    conn.execute('''
        CREATE TABLE opsi_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            imgid TEXT NOT NULL, server TEXT, zone TEXT, zone_type TEXT,
            zone_id INTEGER, hazard_level INTEGER, item TEXT, amount INTEGER,
            tag TEXT, device_id TEXT, instance TEXT, genre TEXT,
            combat_count INTEGER, created_at INTEGER
        )
    ''')
    conn.execute(
        'INSERT INTO opsi_items (imgid, server, zone, zone_type, zone_id, hazard_level, '
        'item, amount, tag, device_id, instance, genre, combat_count, created_at) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        ('abc123', 'cn', 'NA海域', 'abyssal', 5, 6, 'PlateGeneralT4', 3, 'gold',
         'device-1', 'inst', 'opsi_abyssal', 2, 1789000000),
    )
    conn.execute('''
        CREATE TABLE resource_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT, instance TEXT NOT NULL, ts TEXT NOT NULL,
            oil INTEGER, coin INTEGER, gem INTEGER, pt INTEGER, cube INTEGER, core INTEGER,
            medal INTEGER, merit INTEGER, guild_coin INTEGER,
            action_point INTEGER, yellow_coin INTEGER, purple_coin INTEGER
        )
    ''')
    conn.execute(
        'INSERT INTO resource_snapshots (instance, ts, oil, coin, gem, pt, cube, core, medal, '
        'merit, guild_coin, action_point, yellow_coin, purple_coin) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        ('inst', '2026-09-01T10:00:00', 14000, 180000, 2400, 40000, 380, 1200, 600, 18000, 7500, 131, 500, 20),
    )
    conn.commit()
    conn.close()


class OpsiSecureTestCase(unittest.TestCase):
    def setUp(self):
        # Windows 上杀软/索引器会短暂占用刚写入的文件，清理失败不应让用例报错。
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.directory.name)
        (self.root / 'config').mkdir()
        (self.root / 'log' / 'cl1' / 'inst').mkdir(parents=True)
        # 受保护清单指向测试自建文件，便于模拟「修改代码」。
        self.protected = self.root / 'protected_module.py'
        self.protected.write_text('VERSION = 1\n', encoding='utf-8')
        make_cl1_db(self.root / 'config' / 'cl1_data.db')
        make_loot_db(self.root / 'config' / 'azurstats_local.db')
        (self.root / 'log' / 'cl1' / 'inst' / 'ship_exp_data.json').write_text(
            json.dumps({'battle_times': {'samples': [52.0], 'average': 52.0}}, ensure_ascii=False), encoding='utf-8')
        (self.root / 'log' / 'azurstat_meowofficer_farming.csv').write_text(
            '侵蚀等级,上次记录时间,有效战斗轮数,平均黄币/轮,平均金菜/轮,平均深渊/轮,平均隐秘/轮\n'
            '1.0,100.0,2.0,10.0,1.0,0.5,0.25\n', encoding='utf-8')
        self.dpapi_patch = None

    def tearDown(self):
        if self.dpapi_patch is not None:
            self.dpapi_patch.stop()
        self.directory.cleanup()

    def make_vault(self):
        # 后台迁移在测试里关闭，迁移一律显式调用，保证断言确定性。
        return opsi_secure.Vault(root=self.root, protected_files=[self.protected], background_migration=False)

    def ready_vault(self):
        vault = self.make_vault()
        self.assertTrue(vault.ensure_ready())
        return vault

    def block_dpapi(self):
        """模拟换机器 / DPAPI 不可用。"""
        self.dpapi_patch = patch('module.runtime.account_local.dpapi',
                                 side_effect=OSError('dpapi unavailable'))
        self.dpapi_patch.start()

    # ---- 密钥生命周期 ----

    def test_fresh_vault_enables_on_demand(self):
        vault = self.make_vault()
        self.assertFalse(vault.is_configured())
        # 未启用前 seal 不可用（存储层先查 is_configured 才走密文路径）。
        with self.assertRaises(opsi_secure.VaultError):
            vault.seal('cl1', {'battle_count': 1})
        # writer_ready 会就地建立密钥（生产由启动钩子提前完成）。
        self.assertTrue(vault.writer_ready())
        self.assertTrue(vault.is_configured())
        self.assertTrue(vault.keyring_present())
        self.assertTrue(vault.seal('cl1', {'battle_count': 1}).startswith(opsi_secure.BLOB_PREFIX))

    def test_ensure_ready_creates_key_and_seals(self):
        vault = self.ready_vault()
        self.assertTrue(vault.is_configured())
        self.assertTrue(vault.keyring_present())
        for kind in ('cl1', 'loot', 'ships', 'res'):
            obj = {'值': [1, 2.5, None], 'nested': {'a': '中文'}, 'n': 42}
            blob = vault.seal(kind, obj)
            self.assertTrue(blob.startswith(opsi_secure.BLOB_PREFIX))
            self.assertEqual(vault.open_(kind, blob), obj)

    def test_tampered_blob_is_rejected(self):
        vault = self.ready_vault()
        blob = vault.seal('cl1', {'battle_count': 5})
        tampered = blob[:-6] + ('A' if blob[-6] != 'A' else 'B') + blob[-5:]
        with self.assertRaises(opsi_secure.VaultError):
            vault.open_('cl1', tampered)

    def test_cross_kind_blob_is_rejected(self):
        vault = self.ready_vault()
        blob = vault.seal('loot', {'item': 'x'})
        with self.assertRaises(opsi_secure.VaultError):
            vault.open_('cl1', blob)

    def test_new_process_unwraps_same_key(self):
        vault = self.ready_vault()
        blob = vault.seal('cl1', {'battle_count': 9})
        fresh = self.make_vault()
        self.assertTrue(fresh.is_configured())
        self.assertTrue(fresh.writer_ready())
        self.assertEqual(fresh.open_('cl1', blob), {'battle_count': 9})

    def test_dpapi_unavailable_keeps_plaintext_behaviour(self):
        self.block_dpapi()
        vault = self.make_vault()
        self.assertFalse(vault.writer_ready())
        self.assertFalse(vault.is_configured())
        self.assertFalse(vault.keyring_present())
        self.assertFalse(vault.status()['configured'])

    def test_unavailable_environment_degrades_reads(self):
        # 已建立密钥后换到「无法解封」的环境：写入被记数、读取降级，数据不被覆盖。
        vault = self.ready_vault()
        blob = vault.seal('cl1', {'battle_count': 9})
        self.block_dpapi()
        fresh = self.make_vault()
        self.assertTrue(fresh.is_configured())
        self.assertFalse(fresh.writer_ready())
        self.assertIsNone(fresh.open_or_none('cl1', blob))
        with self.assertRaises(opsi_secure.VaultLocked):
            fresh.seal('cl1', {'battle_count': 1})

    # ---- 防篡改清空 ----

    def test_protected_code_change_wipes_data(self):
        vault = self.ready_vault()
        vault.ensure_migrated()
        self.protected.write_text('VERSION = 2\n', encoding='utf-8')
        fresh = self.make_vault()
        self.assertFalse(fresh.is_configured())
        status = fresh.status()
        self.assertIsNotNone(status['lastWipe'])
        self.assertNotEqual(status['lastWipe']['reason'], '')
        # 受保护数据全部清空。
        conn = sqlite3.connect(self.root / 'config' / 'cl1_data.db')
        rows = conn.execute('SELECT data_json, secure_json FROM cl1_data').fetchall()
        conn.close()
        self.assertTrue(all(row[1] is None for row in rows))
        self.assertIn('commission_income_entries', json.loads(rows[0][0]))
        conn = sqlite3.connect(self.root / 'config' / 'azurstats_local.db')
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM opsi_items').fetchone()[0], 0)
        row = conn.execute('SELECT action_point, yellow_coin, purple_coin FROM resource_snapshots').fetchone()
        self.assertEqual(list(row), [None, None, None])
        conn.close()
        # 与加密无关的数据保留。
        self.assertTrue((self.root / 'config' / 'cl1_data.db').exists())

    def test_reseal_updates_manifest_without_wipe(self):
        # 有意修改受保护文件后用重封工具更新清单：完整性校验恢复，不触发清空。
        from dev_tools.opsi_secure_reseal import reseal
        vault = self.ready_vault()
        vault.seal('cl1', {'battle_count': 1})
        self.protected.write_text('VERSION = 2\n', encoding='utf-8')
        code = reseal(self.root, confirm=True, protected_files=[self.protected])
        self.assertEqual(code, 0)
        fresh = self.make_vault()
        self.assertTrue(fresh.writer_ready())
        self.assertIsNone(self.make_vault().status()['lastWipe'])

    def test_reseal_rejects_preview_without_confirm(self):
        from dev_tools.opsi_secure_reseal import reseal
        self.ready_vault()
        self.protected.write_text('VERSION = 3\n', encoding='utf-8')
        self.assertEqual(reseal(self.root, confirm=False, protected_files=[self.protected]), 0)
        # 未确认时不写入：明文校验仍会发现差异。
        fresh = self.make_vault()
        self.assertFalse(fresh.is_configured())
        self.assertIsNotNone(fresh.status()['lastWipe'])

    def test_keyring_edit_wipes_data(self):
        # 攻击者改 keyring 内容但保留其余结构：解封时 MAC 校验失败，数据清空。
        self.ready_vault()
        keyring_path = self.root / 'config' / 'opsi_secure' / 'keyring.json'
        keyring = json.loads(keyring_path.read_text(encoding='utf-8'))
        keyring['created'] = '被改过'
        keyring_path.write_text(json.dumps(keyring, ensure_ascii=False), encoding='utf-8')
        fresh = self.make_vault()
        self.assertFalse(fresh.writer_ready())
        self.assertFalse(self.make_vault().is_configured())
        self.assertIsNotNone(self.make_vault().status()['lastWipe'])

    # ---- 迁移 ----

    def test_migration_cl1_splits_secure_fields(self):
        vault = self.ready_vault()
        summary = vault.ensure_migrated()
        self.assertGreaterEqual(summary['cl1'], 1)
        conn = sqlite3.connect(self.root / 'config' / 'cl1_data.db')
        raw_json, raw_secure = conn.execute('SELECT data_json, secure_json FROM cl1_data').fetchone()
        conn.close()
        public = json.loads(raw_json)
        self.assertNotIn('battle_count', public)
        self.assertNotIn('ap_snapshots', public)
        self.assertNotIn('meow_hazard_stats', public)
        self.assertIn('commission_income_entries', public)
        self.assertIn('research_drop_entries', public)
        self.assertNotIn('battle_count', raw_json)
        secure = vault.open_('cl1', raw_secure)
        self.assertEqual(secure['battle_count'], 120)
        self.assertEqual(secure['meow_hazard_stats']['3']['battle_raw_count'], 4)
        self.assertNotIn('commission_income_entries', secure)
        # 幂等：再次迁移不重复处理。
        self.assertEqual(vault.ensure_migrated()['cl1'], 0)

    def test_migration_loot_rows(self):
        vault = self.ready_vault()
        summary = vault.ensure_migrated()
        self.assertGreaterEqual(summary['loot'], 1)
        conn = sqlite3.connect(self.root / 'config' / 'azurstats_local.db')
        row = conn.execute('SELECT secure_payload, item, amount, zone_id, hazard_level, created_at FROM opsi_items').fetchone()
        conn.close()
        payload, item, amount, zone_id, hazard, created = row
        self.assertIsNone(item)
        self.assertIsNone(amount)
        self.assertIsNone(zone_id)
        self.assertEqual(hazard, 6)
        self.assertEqual(created, 1789000000)
        data = vault.open_('loot', payload)
        self.assertEqual(data['item'], 'PlateGeneralT4')
        self.assertEqual(data['amount'], 3)
        self.assertEqual(data['zone'], 'NA海域')

    def test_migration_resource_snapshots(self):
        vault = self.ready_vault()
        vault.ensure_migrated()
        conn = sqlite3.connect(self.root / 'config' / 'azurstats_local.db')
        row = conn.execute('SELECT oil, action_point, yellow_coin, purple_coin, opsi_payload FROM resource_snapshots').fetchone()
        conn.close()
        oil, ap, yellow, purple, payload = row
        self.assertEqual(oil, 14000)
        self.assertIsNone(ap)
        self.assertIsNone(yellow)
        self.assertIsNone(purple)
        data = vault.open_('res', payload)
        self.assertEqual(data['action_point'], 131)
        self.assertEqual(data['yellow_coin'], 500)
        self.assertEqual(data['purple_coin'], 20)

    def test_migration_files(self):
        vault = self.ready_vault()
        vault.ensure_migrated()
        ship = (self.root / 'log' / 'cl1' / 'inst' / 'ship_exp_data.json').read_text(encoding='utf-8')
        self.assertNotIn('battle_times', ship)
        wrapper = json.loads(ship)
        self.assertTrue(wrapper.get(opsi_secure.WRAPPER_KEY))
        data = vault.open_('ships', wrapper['payload'])
        self.assertEqual(data['battle_times']['average'], 52.0)
        csv_text = (self.root / 'log' / 'azurstat_meowofficer_farming.csv').read_text(encoding='utf-8')
        self.assertTrue(csv_text.startswith(opsi_secure.BLOB_PREFIX))
        csv_data = vault.open_('loot', csv_text)
        self.assertEqual(csv_data['rows'][0][0], '1.0')


if __name__ == '__main__':
    unittest.main()
