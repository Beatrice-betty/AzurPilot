"""奖励识别、委托配对与掉落入库可完全离线运行。"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pytest

from module.statistics.commission_drop import parse_commissions, resolve_name, revise_identity


def test_rewards_are_written_locally_without_reading_or_retrying_old_uploads(tmp_path):
    from module.persistence.database import get_database, use_database
    from module.statistics.azurstats import AzurStats
    from module.statistics.cl1_database import db
    from module.statistics.commission_drop import record_commission_drops
    from module.statistics.research_drop import ResearchDrop
    from module.statistics.reward_templates import local_drop_directory

    store = get_database(tmp_path / 'config')
    config = SimpleNamespace(config_name='offline', DropRecord_CloudUpload=True)
    parser = Mock()
    grid = parser.stats._target_grid.return_value

    def parse_without_cache(images):
        unknown, source = grid.item_resolver(images[0][:96, :96], '1')
        assert unknown.startswith('U?') and unknown == source
        return ResearchDrop(project='D-737-MI', series=8, items={'Coins': 3, unknown: 2})

    parser.parse.side_effect = parse_without_cache
    frame = np.zeros((720, 1280, 3), np.uint8)
    rows = [('done', 'Daily Resource Extraction Ⅰ'),
            ('reward', [{'item': 'Coins', 'amount': 2, 'tag': ''}])]
    with (
        use_database(store),
        patch('module.statistics.research_drop.get_parser', return_value=parser),
        patch('module.statistics.commission_drop.CommissionReader', return_value=Reader()),
        patch('module.statistics.azurstats.cleanup_drop_screenshots_if_due'),
        patch('module.base.http.Session', side_effect=AssertionError('Unexpected HTTP session')) as session,
        patch('requests.sessions.Session.request', side_effect=AssertionError('Unexpected HTTP request')) as request,
    ):
        root = local_drop_directory('offline')
        pending = root / 'screenshots' / 'old.json'
        pending.parent.mkdir(parents=True)
        pending.write_bytes(b'old-queued-image')
        stats = AzurStats(config)
        with stats.new('research', method='do_not') as drop:
            drop.add(frame)
        now = datetime.now().astimezone()
        record_commission_drops(rows, config, collected_at=now.isoformat())
        month = db.get_stats('offline', now.strftime('%Y-%m'))

    items = month['research_drop_entries'][0]['items']
    assert items['Coins'] == 3
    assert [amount for item, amount in items.items() if item.startswith('U?')] == [2]
    assert grid.item_resolver is None
    assert month['cloud_commission_entries'][0]['items'] == {'Coins': 2}
    assert pending.read_bytes() == b'old-queued-image'
    assert not (root / 'jobs').exists()
    session.assert_not_called()
    request.assert_not_called()


def test_commission_names_and_reward_disambiguation():
    assert resolve_name(' DAILY RESOURCE EXTRACTION Ⅰ ', 'en')['code'] == 'Daily Resource Extraction Ⅰ'
    assert resolve_name('BIW巡视护卫', 'cn_ios')['code'] == 'BIW Patrol Escort'
    with pytest.raises(ValueError):
        resolve_name('nonsense', 'cn')
    def item(name, amount):
        return [{'item': name, 'amount': amount}]
    assert revise_identity('Small Merchant Escort', item('Coins', 300)) == 'Small Merchant Escort N'
    assert revise_identity('BIW VIP Escort', item('Gems', 60)) == 'BIW Patrol Escort'
    assert revise_identity('Cargo Defense Ⅱ', item('Coins', 60)) == 'Cargo Defense Ⅰ'


class Reader:
    classify = staticmethod(lambda frame: frame[0])
    name = staticmethod(lambda frame: {'code': frame[1], 'name': frame[1]})
    items = staticmethod(lambda frame: frame[1])


def test_commission_pairing_placeholders_and_invalid_groups():
    items = [{'item': 'U-1234', 'source_template_id': 'U-1234', 'amount': 2, 'tag': 'bonus'}]
    events, errors = parse_commissions([
        ('done', 'Small Merchant Escort'), ('reward', items), ('exp', None),
        ('perfect', 'Cargo Defense Ⅰ'), ('ship', None),
        ('done', 'Broken'), ('reward', items), ('reward', items), ('exit', None),
        ('reward', items)], Reader())
    assert len(events) == 2
    assert events[0]['items'] == [{'item': 'U-1234', 'amount': 2, 'tag': 'bonus'}]
    assert events[1]['status'] == 1
    assert events[1]['items'][0]['item'] == 'PlaceHolder'
    assert errors == ['multiple_reward_pages', 'reward_without_commission']


def test_scrolled_research_reward_boundary_is_after_both_frames():
    from module.research.ui import ResearchUI
    from module.combat.assets import GET_ITEMS_3
    drop = SimpleNamespace(count=1, images=[], add=lambda image: drop.images.append(image))
    drop.finish_research_reward = Mock()
    device = Mock(image=np.zeros((720, 1280, 3), np.uint8))
    target = SimpleNamespace(device=device, get_items=lambda: GET_ITEMS_3)
    ResearchUI.drop_record(target, drop)
    assert len(drop.images) == 2
    drop.finish_research_reward.assert_called_once_with(1)


def test_local_identity_hook_keeps_canonical_suffix_and_does_not_change_other_grids():
    from module.base.button import ButtonGrid
    from module.statistics.item import ItemGrid
    grids = ButtonGrid(origin=(0, 0), delta=(0, 0), button_shape=(96, 96), grid_shape=(1, 1))
    image = np.full((96, 96, 3), 180, np.uint8)
    ordinary = ItemGrid(grids, {})
    ordinary.amount_ocr = Mock(ocr_batch_with_validation=Mock(return_value=[2]))
    assert ordinary.predict(image)[0].name.isdigit()
    reward_grid = ItemGrid(grids, {})
    reward_grid.amount_ocr = ordinary.amount_ocr
    reward_grid.item_resolver = Mock(return_value=('Blueprint_2', 'U-ORIGINAL'))
    item = reward_grid.predict(image)[0]
    assert item.name == 'Blueprint_2'
    assert item.source_template_id == 'U-ORIGINAL'
    assert item.amount == 2


def test_commission_frame_limit_does_not_save_an_incomplete_record():
    from module.statistics.azurstats import AzurStats
    config = SimpleNamespace(config_name='test')
    drop = AzurStats(config).new('commission', method='record')
    with patch('module.statistics.commission_drop.classify_frame', side_effect=['done', 'reward'] * 41):
        for index in range(82):
            drop.add(np.zeros((720, 1280, 3), np.uint8))
    assert len(drop.images) == 80
    assert not drop.commission_capture_valid
    with patch('module.statistics.commission_drop.record_commission_drops') as record:
        drop.__exit__(None, None, None)
    record.assert_not_called()


def test_different_consecutive_reward_pages_reach_pairing_validation():
    from module.statistics.azurstats import AzurStats
    config = SimpleNamespace(config_name='test')
    drop = AzurStats(config).new('commission', method='record')
    first = np.zeros((720, 1280, 3), np.uint8)
    second = np.ones_like(first)
    with patch('module.statistics.commission_drop.classify_frame', side_effect=['done', 'reward', 'reward', 'reward']):
        drop.add(first)
        drop.add(first)
        drop.add(first)  # An exact repeated reward capture is ignored.
        drop.add(second)  # A distinct page must be validated rather than silently discarded.
    assert len(drop.images) == 3
    assert np.array_equal(drop.images[-1], second)


def test_collectors_keep_observed_tags_and_original_identity():
    from module.statistics.reward_templates import summarize_items
    from module.statistics.commission_drop import CommissionReader
    from module.statistics.research_drop import ResearchDropParser
    item = SimpleNamespace(name='Blueprint_2', source_template_id='U-ORIGINAL',
                           tag='catchup', amount=2, is_known_item=lambda: True)
    reader = object.__new__(CommissionReader)
    reader.stats = Mock()
    reader.stats.stats_get_items.return_value = [item]
    commission = reader.items(None)
    parser = object.__new__(ResearchDropParser)
    parser.stats = reader.stats
    with patch('module.research.assets.QUEUE_CHECK.match', return_value=False), \
            patch('module.combat.assets.GET_ITEMS_3.match', return_value=False):
        research = parser.parse([np.zeros((720, 1280, 3), np.uint8)]).source_items
    for source in [commission, research]:
        assert source[0]['source_template_id'] == 'U-ORIGINAL'
        assert summarize_items(source) == [{'item': 'Blueprint_2', 'amount': 2, 'tag': 'catchup'}]
