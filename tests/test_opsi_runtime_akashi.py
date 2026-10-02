"""验证明石异步统计确实落库，提交失败不输出成功次数。"""

import tempfile
import threading
import unittest
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.statistics import cl1_database as database
from module.statistics import opsi_runtime


class TestAkashiAsyncPersistence(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = database.Cl1Database(db_path=Path(self.directory.name) / 'cl1.db')
        self.config = SimpleNamespace(config_name='test')

    @staticmethod
    def wait_completed(future):
        callbacks_finished = threading.Event()
        future.add_done_callback(lambda _: callbacks_finished.set())
        future.result(timeout=10)
        if not callbacks_finished.wait(timeout=10):
            raise AssertionError('统计完成回调没有执行')

    def test_game_thread_does_not_wait_for_pending_database_write(self):
        pending = Future()
        db = Mock()
        db.async_increment_akashi_encounter.return_value = pending
        with patch.object(database, 'db', db), patch.object(opsi_runtime.logger, 'attr') as log:
            self.assertIs(opsi_runtime.record_cl1_akashi_encounter(self.config), pending)
            log.assert_not_called()
            db.async_get_stats.assert_not_called()
            pending.set_result(1)
            log.assert_called_once_with('侵蚀1明石月度次数', 1)

    def test_every_encounter_is_committed_and_logs_real_count(self):
        with patch.object(database, 'db', self.db), patch.object(opsi_runtime.logger, 'attr') as log:
            futures = [opsi_runtime.record_cl1_akashi_encounter(self.config) for _ in range(30)]
            for future in futures:
                self.wait_completed(future)
            month = opsi_runtime.datetime.now().strftime('%Y-%m')
            self.assertEqual(self.db.get_stats('test', month)['akashi_encounters'], 30)
            self.assertEqual([call.args[1] for call in log.call_args_list], list(range(1, 31)))

    def test_failed_transaction_never_logs_success_or_changes_count(self):
        callbacks_finished = threading.Event()
        with (
            patch.object(database, 'db', self.db),
            patch.object(self.db, '_save_stats_in_connection', side_effect=RuntimeError('写入失败')),
            patch.object(opsi_runtime.logger, 'attr') as log,
            patch.object(opsi_runtime.logger, 'exception') as failure,
        ):
            future = opsi_runtime.record_cl1_akashi_encounter(self.config)
            future.add_done_callback(lambda _: callbacks_finished.set())
            with self.assertRaisesRegex(RuntimeError, '写入失败'):
                future.result(timeout=10)
            self.assertTrue(callbacks_finished.wait(timeout=10))
            log.assert_not_called()
            failure.assert_called_once()
        month = opsi_runtime.datetime.now().strftime('%Y-%m')
        self.assertEqual(self.db.get_stats('test', month)['akashi_encounters'], 0)

    def test_explicit_event_month_is_kept_when_background_worker_crosses_month(self):
        self.assertEqual(self.db.increment_akashi_encounter('test', '2026-09'), 1)
        self.assertEqual(self.db.get_stats('test', '2026-09')['akashi_encounters'], 1)
        self.assertEqual(self.db.get_stats('test', '2026-10')['akashi_encounters'], 0)


if __name__ == '__main__':
    unittest.main()
