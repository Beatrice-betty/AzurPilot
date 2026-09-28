"""可展开的现成逻辑；组合卡片自身也是普通程序文档。"""
from module.scheduler.models import CardNode, Connection, ProgramDocument, SubgraphDefinition
from module.scheduler.catalog import port


def node(identifier, kind, x=0, y=0, **params):
    return CardNode(id=identifier, type=kind, params=params, position={'x': x, 'y': y})


def edge(source, target, source_port='next', target_port='in', data=False):
    return Connection(id=f'{source}.{source_port}-{target}.{target_port}', source=source, target=target,
                      sourcePort=source_port, targetPort=target_port, kind='data' if data else 'control')


def builtins():
    priority = SubgraphDefinition(id='builtin.priority', name='按优先级选择', pure=True, entry='first',
        inputs=[port('items', 'tasks', True)], outputs=[port('value', 'task')], nodes=[
            node('input', 'input', name='items'), node('order', 'priority', 250), node('first', 'first', 500)], edges=[
            edge('input', 'order', 'value', 'items', True), edge('order', 'first', 'value', 'items', True)])
    original = SubgraphDefinition(id='builtin.original', name='按原计划筛选选择', pure=True, entry='first',
        outputs=[port('value', 'task')], nodes=[node('tasks', 'tasks'), node('enabled', 'filter', 240),
            node('due', 'filter', 480, rule='due'), node('order', 'priority', 720), node('first', 'first', 960)], edges=[
            edge('tasks', 'enabled', 'value', 'items', True), edge('enabled', 'due', 'value', 'items', True),
            edge('due', 'order', 'value', 'items', True), edge('order', 'first', 'value', 'items', True)])
    def execution_sub(identifier, label, nodes, edges, inputs=()):
        return SubgraphDefinition(id=identifier, name=label, entry='entry', inputs=list(inputs),
                                 nodes=[node('entry', 'entry'), *nodes], edges=edges)
    timed = execution_sub('builtin.window', '时间窗口内执行', [node('clock', 'time_window', 0, 180),
        node('branch', 'branch', 250), node('task', 'input', 250, 180, name='task'), node('run', 'execute', 500)],
        [edge('entry', 'branch'), edge('clock', 'branch', 'value', 'condition', True), edge('branch', 'run', 'yes'),
         edge('task', 'run', 'value', 'task', True)], [port('task', 'task', True)])
    guard = SubgraphDefinition(id='builtin.oil.guard', name='石油停止阈值', pure=True, entry='compare',
        outputs=[port('value', 'boolean')], nodes=[node('oil', 'resource', name='Oil', autoRefresh=False),
            node('compare', 'compare', 250, b=2000)], edges=[edge('oil', 'compare', 'value', 'a', True)])
    threshold = execution_sub('builtin.threshold', '资源阈值启动与停止', [node('oil', 'resource', 0, 200),
        node('check', 'compare', 240, 200, b=5000), node('branch', 'branch', 240),
        node('task', 'input', 480, 200, name='task'), node('run', 'execute', 480, guard=guard.id)],
        [edge('entry', 'branch'), edge('oil', 'check', 'value', 'a', True), edge('check', 'branch', 'value', 'condition', True),
         edge('branch', 'run', 'yes'), edge('task', 'run', 'value', 'task', True)], [port('task', 'task', True)])
    rotation = SubgraphDefinition(id='builtin.rotation', name='按列表轮流选择', pure=True, entry='choose',
        inputs=[port('items', 'tasks', True)], outputs=[port('value', 'task')],
        nodes=[node('input', 'input', name='items'), node('choose', 'round_robin', 250)],
        edges=[edge('input', 'choose', 'value', 'items', True)])
    quota = execution_sub('builtin.quota', '每日配额内执行', [node('quota', 'quota', 0, 200),
        node('branch', 'branch', 250), node('record', 'record', 500), node('task', 'input', 500, 200, name='task'),
        node('run', 'execute', 750), node('tomorrow', 'wait_until', 500, 400, time='00:00')],
        [edge('entry', 'branch'), edge('quota', 'branch', 'value', 'condition', True), edge('branch', 'record', 'yes'),
         edge('record', 'run'), edge('task', 'run', 'value', 'task', True), edge('branch', 'tomorrow', 'no')],
        [port('task', 'task', True)])
    idle = execution_sub('builtin.idle', '等待最近原计划', [node('plan', 'original_plan', 0, 200), node('wait', 'wait_until', 250)],
        [edge('entry', 'wait'), edge('plan', 'wait', 'deadline', 'time', True)])
    return [priority, original, timed, guard, threshold, rotation, quota, idle]


def default_program():
    """默认只搭好原计划示例，保存或打开页面都不会启用。"""
    return ProgramDocument(entry='start', name='按优先级循环调度', subgraphs=builtins(), nodes=[
        node('start', 'entry', 0, 0), node('loop', 'loop', 320, 0),
        node('tasks', 'tasks', 0, 400),
        node('choose', 'call', 320, 300, graph='builtin.original'),
        node('run', 'execute', 640, 0), node('wait', 'wait', 960, 0, seconds=60),
        node('repeat', 'loop_end', 1280, 0, loop='loop')], edges=[
        edge('start', 'loop'), edge('loop', 'run', 'body'),
        edge('choose', 'run', 'value', 'task', True),
        *[edge('run', 'wait', outcome) for outcome in ('completed', 'yielded', 'recoverable', 'empty')],
        edge('wait', 'repeat')])


def enhance_program():
    return ProgramDocument(entry='start', name='原计划优先级增强', subgraphs=builtins(), nodes=[
        node('start', 'entry'), node('tasks', 'tasks', 0, 200), node('choose', 'call', 250, 200, graph='builtin.priority'),
        node('run', 'execute', 500)], edges=[edge('start', 'run'), edge('tasks', 'choose', 'value', 'items', True),
                                           edge('choose', 'run', 'value', 'task', True)])
