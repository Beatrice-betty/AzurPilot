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


class RecoveryStub:
    """只提供 `_recover_unreachable_akashi` 需要的属性。"""

    # 标记逻辑是真实实现（类属性默认 set 是实例共享的，注释里写明了要重新赋值）
    _mark_event_unreachable = OSMap._mark_event_unreachable

    def __init__(self, other_fleet_succeeds=False, unreachable_nodes=None):
        self.other_fleet_succeeds = other_fleet_succeeds
        self._unreachable_event_nodes = set(unreachable_nodes or ())
        self._solved_map_event = set()
        self.force_move_calls = 0

    def _goto_akashi_with_other_fleets(self, drop=None):
        if self.other_fleet_succeeds:
            self._solved_map_event.add('is_akashi')
        return self.other_fleet_succeeds

    def _execute_fixed_patrol_scan(self, ExecuteFixedPatrolScan=False, **kwargs):
        self.force_move_calls += 1


class TestRecoverUnreachableAkashi(unittest.TestCase):
    def test_other_fleet_succeeds_skips_force_move(self):
        """换队就买到了 -> 不再触发强制移动。"""
        stub = RecoveryStub(other_fleet_succeeds=True)
        self.assertTrue(OSMap._recover_unreachable_akashi(stub, None, 'B7'))
        self.assertEqual(stub.force_move_calls, 0)

    def test_all_fleets_fail_triggers_force_move_and_marks(self):
        """全队都到不了 -> 触发强制移动，并记下这一格避免重复重跑。"""
        stub = RecoveryStub(other_fleet_succeeds=False)
        self.assertFalse(OSMap._recover_unreachable_akashi(stub, None, 'B7'))
        self.assertEqual(stub.force_move_calls, 1)
        self.assertIn('B7', stub._unreachable_event_nodes)

    def test_second_call_on_same_node_is_skipped(self):
        """整图重扫的另一个摄像机视野再遇到同一格 -> 直接跳过。"""
        stub = RecoveryStub(other_fleet_succeeds=False)
        OSMap._recover_unreachable_akashi(stub, None, 'B7')
        self.assertFalse(OSMap._recover_unreachable_akashi(stub, None, 'B7'))
        self.assertEqual(stub.force_move_calls, 1)

    def test_other_node_is_still_tried(self):
        """同一轮里另一个格子的事件照常处理。"""
        stub = RecoveryStub(other_fleet_succeeds=False, unreachable_nodes=['B7'])
        self.assertFalse(OSMap._recover_unreachable_akashi(stub, None, 'C3'))
        self.assertEqual(stub.force_move_calls, 1)


class RescanOnceStub:
    """只提供 `map_rescan_once` 需要的属性。"""

    def __init__(self):
        self._unreachable_event_nodes = {'B7'}

    def map_data_init(self, map_=None):
        pass

    def handle_info_bar(self):
        pass

    def update(self):
        pass

    def map_rescan_current(self, drop=None):
        return True


class TestUnreachableNodesReset(unittest.TestCase):
    def test_new_rescan_pass_gives_every_event_another_chance(self):
        """新一轮重扫要清空“到不了”记录，否则上一轮判定会一直挡着。"""
        stub = RescanOnceStub()
        OSMap.map_rescan_once(stub, rescan_mode='full')
        self.assertEqual(stub._unreachable_event_nodes, set())


class DeviceStub:
    """只提供 `_goto_scanning_device_with_other_fleets` 需要的属性。"""

    def __init__(self, view_finds_device, radar_finds_device, confirm_on_fleet=None):
        self.current = 1
        self.view_finds_device = view_finds_device
        self.radar_finds_device = radar_finds_device
        # 指定“切到第几支舰队时装置对话被触发”；None 表示全都触发不了
        self.confirm_on_fleet = confirm_on_fleet
        self.last_fleet = None
        self.config = SimpleNamespace(temporary=lambda **kwargs: nullcontext())
        self.fleet_selector = SimpleNamespace(get=lambda: self.current)
        self.device = SimpleNamespace(screenshot=lambda: None, click=lambda grid: None)
        self.is_siren_device_confirmed = False
        self.fleet_sets = []

    def fleet_set(self, index=1):
        self.fleet_sets.append(index)
        self.last_fleet = index
        return True

    def update_os(self):
        pass

    @property
    def view(self):
        return SimpleNamespace(
            predict=lambda: None,
            select=lambda **kwargs: (
                SelectedStub([make_device_grid()]) if self.view_finds_device else SelectedStub([])
            ),
        )

    def _radar_question_to_local(self):
        return make_device_grid() if self.radar_finds_device else None

    def wait_until_walk_stable(self, **kwargs):
        if self.confirm_on_fleet is not None and self.last_fleet == self.confirm_on_fleet:
            self.is_siren_device_confirmed = True
        return ''


class SelectedStub(list):
    @property
    def count(self):
        return len(self)


def make_device_grid():
    return SimpleNamespace(is_scanning_device=True)


class TestDeviceOtherFleets(unittest.TestCase):
    def run_goto(self, stub):
        result = OSMap._goto_scanning_device_with_other_fleets(stub)
        return stub, result

    def test_view_detection_confirms_device(self):
        """视野里看得到装置 -> 直接用视野定位。"""
        stub = DeviceStub(True, False, confirm_on_fleet=2)
        self.run_goto(stub)
        self.assertTrue(stub.is_siren_device_confirmed)

    def test_radar_fallback_when_view_misses_device(self):
        """视野识别不到装置（图标被舰队模型挡住）-> 回退用雷达问号，仍然点到。"""
        stub = DeviceStub(False, True, confirm_on_fleet=2)
        _, result = self.run_goto(stub)
        self.assertTrue(result)
        self.assertTrue(stub.is_siren_device_confirmed)

    def test_gives_up_when_neither_view_nor_radar_finds_it(self):
        """视野和雷达都没有装置 -> 全部跳过，返回 False。"""
        stub = DeviceStub(False, False)
        _, result = self.run_goto(stub)
        self.assertFalse(result)

    def test_restores_original_fleet(self):
        """无论成败都恢复原舰队。"""
        stub = DeviceStub(False, False)
        self.run_goto(stub)
        self.assertEqual(stub.fleet_sets[-1], 1)


if __name__ == '__main__':
    unittest.main()
