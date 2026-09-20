"""使用临时目录验证完整实例名的日志写入与 MCP 读取归属。"""
import datetime
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import module.logger as logging_module
from module.mcp.tools import Tools


class InstanceLogPathTests(unittest.TestCase):
    def test_log_writers_keep_full_instance_names(self):
        original = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            os.chdir(directory)
            try:
                for writer in (logging_module.set_file_logger, logging_module._set_file_logger):
                    for name in ('account_a', 'account_b', '账号 一.二'):
                        with self.subTest(writer=writer.__name__, name=name), \
                                patch.object(logging_module.logger, 'handlers', []), \
                                patch.object(logging_module.logger, 'log_file', None):
                            try:
                                writer(name)
                                logging_module.logger.info('实例标识: %s', name)
                                path = logging_module.get_log_file_path(name)
                                self.assertEqual(Path(logging_module.logger.log_file).resolve(), path.resolve())
                                self.assertIn(name, path.read_text(encoding='utf-8'))
                            finally:
                                for handler in logging_module.logger.handlers:
                                    if isinstance(handler, logging_module.RichTimedRotatingHandler):
                                        handler.richd.console.file.close()
                                    handler.close()
                self.assertFalse(logging_module.get_log_file_path('account').exists())
            finally:
                os.chdir(original)

    def test_missing_instance_log_never_uses_another_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            default = logging_module.get_log_file_path('alas', root)
            default.parent.mkdir()
            default.write_text('默认实例私有日志', encoding='utf-8')
            configs = SimpleNamespace(root=root, path=lambda name: root / 'config' / f'{name}.json')
            tools = Tools(configs=configs)
            missing = tools.log_path('account_b')
            self.assertFalse(missing.exists())
            self.assertEqual(missing.name, f'{datetime.date.today()}_account_b.txt')
            self.assertEqual(tools.log_path('alas'), default)
            missing.write_text('B 的日志', encoding='utf-8')
            self.assertEqual(tools.log_path('account_b').read_text(encoding='utf-8'), 'B 的日志')

