"""前端 mock 使用同一解释器；输入输出仅走管道，不读取实例或设备。"""
import json
import sys
from datetime import datetime, timedelta

from module.scheduler.catalog import CARDS, RESOURCES, REFRESHABLE, OVERRIDES
from module.scheduler.context import snapshot
from module.scheduler.engine import simulate, parse_time
from module.scheduler.models import ProgramDocument
from module.scheduler.templates import builtins, default_program, enhance_program
from module.scheduler.validation import validate


def dispatch(request):
    action = request['action']
    context = snapshot(request.get('config', {}), {}, datetime.now())
    if action == 'catalog':
        return {'cards': [c.model_dump() for c in CARDS], 'resources': [
            {'name': n, 'label': n, 'refreshable': n in REFRESHABLE} for n in RESOURCES],
            'tasks': context['tasks'], 'overrides': OVERRIDES, 'builtins': [s.model_dump() for s in builtins()],
            'templates': {'takeover': default_program().model_dump(), 'enhance': enhance_program().model_dump()}}
    doc = ProgramDocument.model_validate(request['document'])
    result = validate(doc, {t['name'] for t in context['tasks']}, request.get('mode', 'takeover'))
    if action == 'validate':
        return result
    if not result['valid']:
        return {**result, 'state': {'status': 'error', 'trace': []}, 'effects': []}
    context.update(request.get('context', {}))
    instant = parse_time(context['now'])
    due = [t for t in context['tasks'] if t['enabled'] and parse_time(t['nextRun']) <= instant]
    context.setdefault('nativeTask', due[0] if due else None)
    context.setdefault('nativeDeadline', (instant + timedelta(minutes=5)).isoformat(sep=' '))
    if request.get('mode') == 'enhance':
        context['tasks'] = due
    return {**result, **simulate(doc, context, request.get('outcomes'), request.get('steps', 100))}


if __name__ == '__main__':
    try:
        result = dispatch(json.load(sys.stdin))
    except (ValueError, KeyError, TypeError) as exc:
        result = {'error': str(exc)}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
