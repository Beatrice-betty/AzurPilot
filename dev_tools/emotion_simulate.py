"""离线验证正式心情代码的长期误差；不连接模拟器、不读取真实实例配置。

对照规则核实于客户端 CN/model/proxy/bayproxy.lua、CN/model/vo/ship.lua
及 CN/sharecfg/gameset.lua（Lua 提交 d4f86ec76faf39f83fa3f618a2b1c4d6e9f6850e）。
对照只使用独立常量与逐批次整数恢复，不借用被测模型的相位或恢复公式。
"""

import argparse
import hashlib
import json
import random
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import module.combat.emotion as production
import module.config.config as config_source
from module.combat.emotion_state import EmotionRecoveryState
from module.config.config import AzurLaneConfig
from module.exception import ScriptEnd


US = 1_000_000
PERIOD = 360_000_000
BASE = datetime(2026, 10, 9)


class QuietLogger:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


@dataclass(frozen=True)
class ShipSpec:
    recover: str
    oath: bool

    @property
    def cap(self):
        return 119 if self.recover == 'not_in_dormitory' else 150

    @property
    def label(self):
        return {'not_in_dormitory': '港区', 'dormitory_floor_1': '后宅一楼',
                'dormitory_floor_2': '后宅二楼'}[self.recover] + ('已婚' if self.oath else '未婚')


class LuaOracle:
    """按独立的确定相位逐批次恢复，包含上限和零值截断。"""

    def __init__(self, spec, initial, origin, phase):
        self.spec = spec
        self.values = [initial, initial]
        self.last = origin
        self.next_tick = origin + PERIOD - phase
        self.gains = [0, 0]
        self.used = [0, 0]

    def advance(self, now):
        assert now >= self.last
        while self.next_tick <= now:
            for i, old in enumerate(self.values):
                base = 3 if self.spec.oath else 2
                extra = {'not_in_dormitory': 0, 'dormitory_floor_1': 2,
                         'dormitory_floor_2': 3}[self.spec.recover]
                cap = 119 if extra == 0 else 150
                new = min(old + max(min(base, cap - old), 0) + extra, 150)
                self.values[i] = new
                self.gains[i] += new - old
            self.next_tick += PERIOD
        self.last = now

    def consume(self, index, cost):
        assert self.values[index] >= cost, '对照舰队心情不足，不能伪造完成出击'
        self.values[index] -= cost
        self.used[index] += cost


def json_roundtrip(fields):
    """和实际 JSON 一样保留 datetime 的微秒，恢复读取时的 datetime 类型。"""
    restored = json.loads(json.dumps(fields, default=str))
    for key, value in restored.items():
        if key.endswith('Record') and isinstance(value, str):
            restored[key] = datetime.fromisoformat(value)
    return restored


class MemoryConfig:
    """只替换存储介质，调度目标仍调用生产 task_delay。"""

    def __init__(self, clock, spec, initial, cost, control, public=False, shared=None):
        self.fields = {}
        self.shared = {} if shared is None else shared
        self.saves = 0
        self.task = SimpleNamespace(command='Main')
        self.Emotion_Mode = 'calculate'
        self.Emotion_IgnoreShipwreck = False
        self.Fleet_FleetOrder = 'fleet1_all_fleet2_standby'
        self.Campaign_Use2xBook = cost == 4
        self.PublicEmotion_Enable = public
        self.PublicEmotion_Tasks = 'Main, Event'
        for prefix in ['Emotion_Fleet1', 'Emotion_Fleet2'] + (['PublicEmotion_Fleet'] if public and not self.shared else []):
            values = {'Value': initial, 'Record': clock.now(), 'Recover': spec.recover,
                      'Oath': spec.oath, 'Onsen': False, 'Control': control}
            values['RecoveryState'] = EmotionRecoveryState.calibrate(
                initial, clock.now(), spec.recover, spec.oath, False).export()
            for key, value in values.items():
                setattr(self, prefix + key, value)

    def __getattr__(self, key):
        target = self.shared if key.startswith('PublicEmotion_Fleet') else self.fields
        if key not in target:
            raise AttributeError(key)
        return target[key]

    def __setattr__(self, key, value):
        if key.startswith(('Emotion_', 'PublicEmotion_Fleet')):
            target = self.shared if key.startswith('PublicEmotion_Fleet') else self.fields
            target[key] = value
        else:
            object.__setattr__(self, key, value)

    @contextmanager
    def multi_set(self):
        try:
            yield
        finally:
            restored = json_roundtrip(self.fields)
            self.fields.clear()
            self.fields.update(restored)
            restored = json_roundtrip(self.shared)
            self.shared.clear()
            self.shared.update(restored)
            self.saves += 1

    def cross_set(self, key, value):
        self.fields[key] = value

    task_delay = AzurLaneConfig.task_delay


class Clock:
    def __init__(self, start, oracle):
        self.us = self.origin = start
        self.advanced = 0
        self.oracle = oracle
        self.observe = lambda: None

    def now(self):
        return BASE + timedelta(microseconds=self.us)

    def _move(self, target):
        assert target >= self.us
        self.advanced += target - self.us
        self.us = target
        self.oracle.advance(target)
        self.observe()

    def advance(self, delta):
        assert type(delta) is int and delta >= 0
        target = self.us + delta
        while self.oracle.next_tick <= target:
            tick = self.oracle.next_tick
            if tick - 1 > self.us:
                self._move(tick - 1)
            self._move(tick)
            if tick + 1 <= target:
                self._move(tick + 1)
        if self.us < target:
            self._move(target)


def simulate(spec, *, battles=10_000, cost=2, phase=179 * US, pattern='jitter90',
             control='prevent_green_face', public=False, dual=False, alternate=False, initial=None):
    start = spec.cap if initial is None else initial
    oracle = LuaOracle(spec, start, 500_000, phase)
    clock = Clock(500_000, oracle)
    shared = {}
    configs = [MemoryConfig(clock, spec, start, cost, control, public, shared)
               for _ in range(2 if alternate else 1)]
    for i, config in enumerate(configs):
        config.task.command = 'Main' if i == 0 else 'Event'
        if dual:
            config.Fleet_FleetOrder = 'fleet1_mob_fleet2_boss'
    emotions = [production.Emotion(config) for config in configs]
    for emotion in emotions:
        emotion.map_is_2x_book = cost == 4
    active = emotions[0]
    completed = maps = delays = checks = max_over = max_under = max_segments = 0
    duration_us = wait_us = idle_us = 0
    ledger = [0, 0]
    worst = None
    rng = random.Random(20261009)

    def observe():
        nonlocal checks, max_over, max_under, max_segments, worst
        active.update()
        fleets = [active.public_fleet] if public else active.fleets
        for i, fleet in enumerate(fleets):
            truth = oracle.values[i]
            error = fleet.current - truth
            assert fleet.lower <= truth <= fleet.upper, (completed, clock.us, fleet.lower, truth, fleet.upper)
            assert abs(error) <= 3, (completed, clock.us, error)
            assert fleet.upper - fleet.lower <= fleet.speed
            assert len(fleet.state.segments) <= fleet.speed + 2
            max_segments = max(max_segments, len(fleet.state.segments))
            if worst is None or abs(error) > abs(worst['error']):
                worst = {'battle': completed, 'microseconds': clock.us, 'fleet': i + 1,
                         'calculated': fleet.current, 'lua': truth, 'error': error}
            max_over, max_under = max(max_over, error), max(max_under, -error)
            checks += 1

    clock.observe = observe
    with patch.object(production, 'current_time', clock.now), patch.object(config_source, 'current_time', clock.now), \
            patch.object(production, 'logger', QuietLogger()), patch.object(config_source, 'logger', QuietLogger()):
        observe()
        while completed < battles:
            index = maps % len(configs)
            config, active = configs[index], emotions[index]
            count = min(7, battles - completed)
            attempts = 0
            while True:
                try:
                    active.check_reduce(count)
                    observe()
                    break
                except ScriptEnd:
                    delays += 1
                    attempts += 1
                    assert attempts < 5, '任务等待未能前进'
                    target = config.fields[f'{config.task.command}.Scheduler.NextRun']
                    delta = (target - clock.now()) // timedelta(microseconds=1)
                    assert delta > 0, '调度器提前取整导致反复立即唤醒'
                    wait_us += delta
                    clock.advance(delta)
                    active = emotions[index] = production.Emotion(config)
                    active.map_is_2x_book = cost == 4
            order = [1] * count if not dual else [1] * (count - 1) + [2]
            for fleet_index in order:
                observe()
                before = active.total_reduced
                active.reduce(fleet_index)
                assert active.total_reduced - before == cost
                oracle_index = 0 if public else fleet_index - 1
                oracle.consume(oracle_index, cost)
                ledger[oracle_index] += cost
                completed += 1
                observe()
                if pattern in ('jitter90', 'full_rest'):
                    duration = rng.randrange(70 * US, 110 * US)
                elif pattern == 'half90':
                    duration = 90 * US + 500_000
                elif pattern == 'fast30':
                    duration = 30 * US + 500_000
                elif pattern == 'slow600':
                    duration = 600 * US
                elif pattern == 'balanced':
                    rate = (3 if spec.oath else 2) + {'not_in_dormitory': 0, 'dormitory_floor_1': 2,
                                                     'dormitory_floor_2': 3}[spec.recover]
                    duration = cost * PERIOD // rate
                else:
                    raise ValueError(pattern)
                duration_us += duration
                clock.advance(duration)
            maps += 1
            if pattern == 'full_rest' and maps % 30 == 0:
                rest = 6 * 3600 * US
                idle_us += rest
                clock.advance(rest)
            if maps % 100 == 0:
                active = emotions[index] = production.Emotion(config)
                active.map_is_2x_book = cost == 4
        observe()
    assert completed == battles
    assert clock.us - clock.origin == clock.advanced == duration_us + wait_us + idle_us
    assert oracle.last == clock.us
    assert ledger == oracle.used and sum(ledger) == battles * cost
    assert oracle.values == [start + gain - used for gain, used in zip(oracle.gains, oracle.used)]
    return {'configuration': spec.label, 'cost': cost, 'phase_us': phase, 'pattern': pattern,
            'control': control, 'public': public, 'dual': dual, 'alternate': alternate,
            'battles': completed, 'checks': checks, 'max_overestimate': max_over,
            'max_underestimate': max_under, 'worst': worst, 'max_segments': max_segments,
            'delays': delays, 'saves': sum(c.saves for c in configs), 'hours': (clock.us - clock.origin) / (3600 * US),
            'invariants': '场次、扣减、整数微秒、恢复收支、相位范围和JSON重载一致'}


def run_suite(battles=10_000):
    results = []
    for recover in ('not_in_dormitory', 'dormitory_floor_1', 'dormitory_floor_2'):
        for oath in (False, True):
            spec = ShipSpec(recover, oath)
            for cost in (2, 4):
                for phase in (0, 179 * US, PERIOD - 1):
                    results.append(simulate(spec, battles=battles, cost=cost, phase=phase))
            print(f'{spec.label}: 6 组，每组 {battles} 场通过', flush=True)
    spec = ShipSpec('dormitory_floor_2', True)
    for options in ({'dual': True}, {'public': True, 'alternate': True}, {'pattern': 'half90'},
                    {'pattern': 'balanced', 'initial': 70}, {'pattern': 'full_rest'},
                    {'control': 'keep_exp_bonus'}, {'pattern': 'fast30'}, {'pattern': 'slow600'}):
        results.append(simulate(spec, battles=battles, phase=PERIOD - 1, **options))
    files = ('module/combat/emotion.py', 'module/combat/emotion_state.py')
    report = {'case_count': len(results), 'battle_count': sum(r['battles'] for r in results),
              'max_absolute_error': max(max(r['max_overestimate'], r['max_underestimate']) for r in results),
              'checks': sum(r['checks'] for r in results),
              'source_sha256': {file: hashlib.sha256(Path(file).read_bytes()).hexdigest() for file in files},
              'scope': '全S胜、准确起点、固定且一致的恢复条件；不包含温泉、饮料、识别延迟或实机验收',
              'results': results}
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--battles', type=int, default=10_000)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.battles <= 0:
        parser.error('--battles 必须大于零')
    report = run_suite(args.battles)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key != 'results'}, ensure_ascii=False))
