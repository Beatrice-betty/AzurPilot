"""离线验证独立更新的验签、选源和文件恢复。"""

import base64
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from deploy import independent


class TestIndependentPackage(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.key = Ed25519PrivateKey.generate()
        self.current = {
            'schema': 1, 'revision': '1' * 40, 'sequence': 10,
            'dependencies': 'a' * 64, 'frontendSha256': 'b' * 64,
            'publicKey': base64.b64encode(self.key.public_key().public_bytes_raw()).decode(),
        }
        independent.write_json(self.root / independent.RECEIPT, self.current)

    def signed(self, key, *, revision, sequence):
        data = {**self.current, 'revision': revision * 40, 'sequence': sequence}
        data.pop('publicKey')
        return independent.canonical({
            'release': data,
            'signature': base64.b64encode(key.sign(independent.canonical(data))).decode(),
        })

    def test_old_or_invalid_domestic_manifest_uses_github(self):
        for first in (self.signed(self.key, revision='0', sequence=9),
                      self.signed(Ed25519PrivateKey.generate(), revision='9', sequence=50)):
            with self.subTest(first=first), patch.object(
                independent, 'gitcode_checkout', return_value=self.root,
            ), patch.object(
                independent, 'fetch_gitcode_release', return_value='a' * 40,
            ), patch.object(
                independent, 'gitcode_release_file', return_value=first,
            ), patch.object(
                independent, 'fetch', return_value=self.signed(self.key, revision='2', sequence=11),
            ) as fetch:
                offer = independent.check(self.root)
                self.assertEqual('2' * 40, offer[0]['revision'])
                self.assertEqual(independent.GITHUB_REPOSITORY, offer[2])
                fetch.assert_called_once_with(
                    independent.GITHUB_RELEASE + 'release.json', independent.MAX_MANIFEST_SIZE,
                )

    def test_gitcode_frontend_uses_same_release_commit_as_manifest(self):
        revision = 'a' * 40
        manifest = self.signed(self.key, revision='2', sequence=11)
        zip_path = self.root / 'frontend.zip'
        with patch.object(independent, 'gitcode_checkout', return_value=self.root), patch.object(
            independent, 'fetch_gitcode_release', return_value=revision,
        ) as fetch_git, patch.object(
            independent, 'gitcode_release_file', side_effect=[manifest, b'frontend archive'],
        ) as read_git, patch.object(independent, 'fetch') as raw:
            _, reference, repository = independent.check(self.root)
            independent.fetch_frontend(self.root, repository, reference, zip_path)
        self.assertEqual(b'frontend archive', zip_path.read_bytes())
        fetch_git.assert_called_once_with(self.root, self.root)
        self.assertEqual(
            [call.args[2:4] for call in read_git.call_args_list],
            [(revision, 'release.json'), (revision, 'frontend.zip')],
        )
        raw.assert_not_called()

    def test_unavailable_release_keeps_installed_receipt(self):
        with patch.object(independent, 'gitcode_checkout', side_effect=TimeoutError), patch.object(
            independent, 'fetch', side_effect=TimeoutError,
        ):
            with self.assertRaisesRegex(RuntimeError, '当前版本'):
                independent.check(self.root)
        self.assertEqual(self.current, independent.read_json(self.root / independent.RECEIPT))

    def ready(self):
        app = self.root / 'gui.py'
        app.write_text('old', encoding='utf-8')
        config = self.root / 'config'
        config.mkdir()
        (config / 'player.json').write_text('old data', encoding='utf-8')
        independent.write_json(self.root / independent.FILES, {'gui.py': independent.digest(app)})
        prepared = self.root / independent.WORK / 'ready-sample'
        (prepared / 'app/assets').mkdir(parents=True)
        (prepared / 'app/gui.py').write_text('new', encoding='utf-8')
        (prepared / 'app/assets/new.txt').write_text('new asset', encoding='utf-8')
        independent.write_json(prepared / 'version.json', {**self.current, 'revision': '2' * 40, 'sequence': 11})
        independent.write_json(prepared / 'files.json', {
            name: independent.digest(prepared / 'app' / name)
            for name in ('gui.py', 'assets/new.txt')
        })
        return prepared

    def test_trial_failure_restores_version_and_preserves_failed_data_copy(self):
        ready = self.ready()
        independent.apply(ready, self.root)
        self.assertEqual('new', (self.root / 'gui.py').read_text())
        (self.root / 'config/player.json').write_text('new data', encoding='utf-8')
        self.assertTrue(independent.recover(self.root))
        self.assertFalse(independent.recover(self.root))
        self.assertEqual('old', (self.root / 'gui.py').read_text())
        self.assertEqual('old data', (self.root / 'config/player.json').read_text())
        self.assertEqual('new data', (ready / 'config-after-failed-update/player.json').read_text())
        self.assertFalse((self.root / 'assets/new.txt').exists())
        self.assertEqual(self.current, independent.read_json(self.root / independent.RECEIPT))

    def test_commit_clears_staged_app_and_retains_consistent_database_backup(self):
        ready = self.ready()
        database = self.root / 'config/azurpilot.db'
        writer = sqlite3.connect(database)
        self.addCleanup(writer.close)
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('CREATE TABLE history (value TEXT)')
        writer.execute('INSERT INTO history VALUES (?)', ('saved',))
        writer.commit()
        independent.apply(ready, self.root)
        independent.confirm(self.root)
        self.assertFalse(independent.trial_pending(self.root))
        self.assertFalse((ready / 'app').exists())
        backup = sqlite3.connect(ready / 'previous/config/azurpilot.db')
        try:
            self.assertEqual([('saved',)], backup.execute('SELECT value FROM history').fetchall())
        finally:
            backup.close()

    def test_local_edit_refuses_replacement(self):
        ready = self.ready()
        (self.root / 'gui.py').write_text('local edit', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, '本机修改'):
            independent.apply(ready, self.root)
        self.assertEqual('local edit', (self.root / 'gui.py').read_text())
        self.assertFalse(independent.trial_pending(self.root))
