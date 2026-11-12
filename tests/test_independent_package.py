"""离线验证独立更新的验签、选源和文件恢复。"""

import base64
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
                independent, 'fetch',
                side_effect=[first, self.signed(self.key, revision='2', sequence=11)],
            ) as fetch:
                offer = independent.check(self.root)
                self.assertEqual('2' * 40, offer[0]['revision'])
                self.assertEqual(independent.SOURCES[1][0], offer[1])
                self.assertEqual(independent.SOURCES[0][0] + 'release.json', fetch.call_args_list[0].args[0])

    def test_unavailable_release_keeps_installed_receipt(self):
        with patch.object(independent, 'fetch', side_effect=TimeoutError):
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

    def test_local_edit_refuses_replacement(self):
        ready = self.ready()
        (self.root / 'gui.py').write_text('local edit', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, '本机修改'):
            independent.apply(ready, self.root)
        self.assertEqual('local edit', (self.root / 'gui.py').read_text())
        self.assertFalse(independent.trial_pending(self.root))
