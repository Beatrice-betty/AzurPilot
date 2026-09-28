"""只读任务与资源快照；供 API 模拟器和 worker 共用。"""
from datetime import datetime

from module.scheduler.catalog import REFRESHABLE, RESOURCES


def original_order(data, tasks):
    """模拟读取原优先级合并规则，但不创建可写配置对象或设备。"""
    from types import SimpleNamespace
    from module.base.filter import Filter
    from module.config.config_manual import ManualConfig
    config = ManualConfig()
    config.YukikazeTaskManager_TaskPriorityAdjustment = data.get('General', {}).get('YukikazeTaskManager', {}).get('TaskPriorityAdjustment')
    priority = Filter(regex=r'(.*)', attr=['command'])
    priority.load(config.SCHEDULER_PRIORITY)
    return [item.task for item in priority.apply([SimpleNamespace(command=t['name'], task=t) for t in tasks])]


def snapshot(data, observations, now):
    tasks = []
    for name, groups in data.items():
        if not isinstance(groups, dict):
            continue
        scheduler = groups.get('Scheduler', {})
        if scheduler.get('Command') and name not in ('Alas', 'Restart', 'General'):
            tasks.append({'name': name, 'enabled': bool(scheduler.get('Enable')),
                          'nextRun': str(scheduler.get('NextRun', '')), 'command': scheduler['Command']})
    resources = {}
    for name in RESOURCES:
        row = observations.get(name, {})
        at = row.get('observedAt')
        values = data.get('Dashboard', {}).get(name, {})
        if name.startswith('Emotion'):
            fleet = 'Fleet1' if name == 'Emotion1' else 'Fleet2'
            values = data.get('Main', {}).get('Emotion', {})
            at = str(values.get(f'{fleet}Record', ''))
            row = {'Value': values.get(f'{fleet}Value'), 'observedAt': at, 'source': 'emotion_record'}
        try:
            age = (now - datetime.fromisoformat(at)).total_seconds() if at else None
        except (ValueError, TypeError):
            age = None
        resources[name] = {'name': name, 'value': row.get('Value'), 'limit': row.get('Limit', values.get('Limit')),
                           'total': row.get('Total', values.get('Total')), 'observedAt': at,
                           'source': row.get('source', 'unknown'), 'refreshable': name in REFRESHABLE,
                           'status': 'missing' if age is None else 'fresh' if 0 <= age <= 300 else 'stale'}
    return {'now': now.isoformat(sep=' '), 'tasks': tasks, 'resources': resources,
            'serverReset': data.get('Main', {}).get('Scheduler', {}).get('ServerUpdate', '00:00'), 'requests': []}
