"""离线 ATX 资源的选择与完整性。"""

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from module.device.method import utils


class TestAtxAgentDownload(unittest.TestCase):
    def setUp(self):
        self.initer = object.__new__(utils.PatchedIniter)
        self.initer.abis = ['x86']
        self.initer.logger = Mock()

    def test_uses_openatx_release_for_selected_architecture(self):
        self.assertEqual(
            self.initer.atx_agent_url,
            'https://github.com/openatx/atx-agent/releases/download/0.10.0/atx-agent_0.10.0_linux_386.tar.gz',
        )

    def test_missing_bundled_asset_never_calls_download(self):
        with patch.dict('os.environ', {'AZURPILOT_INDEPENDENT': '1'}), patch.object(
            utils.Path, 'is_file', return_value=False,
        ), patch.object(utils.u2.init, 'mirror_download') as download:
            with self.assertRaises(FileNotFoundError):
                self.initer._cache_atx_agent(self.initer.atx_agent_url)
        download.assert_not_called()

    def test_corrupt_bundled_asset_is_rejected_before_cache_write(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'corrupt.tar.gz'
            archive.write_bytes(b'corrupt')
            checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
            self.assertNotEqual(checksum, '358d450001afe253339f0d1070b2a20ec758addfe163264ab265a74fcf16a6f7')
            with patch.dict('os.environ', {'AZURPILOT_INDEPENDENT': '1'}), patch.object(
                utils, 'Path', return_value=archive.parent,
            ):
                named = archive.with_name('atx-agent_0.10.0_linux_386.tar.gz')
                archive.rename(named)
                with self.assertRaises(ValueError):
                    self.initer._cache_atx_agent(self.initer.atx_agent_url)
