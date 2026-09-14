"""验证强制移动的“漏猫”兜底：效率模式何时升级为保守模式。

背景：明石刷新在舰队模型旁边时图标会被挡住，或目标点超出舰队移动范围
（游戏提示“目标点超出移动范围”，即 `handle_walk_out_of_step` 抓的
`TEMPLATE_MAP_WALK_OUT_OF_STEP`），此时效率模式「只换队看雷达、一支都不挪动」
永远点不到猫——只有把挡路的舰队挪开才能解决。

因此 `clear_question` 连续看到问号却清不掉时置位 `_question_unreachable`，
`_execute_fixed_patrol_scan` 在效率模式且没人点到时据此升级为保守模式；
反过来，雷达上压根没有问号时绝不能升级，否则效率模式就退化成保守模式。
"""

import unittest
from contextlib import nullcontext
from types import SimpleNamespace

from module.os.map import ALREADY_SOLVED_MAP_EVENTS, OSMap


class FixedPatrolStub:
    """只提供 `_execute_fixed_patrol_scan` 需要的属性。"""

    def __init__(self, level, any_fleet_result, unreachable):
        self.config = SimpleNamespace(OpsiFleet_Fleet=1)
        self.map = SimpleNamespace(grids=[object()])
        self.level = level
        self.any_fleet_result = any_fleet_result
        self._question_unreachable = unreachable
        self.recovery_calls = 0
        self.fleet_sets = []

    def map_init(self, map_=None):
        pass

    def _forced_move_level(self):
        return self.level

    def clear_question_any_fleet(self, drop=None):
        return self.any_fleet_result

    def _execute_akashi_recovery(self):
        self.recovery_calls += 1

    def fleet_set(self, index=1):
        self.fleet_sets.append(index)
        return True


class TestFixedPatrolEscalation(unittest.TestCase):
    def run_scan(self, level, any_fleet_result, unreachable):
        stub = FixedPatrolStub(level, any_fleet_result, unreachable)
        OSMap._execute_fixed_patrol_scan(stub, ExecuteFixedPatrolScan=True)
        return stub

    def test_efficiency_mode_escalates_when_question_unreachable(self):
        """有舰队看到了问号却谁都到不了 -> 升级保守模式挪开舰队。"""
        stub = self.run_scan(level=1, any_fleet_result=False, unreachable=True)
        self.assertEqual(stub.recovery_calls, 1)

    def test_efficiency_mode_keeps_when_nothing_found(self):
        """雷达上压根没有问号 -> 保持效率模式，不升级。"""
        stub = self.run_scan(level=1, any_fleet_result=False, unreachable=False)
        self.assertEqual(stub.recovery_calls, 0)

    def test_efficiency_mode_keeps_when_already_solved(self):
        """换舰队已经点到了 -> 不升级。"""
        stub = self.run_scan(level=1, any_fleet_result=True, unreachable=True)
        self.assertEqual(stub.recovery_calls, 0)

    def test_conservative_mode_is_unaffected(self):
        """等级 2 直接走保守模式，与问号标志无关。"""
        for unreachable in (True, False):
            with self.subTest(unreachable=unreachable):
                stub = self.run_scan(level=2, any_fleet_result=False, unreachable=unreachable)
                self.assertEqual(stub.recovery_calls, 1)

    def test_closed_level_does_nothing(self):
        """等级 0 关闭强制移动。"""
        stub = self.run_scan(level=0, any_fleet_result=False, unreachable=True)
        self.assertEqual(stub.recovery_calls, 0)

    def test_main_fleet_restored_after_scan(self):
        """无论走哪条分支，结束后都要复位主队。"""
        for level, unreachable in ((1, True), (1, False), (2, False)):
            with self.subTest(level=level, unreachable=unreachable):
                stub = self.run_scan(level=level, any_fleet_result=False, unreachable=unreachable)
                self.assertEqual(stub.fleet_sets, [1])


class ClearQuestionStub:
    """只提供 `clear_question` 需要的属性。"""

    def __init__(self, predictions, walk_result=''):
        self.predictions = list(predictions)
        self.walk_result = walk_result
        self.config = SimpleNamespace(temporary=lambda **kwargs: nullcontext())
        self.zone = SimpleNamespace(is_port=False)
        self.device = SimpleNamespace(image=object(), click=lambda grid: None)
        self.view = SimpleNamespace(
            select=lambda **kwargs: SimpleNamespace(count=1),
            predict=lambda: None,
            show=lambda: None,
        )
        self.radar = SimpleNamespace(predict_question=self.predict_question)
        self.is_siren_device_confirmed = False
        self._solved_map_event = set()
        self._question_unreachable = False

    def predict_question(self, image, in_port=True):
        return self.predictions.pop(0) if self.predictions else None

    def handle_info_bar(self):
        pass

    def update_os(self):
        pass

    def convert_radar_to_local(self, grid):
        return grid

    def _should_skip_siren_research(self, grid):
        return False

    def wait_until_walk_stable(self, **kwargs):
        return self.walk_result


def make_question_grid(is_logging_tower=False):
    return SimpleNamespace(is_logging_tower=is_logging_tower)


class TestClearQuestionUnreachableFlag(unittest.TestCase):
    def run_clear_question(self, predictions, walk_result=''):
        stub = ClearQuestionStub(predictions, walk_result)
        result = OSMap.clear_question(stub)
        return stub, result

    def test_marks_unreachable_after_all_attempts_failed(self):
        """三次都在雷达上看到问号却清不掉 -> 置位不可达。"""
        grid = make_question_grid()
        stub, result = self.run_clear_question([grid, grid, grid])
        self.assertFalse(result)
        self.assertTrue(stub._question_unreachable)

    def test_does_not_mark_when_radar_has_no_question(self):
        """雷达上没有问号 -> 只是没得清，不算“看到了却到不了”。"""
        stub, result = self.run_clear_question([None])
        self.assertFalse(result)
        self.assertFalse(stub._question_unreachable)

    def test_does_not_mark_when_akashi_reached(self):
        """点到明石 -> 不置位。"""
        grid = make_question_grid()
        stub, result = self.run_clear_question([grid], walk_result='akashi')
        self.assertTrue(result)
        self.assertFalse(stub._question_unreachable)

    def test_does_not_mark_when_logging_tower_triggered(self):
        """移动触发了记录塔剧情 -> 视为问号已解决，不置位。"""
        grid = make_question_grid(is_logging_tower=True)
        stub, result = self.run_clear_question([grid], walk_result='event')
        self.assertTrue(result)
        self.assertFalse(stub._question_unreachable)


class AnyFleetStub:
    """只提供 `clear_question_any_fleet` 需要的属性。"""

    def __init__(self, radar_results, solve_on_fleet=None):
        self.config = SimpleNamespace(OpsiFleet_Fleet=1)
        self.zone = SimpleNamespace(is_port=False)
        self.device = SimpleNamespace(image=object(), screenshot=lambda: None)
        self.radar = SimpleNamespace(predict_question=self.predict_question)
        self.radar_results = list(radar_results)
        # 指定“第几支舰队清问号时成功”；None 表示永远清不掉
        self.solve_on_fleet = solve_on_fleet
        self._solved_map_event = set()
        self._solved_fleet_mechanism = False
        self._question_unreachable = False
        self.fleet_sets = []

    def predict_question(self, image, in_port=True):
        return self.radar_results.pop(0) if self.radar_results else None

    def fleet_set(self, index=1):
        self.fleet_sets.append(index)
        return True

    def clear_question(self, drop=None):
        # 真实实现只有雷达上看到问号时才会被调用；看到了却清不掉就置位不可达
        self._question_unreachable = True
        if self.solve_on_fleet is not None and self.fleet_sets[-1] == self.solve_on_fleet:
            self._solved_map_event.add('is_akashi')
            return True
        return False

    def map_rescan_once(self, rescan_mode='full', drop=None):
        return False


class TestAnyFleetFleetOrder(unittest.TestCase):
    def run_any_fleet(self, stub):
        result = OSMap.clear_question_any_fleet(stub)
        return stub, result

    def test_starts_from_primary_then_others(self):
        """先主队，再按编号补上其余舰队，且全程不切回主队。"""
        stub = AnyFleetStub([None, None, None, None])
        self.run_any_fleet(stub)
        self.assertEqual(stub.fleet_sets, [1, 2, 3, 4])

    def test_resets_unreachable_flag_when_nothing_seen(self):
        """本轮雷达上什么都没有 -> 清掉上一轮的不可达标记。"""
        stub = AnyFleetStub([None, None, None, None])
        stub._question_unreachable = True
        self.run_any_fleet(stub)
        self.assertFalse(stub._question_unreachable)

    def test_keeps_unreachable_flag_when_fleet_saw_question(self):
        """某舰队看到问号却清不掉 -> 标记保留给调用方升级保守模式。"""
        stub = AnyFleetStub([make_question_grid(), None, None, None])
        self.run_any_fleet(stub)
        self.assertTrue(stub._question_unreachable)

    def test_other_fleet_can_solve_the_question(self):
        """主队清不掉、第 3 舰队清掉了 -> 立即结束并标记已解决。"""
        stub = AnyFleetStub(
            [make_question_grid(), make_question_grid(), make_question_grid(), None],
            solve_on_fleet=3,
        )
        result = OSMap.clear_question_any_fleet(stub)
        self.assertTrue(result)
        self.assertEqual(stub.fleet_sets, [1, 2, 3])


if __name__ == '__main__':
    unittest.main()
