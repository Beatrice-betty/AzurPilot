"""本地掉落设置保留旧配置的截图和统计选择。"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from module.api.config_service import ConfigService
from module.config.config_generated import GeneratedConfig
from module.config.config_updater import ConfigUpdater
from module.config.redirect_utils.utils import OPSI_RECORD_ARGS
from module.statistics.azurstats import AzurStats
from tests.test_api import fixture


def migrate(record):
    return ConfigUpdater().config_update({'Alas': {'DropRecord': record}})['Alas']['DropRecord']


@pytest.mark.parametrize('old, research, enabled_mode, disabled_mode', [
    ('save', 'save', 'save_and_record', 'save_only'),
    ('upload', 'do_not', 'record', 'do_not'),
    ('save_and_upload', 'save', 'save_and_record', 'save_only'),
    ('do_not', 'do_not', 'do_not', 'do_not'),
])
@pytest.mark.parametrize('enabled', [False, True])
def test_old_modes_keep_screenshot_and_commission_collection(old, research, enabled_mode, disabled_mode, enabled):
    result = migrate({'ResearchRecord': old, 'CommissionRecord': old,
                      'CloudUpload': enabled, 'TelemetryReport': True})
    assert result['ResearchRecord'] == research
    assert result['CommissionRecord'] == (enabled_mode if enabled else disabled_mode)
    assert 'CloudUpload' not in result and 'TelemetryReport' not in result
    assert migrate(result) == result


def test_webui_and_runtime_migrate_old_options_without_writing_on_read(tmp_path):
    configs = ConfigService(fixture(str(tmp_path)))
    path = configs.path('testpilot')
    data = configs.read_json(path)
    old = {'ResearchRecord': 'save_and_upload', 'CommissionRecord': 'save_and_upload',
           'CloudUpload': False, 'TelemetryReport': True}
    data['Alas']['DropRecord'] = old
    original = json.dumps(data).encode()
    path.write_bytes(original)
    assert configs.read('testpilot')[0]['Alas']['DropRecord'] == migrate(old)
    assert path.read_bytes() == original


def test_missing_legacy_commission_mode_respects_the_old_master_switch():
    assert migrate({'CloudUpload': False})['CommissionRecord'] == 'do_not'


@pytest.mark.parametrize('old, expected', [
    ('save', 'save'), ('save_and_upload', 'save'), ('upload', 'do_not'), ('do_not', 'do_not'),
])
def test_local_modes_keep_screenshot_intent_and_migrate_old_shared_switch(old, expected):
    result = migrate({'OpsiRecord': old, 'CombatRecord': old, 'MeowfficerTalent': old})
    for arg in (*OPSI_RECORD_ARGS, 'CombatRecord', 'MeowfficerTalent'):
        assert result[arg] == expected
    assert 'OpsiRecord' not in result
    assert migrate(result) == result


def test_defaults_and_removed_settings_match_generated_schema():
    schema = ConfigUpdater().args['Alas']['DropRecord']
    template = json.loads(Path('config/template.json').read_text(encoding='utf-8'))['Alas']['DropRecord']
    removed = {'API', 'AzurStatsID', 'CloudUpload', 'TelemetryReport'}
    migrated = migrate({'API': 'obsolete', 'AzurStatsID': 'obsolete', 'TelemetryReport': True})
    assert removed.isdisjoint(migrated) and removed.isdisjoint(template)
    for arg, options, default in (
        ('ResearchRecord', ['save', 'do_not'], 'do_not'),
        ('CommissionRecord', ['record', 'save_and_record', 'save_only', 'do_not'], 'record'),
    ):
        assert schema[arg]['option'] == options
        assert schema[arg]['value'] == template[arg] == migrated[arg] == default
        assert getattr(GeneratedConfig, f'DropRecord_{arg}') == default
    for arg in (*OPSI_RECORD_ARGS, 'CombatRecord', 'MeowfficerBuy', 'MeowfficerTalent'):
        assert schema[arg]['option'] == ['save', 'do_not']
        assert schema[arg]['value'] == template[arg] == 'do_not'
    for path in Path('module/config/i18n').glob('*.json'):
        words = json.loads(path.read_text(encoding='utf-8'))['DropRecord']
        assert not removed.intersection(words)
        for arg in ('ResearchRecord', 'CommissionRecord', *OPSI_RECORD_ARGS):
            assert 'upload' not in words[arg] and 'save_and_upload' not in words[arg]


@pytest.mark.parametrize('scene, method, save, analyze', [
    ('research', 'save', True, True), ('research', 'do_not', False, True),
    ('commission', 'record', False, True), ('commission', 'save_and_record', True, True),
    ('commission', 'save_only', True, False), ('commission', 'do_not', False, False),
])
def test_local_collection_and_screenshot_choices_are_independent(scene, method, save, analyze):
    config = SimpleNamespace(config_name='isolated')
    with patch('module.statistics.azurstats.cleanup_drop_screenshots_if_due'):
        drop = AzurStats(config).new(scene, method=method)
    assert drop.save is save
    assert drop.analyze is analyze


def test_research_without_screenshot_save_still_commits_local_rewards():
    config = SimpleNamespace(config_name='isolated')
    with patch('module.statistics.azurstats.cleanup_drop_screenshots_if_due'), \
            patch('module.statistics.research_drop.record_research_drop') as parse:
        with AzurStats(config).new('research', method='do_not') as drop:
            drop.add(np.zeros((720, 1280, 3), np.uint8))
    parse.assert_called_once()
    assert parse.call_args.kwargs['instance'] == 'isolated'
