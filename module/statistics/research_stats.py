"""科研掉落统计汇总。

把 cl1_record.db 里逐次记录的科研掉落，按「科研期数 × 物品」聚合，供 WebUI
的科研统计页展示。

展示口径（用户 2026-09-24 定）：
- **期数视图**（第 1~9 期）：只统计**彩装备、彩图纸、金图纸**与心智单元；
- **金装视图**（不分期）：只看**金装备图纸**，所有期数合并；
- **心智/物资视图**（不分期）：只看**心智单元与物资**，所有期数合并。

不分期的两个口径是必要的：只有彩装备与舰船图纸绑定期数，金装备、心智单元与物资
都是各期混着出的（见 alas-research-stats 技能文档第六节第 16 条）。
其余物品照常入库，只是不出现在这里，将来想扩展口径改 should_show 即可。
"""

import json
import os
import typing as t
from collections import defaultdict
from datetime import date, datetime, timedelta

from module.logger import logger

NAME_TABLE_PATH = './assets/stats/research_item_names.json'
# 中文名与稀有度的静态表，由 dev_tools/research_template_extract.py 生成

# 展示的稀有度门槛：4 = 金，5 = 彩。金装备图纸与改造图纸不入期数视图。
SHOW_RARITY = (4, 5)
# 金装视图认的稀有度
RARITY_GOLD = 4
# 界面上的稀有度标签，与游戏内一致：彩 > 金 > 紫 > 蓝
RARITY_LABELS = {6: '彩', 5: '彩', 4: '金', 3: '紫', 2: '蓝'}
# 心智单元的模板名，单独放行（它没有 T 品阶后缀，查不到稀有度）
ALWAYS_SHOW = ('CognitiveChips',)

# 视图口径
SCOPE_SERIES = 'series'
SCOPE_GOLD = 'gold'
SCOPE_CONSUMABLE = 'consumable'
# 心智/物资视图认的物品（模板名）。这两件不绑期数，各期混着出，所以单开一个不分期的视图。
CONSUMABLE_ITEMS = ('CognitiveChips', 'Coins')
# 少数物品的名字推导不出模板名（如心智单元没有 T 后缀），内置兜底
BUILTIN_NAMES = {
    'CognitiveChips': {'zh': '心智单元', 'en': 'Cognitive Chips', 'rarity': 4},
}

_name_table: t.Optional[dict] = None


def load_name_table() -> dict:
    """读取模板名 -> 中文名/稀有度的静态表（进程内缓存）。

    Returns:
        dict: {模板名: {'zh': ..., 'en': ..., 'rarity': ...}}；表缺失时返回空字典。
    """
    global _name_table
    if _name_table is None:
        try:
            with open(NAME_TABLE_PATH, encoding='utf-8') as f:
                _name_table = json.load(f)
        except (OSError, ValueError):
            logger.warning(f'[科研统计] 名称表不存在或损坏: {NAME_TABLE_PATH}')
            _name_table = {}
    return _name_table


def item_info(template_name: str) -> dict:
    """取物品的中文名与稀有度。

    Args:
        template_name (str): 模板文件名（不含扩展名），如 'BlueprintValparaiso'。

    Returns:
        dict: {'zh': 中文名, 'en': 英文名, 'rarity': 稀有度}；查不到时用模板名兜底。
    """
    table = load_name_table()
    info = BUILTIN_NAMES.get(template_name) or table.get(template_name)
    if info:
        return info
    # 变体（_2/_3）与库外模板走兜底，至少让界面不是空白
    base, _, suffix = template_name.rpartition('_')
    if base and suffix.isdigit():
        info = table.get(base)
        if info:
            return info
    return {'zh': template_name, 'en': template_name, 'rarity': None}


def is_gold_equipment(template_name: str) -> bool:
    """判断是不是「金装」：稀有度 4 的**装备图纸**。

    舰船图纸与心智单元不算金装——它们在期数视图里各自有位置。

    Args:
        template_name (str): 模板文件名（不含扩展名）。

    Returns:
        bool: 是否金装。
    """
    if template_name.startswith('Blueprint') or template_name in ALWAYS_SHOW:
        return False
    info = item_info(template_name)
    if info.get('rarity') != RARITY_GOLD:
        return False
    # 稀有度对上了还要确认是装备图纸：物资、装备本身的名字里没有「设计图」
    return (info.get('zh') or '').endswith('设计图')


def should_show(template_name: str, scope: str = SCOPE_SERIES) -> bool:
    """判断某件掉落是否进入当前视图。

    期数视图只展示彩装备、彩图纸、金图纸与心智单元；金装视图只展示金装备图纸；
    心智/物资视图只展示心智单元与物资。稀有度来自静态名称表，查不到时（模板没
    收录进表）一律不展示，避免用乱码占屏。

    Args:
        template_name (str): 模板文件名（不含扩展名）。
        scope (str): 视图口径，SCOPE_SERIES / SCOPE_GOLD / SCOPE_CONSUMABLE。

    Returns:
        bool: 是否展示。
    """
    if scope == SCOPE_GOLD:
        return is_gold_equipment(template_name)
    if scope == SCOPE_CONSUMABLE:
        return template_name in CONSUMABLE_ITEMS
    if template_name in ALWAYS_SHOW:
        return True
    info = item_info(template_name)
    rarity = info.get('rarity')
    if rarity not in SHOW_RARITY:
        return False
    if template_name.startswith('Blueprint'):
        return True
    return rarity == 5


def _iter_entries(instance: str, start: datetime, end: datetime) -> t.Iterator[dict]:
    """跨月读取掉落条目。

    Args:
        instance (str): ALAS 实例名。
        start (datetime): 起始时间（含）。
        end (datetime): 结束时间（不含）。

    Yields:
        dict: 单条掉落记录。
    """
    from module.statistics.cl1_database import db

    cursor = start.replace(day=1)
    while cursor < end:
        for entry in db.get_research_drop(instance, cursor.year, cursor.month):
            yield entry
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)


def _entry_date(entry: dict) -> t.Optional[date]:
    """取一条掉落记录的日期。

    记录里 `ts` 与 `completed_at` 都是 ISO 时间串；缺失或损坏时返回 None，
    调用方据此跳过「今日/本月」计数，而不是把缺失值当成今天。

    Args:
        entry (dict): 单条掉落记录。

    Returns:
        date: 记录日期；解析不出来时 None。
    """
    for key in ('ts', 'completed_at'):
        raw = entry.get(key)
        if not raw:
            continue
        try:
            return datetime.fromisoformat(str(raw)).date()
        except ValueError:
            continue
    return None


def collect(instance: str, days: int = 90, series: int = 0, scope: str = SCOPE_SERIES) -> dict:
    """汇总最近若干天的科研掉落。

    Args:
        instance (str): ALAS 实例名。
        days (int): 回溯天数。
        series (int): 只看某一期（1~9）；0 表示最新有记录的一期。不分期的口径忽略此参数。
        scope (str): 视图口径，SCOPE_SERIES / SCOPE_GOLD / SCOPE_CONSUMABLE。

    Returns:
        dict: {
            'scope': 视图口径,
            'series': 实际展示的期数（不分期的口径为 0）,
            'available': 有记录的期数列表（降序）,
            'items': [{'name': 模板名, 'zh': 中文名, 'rarity': ..., 'amount': 总数量,
                       'count': 掉落次数, 'today': 今日数量, 'month': 本月数量}, ...],
            'total': 总掉落数量,
            'today': 今日掉落总数量,
            'month': 本月掉落总数量,
            'records': 纳入统计的记录条数,
        }
    """
    now = datetime.now()
    start = now - timedelta(days=max(1, days))
    entries = list(_iter_entries(instance, start, now + timedelta(seconds=1)))

    available = sorted({int(e.get('series') or 0) for e in entries if e.get('series')}, reverse=True)
    if scope != SCOPE_SERIES:
        picked = entries
    else:
        if series <= 0:
            series = available[0] if available else 0
        picked = [e for e in entries if int(e.get('series') or 0) == series]

    today = now.date()
    this_month = (now.year, now.month)
    amount_by_item: t.Dict[str, int] = defaultdict(int)
    count_by_item: t.Dict[str, int] = defaultdict(int)
    today_by_item: t.Dict[str, int] = defaultdict(int)
    month_by_item: t.Dict[str, int] = defaultdict(int)
    for entry in picked:
        stamp = _entry_date(entry)
        for name, amount in (entry.get('items') or {}).items():
            if not should_show(name, scope=scope):
                continue
            amount = int(amount)
            amount_by_item[name] += amount
            count_by_item[name] += 1
            if stamp == today:
                today_by_item[name] += amount
            if stamp is not None and (stamp.year, stamp.month) == this_month:
                month_by_item[name] += amount

    items = []
    for name, amount in amount_by_item.items():
        info = item_info(name)
        items.append({
            'name': name,
            'zh': info.get('zh') or name,
            'en': info.get('en') or '',
            'rarity': info.get('rarity'),
            'amount': amount,
            'count': count_by_item[name],
            'today': today_by_item[name],
            'month': month_by_item[name],
        })
    # 彩在前、金在后，同稀有度按数量降序
    items.sort(key=lambda item: (-(item['rarity'] or 0), -item['amount'], item['zh']))

    return {
        'scope': scope,
        'series': 0 if scope != SCOPE_SERIES else series,
        'available': available,
        'items': items,
        'total': sum(item['amount'] for item in items),
        'today': sum(today_by_item.values()),
        'month': sum(month_by_item.values()),
        'records': len(picked),
    }
