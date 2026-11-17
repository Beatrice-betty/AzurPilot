"""用真实内存配置回归 #1503 的出击交接与资源边界。"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import pytest

from module.config.config import AzurLaneConfig, TaskEnd, name_to_function
from module.config.schedule_time import schedule_utc
from module.config.utils import DEFAULT_TIME, write_file
from module.handover.handover import HandoverOil, OperationHandover
from module.handover.planning import daily_runs, fit_sortie_window, next_sortie
from module.map.assets import (HANDOVER_OIL_COST_OCR, HANDOVER_PASS_CLICK,
                               HANDOVER_REWARD_CHECK, HANDOVER_STOP_CHECK)
from module.map.map_operation import MapOperation


NOW = datetime(2026, 11, 17, 12)


def memory_config(task='OperationHandover', **tasks):
    config = AzurLaneConfig('template')
    config.data = config.config_update(tasks)
    config.bind(task)
    config.task = name_to_function(task)
    return config


@pytest.mark.parametrize('task', ['Main', 'Event', 'WarArchives'])
def test_waiting_sortie_gets_its_turn_after_commission_ends(task):
    deadline = NOW + timedelta(minutes=1)
    config = memory_config(**{task: {'Scheduler': {'Enable': True, 'NextRun': deadline}}})
    runner = OperationHandover(config=config, device=Mock())
    runner.handover_enter = Mock()
    runner.handover_reward = Mock(return_value=True)
    runner.appear = Mock(return_value=False)
    runner.handover_panel_enter = Mock()
    with patch('module.handover.handover.current_time', return_value=NOW):
        runner.run()
    runner.handover_panel_enter.assert_not_called()
    assert config.modified['OperationHandover.Scheduler.NextRun'] == schedule_utc(NOW + timedelta(minutes=2))
    assert config.data[task]['Scheduler']['NextRun'] == deadline


def test_saved_sortie_deadline_works_with_real_config_and_legacy_immediate_task(tmp_path, monkeypatch):
    path = tmp_path / 'handover.json'
    deadline = NOW + timedelta(minutes=1)
    write_file(str(path), {'Main': {'Scheduler': {'Enable': True, 'NextRun': str(deadline)}}})
    monkeypatch.setattr('module.config.config.filepath_config', lambda *args: str(path))
    monkeypatch.setattr('module.config.config_updater.filepath_config', lambda *args: str(path))
    monkeypatch.setattr('module.config.config.current_time', lambda: NOW)
    monkeypatch.setattr('module.handover.handover.current_time', lambda: NOW)

    config = AzurLaneConfig('handover', task='OperationHandover')
    assert config.data['Main']['Scheduler']['NextRun'] == schedule_utc(deadline)
    assert config.OperationHandover_DailyRunCount == 0
    assert next_sortie(config) == ('Main', schedule_utc(deadline))

    runner = ready_handover(config)
    runner.run()
    runner.handover_panel_enter.assert_not_called()
    config.load()
    assert config.cross_get('OperationHandover.Scheduler.NextRun') == NOW + timedelta(minutes=2)
    assert config.cross_get('Main.Scheduler.NextRun') == deadline

    config.data['Event']['Scheduler'].update(Enable=True, NextRun=DEFAULT_TIME)
    assert next_sortie(config) == ('Event', schedule_utc(DEFAULT_TIME))


def test_disabled_sorties_do_not_prevent_idle_commissions():
    config = memory_config(Main={'Scheduler': {'Enable': False, 'NextRun': NOW}})
    assert next_sortie(config) is None


@pytest.mark.parametrize('deadline,expected', [(15, 0), (90, 1), (150, 2), (630, 10)])
def test_batch_fits_next_sortie_without_overrunning_it(deadline, expected):
    target = schedule_utc(NOW + timedelta(seconds=deadline))
    assert fit_sortie_window(10, timedelta(minutes=10), target, NOW) == expected


@pytest.mark.parametrize('task', ['Main', 'Event', 'WarArchives'])
def test_running_commission_delays_sortie_using_live_time_and_real_config(task):
    config = memory_config(task=task)
    context = SimpleNamespace(
        config=config, device=Mock(), handover_conflict_appear=lambda: True,
        appear=lambda button, **kwargs: button is HANDOVER_STOP_CHECK,
        handover_close_conflict=Mock(return_value=True),
    )
    context.handover_conflict_delay = lambda **kwargs: MapOperation.handover_conflict_delay(context, **kwargs)
    reader = SimpleNamespace(last_valid=True, ocr=lambda image: timedelta(minutes=3))
    with patch('module.map.map_operation.current_time', return_value=NOW), \
            patch('module.handover.handover.OCR_HANDOVER_STOP_TIME', reader):
        with pytest.raises(TaskEnd):
            MapOperation.handle_handover_conflict(context)
    assert config.modified['OperationHandover.OperationHandover.CommissionEnd'] == NOW + timedelta(minutes=3)
    assert config.modified[f'{task}.Scheduler.NextRun'] == schedule_utc(NOW + timedelta(minutes=4))


def test_invalid_duration_does_not_overwrite_a_known_end_time():
    end = NOW + timedelta(hours=1)
    config = memory_config(task='Main', OperationHandover={'OperationHandover': {'CommissionEnd': end}})
    context = SimpleNamespace(
        config=config, device=Mock(), handover_conflict_appear=lambda: True,
        appear=lambda button, **kwargs: button is HANDOVER_STOP_CHECK,
        handover_close_conflict=Mock(return_value=True),
    )
    context.handover_conflict_delay = lambda **kwargs: MapOperation.handover_conflict_delay(context, **kwargs)
    reader = SimpleNamespace(last_valid=False, ocr=lambda image: timedelta())
    with patch('module.map.map_operation.current_time', return_value=NOW), \
            patch('module.handover.handover.OCR_HANDOVER_STOP_TIME', reader), pytest.raises(TaskEnd):
        MapOperation.handle_handover_conflict(context)
    assert 'OperationHandover.OperationHandover.CommissionEnd' not in config.modified
    assert config.modified['Main.Scheduler.NextRun'] == schedule_utc(end + timedelta(minutes=1))


@pytest.mark.parametrize('cost,expected', [(None, None), (0, True), (99, True), (101, False)])
def test_free_and_unrecognized_costs_have_different_outcomes(cost, expected):
    context = SimpleNamespace(handover_oil_cost=lambda: cost)
    assert OperationHandover.handover_oil_enough(context, 100) is expected


def test_requested_loot_does_not_silently_start_without_orders():
    context = SimpleNamespace(handover_click_until_stable=lambda *args: 0)
    assert not OperationHandover.handover_book_max(context)


def test_exhausted_quota_never_enters_an_unreachable_zero_count():
    context = SimpleNamespace(handover_count_max=lambda: 0, handover_input_count=Mock())
    assert not OperationHandover.handover_set_count(context, 5)
    context.handover_input_count.assert_not_called()


def test_count_recognition_failure_has_a_bounded_exit_without_repeated_clicks():
    state = {'time': 100}

    def screenshot():
        state['time'] += 1

    context = SimpleNamespace(
        device=SimpleNamespace(screenshot=screenshot, image=None), appear=lambda *args, **kwargs: True,
        appear_then_click=Mock(),
    )
    reader = SimpleNamespace(last_valid=False, ocr=lambda image: 0)
    with patch('module.base.timer.time', side_effect=lambda: state['time']):
        assert OperationHandover.handover_click_until_stable(context, object(), reader, '测试次数') == -1
    context.appear_then_click.assert_not_called()


@pytest.mark.parametrize('offset,expected', [(-1, 0), (0, 2), (1, 0)])
def test_daily_batch_counter_ignores_previous_days_and_future_records(offset, expected):
    config = SimpleNamespace(OperationHandover_DailyRunCountRecord=schedule_utc(NOW + timedelta(days=offset)),
                             OperationHandover_DailyRunCountUsed=2)
    assert daily_runs(config, NOW.replace(hour=0), NOW) == expected


def test_reaching_daily_batch_limit_still_claims_before_deferring_new_batches():
    config = memory_config(OperationHandover={'OperationHandover': {
        'DailyRunCount': 1, 'DailyRunCountUsed': 1, 'DailyRunCountRecord': NOW,
    }})
    runner = OperationHandover(config=config, device=Mock())
    runner.handover_enter = Mock()
    runner.handover_reward = Mock(return_value=True)
    runner.appear = Mock(return_value=False)
    runner.handover_panel_enter = Mock()
    with patch('module.handover.handover.current_time', return_value=NOW), \
            patch('module.handover.handover.get_server_last_update', return_value=NOW.replace(hour=0)):
        runner.run()
    runner.handover_reward.assert_called_once_with()
    runner.handover_panel_enter.assert_not_called()
    assert config.modified['OperationHandover.Scheduler.NextRun'] > schedule_utc(NOW)


def ready_handover(config):
    runner = OperationHandover(config=config, device=Mock())
    for name in ('handover_enter', 'handover_panel_enter', 'handover_close_panel', 'handover_delay'):
        setattr(runner, name, Mock())
    runner.appear = Mock(return_value=False)
    runner.handover_reward = Mock(return_value=False)
    runner.get_oil = Mock(return_value=5000)
    runner.handover_check_support = Mock(return_value=True)
    runner.handover_set_count = Mock(return_value=True)
    runner.handover_oil_enough = Mock(return_value=True)
    runner.handover_get_time = Mock(return_value=(timedelta(minutes=1), timedelta(hours=8)))
    return runner


@pytest.fixture
def valid_panel(monkeypatch):
    reader = SimpleNamespace(last_valid=True, ocr=lambda image: 1)
    for name in ('OCR_HANDOVER_COUNT', 'OCR_HANDOVER_TIME_NEEDED', 'OCR_HANDOVER_TIME_REMAINING'):
        monkeypatch.setattr(f'module.handover.handover.{name}', reader)


@pytest.mark.parametrize('started,rollover,expected', [(False, False, 0), (True, False, 3), (True, True, 1)])
def test_batch_counter_only_records_confirmed_starts(started, rollover, expected, valid_panel, monkeypatch):
    before_start = NOW.replace(hour=23, minute=59)
    started_at = before_start + timedelta(minutes=2 if rollover else 0)
    clock = before_start
    config = memory_config(OperationHandover={'OperationHandover': {
        'DailyRunCountUsed': 2, 'DailyRunCountRecord': before_start,
    }})
    runner = ready_handover(config)

    def start():
        nonlocal clock
        clock = started_at
        return started

    runner.handover_start = start
    monkeypatch.setattr('module.handover.handover.current_time', lambda: clock)
    monkeypatch.setattr('module.handover.handover.get_server_last_update',
                        lambda trigger: clock.replace(hour=0, minute=0))
    runner.run()

    prefix = 'OperationHandover.OperationHandover'
    assert config.modified.get(f'{prefix}.DailyRunCountUsed', 0) == expected
    assert config.modified.get(f'{prefix}.DailyRunCountRecord') == (schedule_utc(started_at) if started else None)


@pytest.mark.parametrize('books,oil_enough,give_up', [(0, True, True), (1, False, True), (1, None, False)])
def test_weekly_orders_only_end_on_confirmed_shortage(books, oil_enough, give_up, valid_panel, monkeypatch):
    config = memory_config()
    runner = ready_handover(config)
    runner.handover_consume_all_book_state = Mock(return_value=(True, '触发'))
    runner.handover_consume_all_book = Mock(return_value=books)
    runner.handover_oil_enough = Mock(return_value=oil_enough)
    runner.handover_idle_delay = Mock()
    runner.handover_start = Mock()
    monkeypatch.setattr('module.handover.handover.current_time', lambda: NOW)
    runner.run()

    record = config.modified.get('OperationHandover.OperationHandover.ConsumeAllBookRecord')
    assert record == (NOW if give_up else None)
    assert runner.handover_delay.called is (not give_up)
    runner.handover_close_panel.assert_called_once()
    runner.handover_start.assert_not_called()


@pytest.mark.parametrize('red', [False, True])
def test_real_cost_image_excludes_oil_icon_and_chinese_unit_without_loading_ocr(red):
    image = cv2.cvtColor(cv2.imread(HANDOVER_OIL_COST_OCR.file), cv2.COLOR_BGR2RGB)
    x1, y1, x2, y2 = HANDOVER_OIL_COST_OCR.area
    crop = image[y1:y2, x1:x2]
    if red:
        crop = crop[:, :, [1, 0, 2]]
    numeric = HandoverOil.pre_process(None, crop)
    _, _, stats, _ = cv2.connectedComponentsWithStats(255 - numeric)
    digits = [part for part in stats[1:] if part[3] >= 10]
    assert len(digits) == 3
    assert numeric.shape[1] < 40


@pytest.mark.parametrize('custom_page', [False, True])
def test_claim_waits_through_a_transient_chapter_before_delayed_ship_and_rewards(custom_page):
    state = {'page': '', 'time': 100}
    frames = iter(['complete', 'complete', 'chapter', 'overflow', 'ship', 'reward', 'chapter', 'chapter'])
    handled = []

    def screenshot():
        state.update(page=next(frames), time=state['time'] + 1)

    def popup():
        if state['page'] in ('ship', 'reward'):
            handled.append(state['page'])
            return True
        return False

    context = SimpleNamespace(
        device=SimpleNamespace(screenshot=screenshot),
        appear=lambda button, **kwargs: (
            button is HANDOVER_PASS_CLICK and state['page'] == 'complete'
            or button is HANDOVER_REWARD_CHECK and state['page'] == 'reward'),
        appear_then_click=lambda button, **kwargs: button is HANDOVER_PASS_CLICK and state['page'] == 'complete',
        handover_handle_popup=popup,
        handle_popup_confirm=lambda *args: state['page'] == 'overflow',
        campaign=SimpleNamespace(ENTRANCE=object(), appear=Mock(side_effect=lambda *args: state['page'] == 'chapter')),
    )
    page_ready = (lambda: state['page'] == 'chapter') if custom_page else None
    with patch('module.base.timer.time', side_effect=lambda: state['time']):
        assert OperationHandover.handover_reward(context, page_ready=page_ready)
    assert handled == ['ship', 'reward']
    assert context.campaign.appear.called is (not custom_page)


@pytest.mark.parametrize('use_book,oil,expected', [(True, 500, True), (True, 200, False), (False, 0, True)])
def test_quota_is_prepared_for_requested_count_before_the_game_clamps_it(use_book, oil, expected):
    context = SimpleNamespace(
        config=SimpleNamespace(data={}, task_delay=Mock()), device=Mock(),
        handover_get_time=lambda: (timedelta(minutes=1), timedelta(minutes=1)),
        handover_time_deficit=OperationHandover.handover_time_deficit,
        handover_book_max=Mock(return_value=True), handover_oil_cost=lambda: 100,
        handover_delay=Mock(), handover_exchange_time=Mock(return_value=True),
    )
    reader = SimpleNamespace(last_valid=True, ocr=lambda image: 1)
    with patch('module.handover.handover.OCR_HANDOVER_COUNT', reader), \
            patch('module.handover.handover.OCR_HANDOVER_BOOK_COUNT', reader), \
            patch('module.handover.handover.OCR_HANDOVER_TIME_NEEDED', SimpleNamespace(last_valid=True)), \
            patch('module.handover.handover.OCR_HANDOVER_TIME_REMAINING', SimpleNamespace(last_valid=True)):
        assert OperationHandover.handover_prepare_time(context, 3, oil, use_book=use_book) is expected
    if expected:
        context.handover_exchange_time.assert_called_once_with(timedelta(seconds=61), reserve_books=int(use_book))
    else:
        context.handover_exchange_time.assert_not_called()
