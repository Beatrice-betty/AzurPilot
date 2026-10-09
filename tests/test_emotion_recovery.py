"""心情相位计算、出击保护、校准和真实存档的定向回归。"""

import copy
import json
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from dev_tools.emotion_simulate import Clock, LuaOracle, MemoryConfig, QuietLogger, ShipSpec, simulate
import module.combat.emotion as production
import module.config.config as config_source
from module.combat.emotion_state import EmotionRecoveryState, PERIOD_US
from module.config.config import AzurLaneConfig, Function
from module.config.utils import read_file, write_file
from module.exception import RequestHumanTakeover, ScriptEnd


class RecoveryStateTests(unittest.TestCase):
    def test_tick_boundaries_and_partitioned_time_match(self):
        start = datetime(2026, 10, 9, microsecond=500_000)
        whole = EmotionRecoveryState.calibrate(70, start, 'dormitory_floor_2', True, False)
        split = copy.deepcopy(whole)
        for delta in (1, 17, 359_999_999, 360_000_000, 360_000_001, 5 * PERIOD_US + 113):
            split.advance(start + timedelta(microseconds=delta))
        whole.advance(split.record)
        self.assertEqual(whole.export(), split.export())
        self.assertEqual((100, 106), (whole.lower, whole.upper))

    def test_cap_and_zero_clamping_do_not_accumulate_uncertainty(self):
        state = EmotionRecoveryState.calibrate(119, datetime(2026, 10, 9), 'not_in_dormitory', False, False)
        state.advance(state.record + timedelta(hours=6))
        self.assertEqual((119, 119), (state.lower, state.upper))
        state.consume(120)
        self.assertEqual([(0, PERIOD_US, 0)], state.segments)
        self.assertEqual(0, state.value)

    def test_port_preserves_existing_emotion_above_119(self):
        state = EmotionRecoveryState.calibrate(150, datetime(2026, 10, 9), 'not_in_dormitory', True, False)
        state.advance(state.record + timedelta(days=30))
        self.assertEqual(150, state.value)
        state.consume(34)
        state.advance(state.record + timedelta(minutes=6))
        self.assertEqual(119, state.value)

    def test_onsen_cap_and_error_bound_are_explicit(self):
        state = EmotionRecoveryState.calibrate(119, datetime(2026, 10, 9), 'not_in_dormitory', False, True)
        state.advance(state.record + timedelta(days=1))
        self.assertEqual(150, state.value)
        state = EmotionRecoveryState.calibrate(80, state.record, 'dormitory_floor_2', True, True)
        state.advance(state.record + timedelta(seconds=60))
        self.assertEqual(7, state.upper - state.lower)
        self.assertEqual(4, state.upper - state.value)

    def test_bad_state_and_clock_rollback_are_rejected(self):
        state = EmotionRecoveryState.calibrate(80, datetime(2026, 10, 9), 'dormitory_floor_2', True, False)
        valid = state.export()
        for field, value in [('version', True), ('record', 'invalid'), ('signature', ['dormitory_floor_1', True, False]),
                             ('segments', [[1, PERIOD_US, 80]]), ('segments', [[0, PERIOD_US, 81]]),
                             ('segments', [[0, PERIOD_US, True]])]:
            bad = {**valid, field: value}
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                EmotionRecoveryState.restore(bad, 80, state.record, state.recover, state.oath, state.onsen)
        with self.assertRaises(ValueError):
            state.advance(state.record - timedelta(microseconds=1))

    def test_wait_target_is_conservative_and_ceil_to_second(self):
        state = EmotionRecoveryState.calibrate(40, datetime(2026, 10, 9, microsecond=500_000),
                                               'dormitory_floor_2', True, False)
        state.advance(state.record + timedelta(seconds=60))
        self.assertEqual((40, 46, 43), (state.lower, state.upper, state.value))
        target = state.recovered_at(42)
        self.assertEqual(datetime(2026, 10, 9, 0, 6, 1), target)
        state.advance(target)
        self.assertGreaterEqual(state.lower, 42)


class EmotionIntegrationTests(unittest.TestCase):
    def setup_tracker(self, initial=70, public=False):
        spec = ShipSpec('dormitory_floor_2', True)
        oracle = LuaOracle(spec, initial, 500_000, 0)
        clock = Clock(500_000, oracle)
        config = MemoryConfig(clock, spec, initial, 2, 'prevent_green_face', public)
        self.enterContext(patch.object(production, 'current_time', clock.now))
        self.enterContext(patch.object(config_source, 'current_time', clock.now))
        self.enterContext(patch.object(production, 'logger', QuietLogger()))
        self.enterContext(patch.object(config_source, 'logger', QuietLogger()))
        return clock, config, production.Emotion(config)

    def test_uncalibrated_active_fleet_stops_but_standby_does_not(self):
        _, config, emotion = self.setup_tracker()
        config.Emotion_Fleet2RecoveryState = None
        emotion.check_reduce(1)
        self.assertEqual(70, config.Emotion_Fleet2Value)
        self.assertIsNone(config.Emotion_Fleet2RecoveryState)
        config.Emotion_Fleet1RecoveryState = None
        with self.assertRaises(RequestHumanTakeover):
            emotion.check_reduce(1)

    def test_ignore_mode_does_not_require_calibration_or_record_costs(self):
        _, config, emotion = self.setup_tracker()
        config.Emotion_Mode = 'ignore'
        config.Emotion_Fleet1RecoveryState = None
        before = copy.deepcopy(config.fields)
        emotion.check_reduce(1)
        emotion.reduce(1)
        emotion.reduce(1, shipwreck=True)
        self.assertEqual(before, config.fields)
        self.assertEqual(0, emotion.total_reduced)

    def test_display_midpoint_does_not_authorize_unsafe_battle(self):
        clock, config, emotion = self.setup_tracker(initial=40)
        clock.advance(60_000_000)
        with self.assertRaises(ScriptEnd):
            emotion.check_reduce(1)
        self.assertGreater(emotion.fleet_1.current, 42)
        self.assertEqual(40, emotion.fleet_1.lower)
        self.assertGreater(config.fields['Main.Scheduler.NextRun'], clock.now())

    def test_save_latency_and_repeat_save_use_calculation_time(self):
        clock, config, emotion = self.setup_tracker()
        clock.advance(100_123_456)
        emotion.update()
        calculated_at = clock.now()
        clock.advance(30_333_333)
        emotion.record()
        emotion.record()
        self.assertEqual(calculated_at, config.Emotion_Fleet1Record)
        emotion.update()
        self.assertLessEqual(abs(emotion.fleet_1.current - clock.oracle.values[0]), 3)
        self.assertEqual(clock.now(), emotion.fleet_1.state.record)

    def test_recovery_condition_change_and_red_fallback_require_calibration(self):
        _, config, emotion = self.setup_tracker(public=True)
        config.PublicEmotion_FleetOath = False
        with self.assertRaises(RequestHumanTakeover):
            emotion.check_reduce(1)
        emotion.emergency_reset()
        self.assertEqual(0, config.PublicEmotion_FleetValue)
        self.assertIsNone(config.PublicEmotion_FleetRecoveryState)
        with self.assertRaises(RequestHumanTakeover):
            emotion.check_reduce(1)

    def test_clock_rollback_cannot_authorize_sortie(self):
        clock, _, emotion = self.setup_tracker()
        clock.advance(100_000_000)
        emotion.check_reduce(1)
        clock.us -= 1
        with self.assertRaises(RequestHumanTakeover):
            emotion.check_reduce(1)

    def test_record_conflict_does_not_authorize_using_old_state(self):
        clock, config, emotion = self.setup_tracker()
        original_multi_set = config.multi_set

        @contextmanager
        def recalibrate_during_save():
            with original_multi_set():
                yield
            stamp = clock.now() + timedelta(microseconds=1)
            config.Emotion_Fleet1Record = stamp
            config.Emotion_Fleet1RecoveryState = EmotionRecoveryState.calibrate(
                70, stamp, 'dormitory_floor_2', True, False).export()

        with patch.object(config, 'multi_set', recalibrate_during_save), self.assertRaises(RequestHumanTakeover):
            emotion.check_reduce(1)
        self.assertEqual(70, config.Emotion_Fleet1Value)
        self.assertIsNone(emotion.fleet_1.state)

    def test_double_book_oversized_map_limit_still_recovers(self):
        _, config, emotion = self.setup_tracker(initial=120)
        config.Emotion_Fleet1Control = 'keep_exp_bonus'
        emotion.update()
        target = emotion.fleet_1.get_recovered(32)
        emotion.fleet_1.state.advance(target)
        self.assertGreaterEqual(emotion.fleet_1.lower, 149)

    def test_shared_fleet_reload_uses_latest_task_state(self):
        report = simulate(ShipSpec('dormitory_floor_2', True), battles=300, public=True, alternate=True)
        self.assertLessEqual(max(report['max_overestimate'], report['max_underestimate']), 3)
        self.assertGreater(report['delays'], 0)

    def test_public_fleet_real_config_bind_and_save_across_tasks(self):
        from module.api.config_service import ConfigService
        from module.api.protocol import ConfigChange
        from tests.test_api import fixture

        clock, _, _ = self.setup_tracker(initial=85)
        with tempfile.TemporaryDirectory() as directory:
            service = ConfigService(fixture(directory))
            with patch('module.api.config_service.current_time', clock.now):
                service.patch('testpilot', None, [
                    ConfigChange(path='General.PublicEmotion.Enable', value=True),
                    ConfigChange(path='General.PublicEmotion.Tasks', value='Main, Event'),
                    ConfigChange(path='General.PublicEmotion.FleetRecover', value='dormitory_floor_2'),
                    ConfigChange(path='General.PublicEmotion.FleetOath', value=True),
                    ConfigChange(path='General.PublicEmotion.FleetValue', value=85),
                ])
            path = str(service.path('testpilot'))
            real = AzurLaneConfig.__new__(AzurLaneConfig)
            real.config_name = 'testpilot'
            real.root = service.root
            real.modified, real.bound, real.overridden = {}, {}, {}
            real.data = real.config_update(service.read('testpilot')[0])
            real._loaded_data = copy.deepcopy(real.data)
            real.auto_update = True
            with patch('module.config.config.filepath_config', return_value=path), patch(
                'module.config.config_updater.filepath_config', return_value=path
            ), patch.object(AzurLaneConfig, 'config_override'):
                for task in ('Main', 'Event'):
                    real.bind(task)
                    real.task = Function(real.data[task])
                    emotion = production.Emotion(real)
                    self.assertTrue(emotion.using_public)
                    emotion.check_reduce(1)
                    emotion.reduce(1)
                fields = service.read('testpilot')[0]['General']['PublicEmotion']
                self.assertEqual(81, fields['FleetValue'])
                self.assertEqual([[0, PERIOD_US, 81]], fields['FleetRecoveryState']['segments'])

    def test_real_atomic_json_matches_memory_through_restarts(self):
        clock, fake, left = self.setup_tracker()
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'anonymous.json')
            disk = {'Main': {'Scheduler': {'Enable': False, 'Command': 'Main', 'NextRun': clock.now()},
                             'Emotion': {key.removeprefix('Emotion_'): value for key, value in fake.fields.items()
                                         if key.startswith('Emotion_')}},
                    'Alas': {'PublicEmotion': {'Enable': False, 'Tasks': ''}}}
            write_file(path, disk)
            real = AzurLaneConfig.__new__(AzurLaneConfig)
            real.bound, real.modified, real.overridden = {}, {}, {}
            real.config_name = 'anonymous'
            real.data = copy.deepcopy(disk)
            real._loaded_data = copy.deepcopy(disk)
            real.auto_update = True
            real.task = Function(disk['Main'])

            def read_config(name):
                data = read_file(path)
                for key, value in data['Main']['Emotion'].items():
                    if key.endswith('Record'):
                        data['Main']['Emotion'][key] = datetime.fromisoformat(value)
                data['Main']['Scheduler']['NextRun'] = datetime.fromisoformat(data['Main']['Scheduler']['NextRun'])
                return data

            real.read_file = read_config
            real.write_file = lambda name, data: write_file(path, data)
            real.bind('Main')
            with patch.object(config_source, 'filepath_config', return_value=path):
                for i in range(100):
                    clock.advance(90_000_000 + i * 7919)
                    right = production.Emotion(real)
                    left.reduce(1)
                    right.reduce(1)
                    clock.oracle.consume(0, 2)
                    for prefix in ('Emotion_Fleet1', 'Emotion_Fleet2'):
                        for suffix in ('Value', 'Record', 'RecoveryState'):
                            self.assertEqual(getattr(fake, prefix + suffix), getattr(real, prefix + suffix))
                    saved = json.loads(Path(path).read_text(encoding='utf-8'))['Main']['Emotion']
                    self.assertEqual(saved['Fleet1RecoveryState']['record'], real.Emotion_Fleet1Record.isoformat(timespec='microseconds'))
                    self.assertEqual(saved['Fleet1Value'], right.fleet_1.current)


if __name__ == '__main__':
    unittest.main()
