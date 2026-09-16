"""验证战后强制移动的“漏猫”兜底。

背景：明石刷新在舰队模型旁边时图标会被挡住，或目标点超出舰队移动范围
（游戏提示“目标点超出移动范围”，即 `handle_walk_out_of_step` 抓的
`TEMPLATE_MAP_WALK_OUT_OF_STEP`）。这时「只换队看雷达、一支都不挪动」的
零移动检索永远点不到猫——只有把挡路的舰队挪开才能解决。

所以强制移动是一个开关：开启后先零移动遍历 1~4 队雷达（L0/L1），什么都没
找到、且行动力大于 7 时，再逐队挪动舰队做一遍整图重扫（L2）。行动力不够
就不挪，留给下一轮正常练级。这里的用例锁定这几条边界。
"""

import unittest
from contextlib import nullcontext
from types import SimpleNamespace

from module.config.redirect_utils.utils import execute_fixed_patrol_scan_redirect
from module.os.map import ALREADY_SOLVED_MAP_EVENTS, OSMap


class TestForcedMoveRedirect(unittest.TestCase):
    def test_stored_levels_are_cleaned_into_switch(self):
        """存量配置里的等级数字要被清洗成布尔，别在复选框里留个 2。"""
        self.assertIs(execute_fixed_patrol_scan_redirect(0), False)
        for level in (1, 2, 3):
            with self.subTest(level=level):
                self.assertIs(execute_fixed_patrol_scan_redirect(level), True)

    def test_bool_values_pass_through(self):
        self.assertIs(execute_fixed_patrol_scan_redirect(True), True)
        self.assertIs(execute_fixed_patrol_scan_redirect(False), False)


class SwitchStub:
    """只提供 `_forced_move_enabled` 需要的属性。"""

    def __init__(self, value):
        self.config = SimpleNamespace(
            OpsiHazard1Leveling_ExecuteFixedPatrolScan=value
        )


class TestForcedMoveSwitch(unittest.TestCase):
    def enabled(self, value):
        return OSMap._forced_move_enabled(SwitchStub(value))

    def test_bool_values(self):
        """新配置是复选框，直接读布尔值。"""
        self.assertTrue(self.enabled(True))
        self.assertFalse(self.enabled(False))
        self.assertFalse(self.enabled(None))

    def test_legacy_level_values(self):
        """旧版等级配置：0=关闭，1=效率模式，2=保守模式（已并入 -> 同样视为开启）。"""
        self.assertFalse(self.enabled(0))
        for level in (1, 2, 3):
            with self.subTest(level=level):
                self.assertTrue(self.enabled(level))

    def test_string_values(self):
        """配置文件里读出来的字符串也要认。"""
        for value in ('true', 'TRUE', '1', '2'):
            with self.subTest(value=value):
                self.assertTrue(self.enabled(value))
        for value in ('false', 'False', '0', ''):
            with self.subTest(value=value):
                self.assertFalse(self.enabled(value))


class FixedPatrolStub:
    """只提供 `_execute_fixed_patrol_scan` 需要的属性。"""

    # 阈值取真实实现，避免测试和代码各写一份
    _FIXED_PATROL_L2_AP = OSMap._FIXED_PATROL_L2_AP

    def __init__(self, enabled=True, any_fleet_result=False, current_ap=0):
        self.config = SimpleNamespace(OpsiFleet_Fleet=1)
        self.map = SimpleNamespace(grids=[object()])
        self.enabled = enabled
        self.any_fleet_result = any_fleet_result
        self.current_ap = current_ap
        self.ap_reads = 0
        self.move_calls = 0
        self.fleet_sets = []

    def map_init(self, map_=None):
        pass

    def _forced_move_enabled(self):
        return self.enabled

    def clear_question_any_fleet(self, drop=None):
        return self.any_fleet_result

    def _read_current_action_point(self):
        self.ap_reads += 1
        return self.current_ap

    def _move_fleets_and_rescan(self):
        self.move_calls += 1
        return False

    def fleet_set(self, index=1):
        self.fleet_sets.append(index)
        return True


class TestFixedPatrolScan(unittest.TestCase):
    def run_scan(self, **kwargs):
        stub = FixedPatrolStub(**kwargs)
        OSMap._execute_fixed_patrol_scan(stub, ExecuteFixedPatrolScan=True)
        return stub

    def test_skips_when_not_requested(self):
        """调用方没要求强制移动 -> 什么都不做。"""
        stub = FixedPatrolStub()
        OSMap._execute_fixed_patrol_scan(stub, ExecuteFixedPatrolScan=False)
        self.assertEqual(stub.move_calls, 0)
        self.assertEqual(stub.fleet_sets, [])

    def test_skips_when_switch_is_off(self):
        """开关关闭 -> 什么都不做，连行动力都不去查。"""
        stub = self.run_scan(enabled=False)
        self.assertEqual(stub.move_calls, 0)
        self.assertEqual(stub.ap_reads, 0)
        self.assertEqual(stub.fleet_sets, [])

    def test_solved_during_zero_move_scan_skips_l2(self):
        """零移动检索就找到了事件 -> 直接结束，不查行动力也不挪舰队。"""
        stub = self.run_scan(any_fleet_result=True)
        self.assertEqual(stub.move_calls, 0)
        self.assertEqual(stub.ap_reads, 0)

    def test_moves_fleets_when_action_point_enough(self):
        """什么都没找到但当前行动力大于 7 -> 走一遍 L2 挪舰队。"""
        stub = self.run_scan(any_fleet_result=False, current_ap=8)
        self.assertEqual(stub.ap_reads, 1)
        self.assertEqual(stub.move_calls, 1)

    def test_keeps_farming_when_action_point_low(self):
        """当前行动力不够 -> 不挪舰队，留给下一轮正常练级。"""
        for current_ap in (0, 5, 7):
            with self.subTest(current_ap=current_ap):
                stub = self.run_scan(any_fleet_result=False, current_ap=current_ap)
                self.assertEqual(stub.ap_reads, 1)
                self.assertEqual(stub.move_calls, 0)

    def test_main_fleet_restored_after_scan(self):
        """无论走哪条分支，结束后都要复位主队。"""
        for any_fleet_result, current_ap in ((True, 0), (False, 30), (False, 0)):
            with self.subTest(any_fleet_result=any_fleet_result, current_ap=current_ap):
                stub = self.run_scan(
                    any_fleet_result=any_fleet_result, current_ap=current_ap
                )
                self.assertEqual(stub.fleet_sets, [1])

    def test_nested_call_is_skipped(self):
        """已经在强制移动流程里 -> 跳过嵌套调用，避免重复挪舰队。"""
        stub = FixedPatrolStub(current_ap=30)
        stub._in_akashi_recovery = True
        OSMap._execute_fixed_patrol_scan(stub, ExecuteFixedPatrolScan=True)
        self.assertEqual(stub.move_calls, 0)
        self.assertEqual(stub.fleet_sets, [])

    def test_action_point_threshold_is_seven(self):
        """阈值就是 7：大于 7 才挪，等于 7 不挪。"""
        self.assertEqual(OSMap._FIXED_PATROL_L2_AP, 7)
        stub = self.run_scan(any_fleet_result=False, current_ap=7)
        self.assertEqual(stub.move_calls, 0)


def make_question_grid(is_logging_tower=False):
    return SimpleNamespace(is_logging_tower=is_logging_tower)


class ClearQuestionStub:
    """只提供 `clear_question` 需要的属性。"""

    def __init__(self, predictions, walk_result=''):
        self.predictions = list(predictions)
        self.walk_result = walk_result
        self.config = SimpleNamespace(temporary=lambda **kwargs: nullcontext())
        self.zone = SimpleNamespace(is_port=False)
        self.clicked = []
        self.device = SimpleNamespace(image=object(), click=self.clicked.append)
        self.view = SimpleNamespace(
            select=lambda **kwargs: SimpleNamespace(count=1),
            predict=lambda: None,
            show=lambda: None,
        )
        self.radar = SimpleNamespace(predict_question=self.predict_question)
        self.is_siren_device_confirmed = False
        self._solved_map_event = set()

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


class TestClearQuestion(unittest.TestCase):
    def run_clear_question(self, predictions, walk_result=''):
        stub = ClearQuestionStub(predictions, walk_result)
        result = OSMap.clear_question(stub)
        return stub, result

    def test_retries_every_attempt_before_giving_up(self):
        """三次点不掉 -> 返回 False；不可达的问号由上层换队/挪舰队处理。"""
        grid = make_question_grid()
        stub, result = self.run_clear_question([grid, grid, grid])
        self.assertFalse(result)
        self.assertEqual(len(stub.clicked), 3)

    def test_returns_false_when_radar_has_no_question(self):
        """雷达上没有问号 -> 不点，直接返回。"""
        stub, result = self.run_clear_question([None])
        self.assertFalse(result)
        self.assertEqual(stub.clicked, [])

    def test_returns_true_when_akashi_reached(self):
        """点到明石 -> True。"""
        stub, result = self.run_clear_question([make_question_grid()], walk_result='akashi')
        self.assertTrue(result)


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
        self.fleet_sets = []

    def predict_question(self, image, in_port=True):
        return self.radar_results.pop(0) if self.radar_results else None

    def fleet_set(self, index=1):
        self.fleet_sets.append(index)
        return True

    def clear_question(self, drop=None):
        # 真实实现只有雷达上看到问号时才会被调用
        if self.solve_on_fleet is not None and self.fleet_sets[-1] == self.solve_on_fleet:
            self._solved_map_event.add('is_akashi')
            return True
        return False

    def map_rescan_once(self, rescan_mode='full', drop=None):
        return False


class TestAnyFleetFleetOrder(unittest.TestCase):
    def run_any_fleet(self, stub):
        return OSMap.clear_question_any_fleet(stub)

    def test_starts_from_primary_then_others(self):
        """先主队，再按编号补上其余舰队，且全程不切回主队。"""
        stub = AnyFleetStub([None, None, None, None])
        self.run_any_fleet(stub)
        self.assertEqual(stub.fleet_sets, [1, 2, 3, 4])

    def test_returns_false_when_nothing_seen(self):
        """雷达上什么都没有 -> 返回 False，交给上层决定要不要挪舰队。"""
        stub = AnyFleetStub([None, None, None, None])
        self.assertFalse(self.run_any_fleet(stub))

    def test_other_fleet_can_solve_the_question(self):
        """主队清不掉、第 3 舰队清掉了 -> 立即结束并标记已解决。"""
        stub = AnyFleetStub(
            [make_question_grid(), make_question_grid(), make_question_grid(), None],
            solve_on_fleet=3,
        )
        result = self.run_any_fleet(stub)
        self.assertTrue(result)
        self.assertEqual(stub.fleet_sets, [1, 2, 3])


class MarkStub:
    """只提供 `_mark_event_unreachable` 需要的属性。"""

    _mark_event_unreachable = OSMap._mark_event_unreachable

    def __init__(self, nodes=None):
        self._unreachable_event_nodes = set(nodes or ())


class RecoveryStub(MarkStub):
    """只提供 `_recover_unreachable_akashi` 需要的属性。"""

    def __init__(self, other_fleet_succeeds=False, unreachable_nodes=None):
        super().__init__(unreachable_nodes)
        self.other_fleet_succeeds = other_fleet_succeeds
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

    def test_marking_does_not_touch_shared_default(self):
        """标记不能写进类属性上的默认 set（那是所有实例共享的）。"""
        first = MarkStub()
        second = MarkStub()
        OSMap._mark_event_unreachable(first, 'B7')
        self.assertEqual(first._unreachable_event_nodes, {'B7'})
        self.assertEqual(second._unreachable_event_nodes, set())
        self.assertEqual(OSMap._unreachable_event_nodes, set())


class RescanOnceStub(MarkStub):
    """只提供 `map_rescan_once` 需要的属性。"""

    def __init__(self):
        super().__init__({'B7'})

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


def make_device_grid():
    return SimpleNamespace(is_scanning_device=True)


class SelectedStub(list):
    @property
    def count(self):
        return len(self)


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
                SelectedStub([make_device_grid()])
                if self.view_finds_device
                else SelectedStub([])
            ),
        )

    def _radar_question_to_local(self):
        return make_device_grid() if self.radar_finds_device else None

    def wait_until_walk_stable(self, **kwargs):
        if self.confirm_on_fleet is not None and self.last_fleet == self.confirm_on_fleet:
            self.is_siren_device_confirmed = True
        return ''


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
