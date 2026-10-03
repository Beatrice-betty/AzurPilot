"""独立仓库统计任务：只浏览材料并在完整复核后提交快照。"""

from datetime import datetime
import sqlite3
import numpy as np

import module.config.server as server
from module.base.timer import Timer
from module.base.utils import random_rectangle_point
from module.exception import StorageStatisticsError
from module.logger import logger
from module.statistics.storage_snapshot import save_snapshot
from module.storage.assets import MATERIAL_STATISTICS_SCROLL
from module.storage.statistics_recognition import (StorageCatalog, StorageNoProgressError, StorageRecognitionError,
    StorageTraversal, recognize_rows, same_row)
from module.storage.ui import StorageUI
from module.ui.scroll import Scroll


class StorageStatistics(StorageUI):
    """进入材料仓库，从顶到底扫描两遍；识别或复核失败保留旧快照。"""

    def _scan_pass(self, catalog):
        """同一截图状态循环完成回顶、滚动、稳定复读与到底确认。

        Pages:
            in: page_storage, material
            out: page_storage, material
        """
        scroll = Scroll(MATERIAL_STATISTICS_SCROLL, color=(247, 211, 66))
        scroll.edge_threshold = .006
        traversal = StorageTraversal()
        target = 0.
        moving = True
        dragged = False
        previous = None
        previous_position = None
        recovery_origin = None
        recovery_count = 0
        stable = Timer(.4, count=1)
        action = Timer(2, count=2)
        timeout = Timer(20, count=40).start()
        last_error = '等待材料仓库稳定'
        for image in self.loop(skip_first=False):
            if timeout.reached():
                raise StorageRecognitionError(last_error)
            if self.handle_info_bar():
                previous = None
                continue
            if not self._storage_in_material():
                # 页签的动画或短暂遮挡不能覆盖此前真实的识别错误，也不能盲目滑动。
                previous = None
                continue
            mask = scroll.match_color(self)
            if not mask.any():
                raise StorageRecognitionError('无法确认仓库滚动条位置')
            pixels = np.flatnonzero(mask)
            if pixels[-1] - pixels[0] + 1 != len(pixels):
                last_error = '仓库滚动条被光点或遮挡干扰'
                previous = None
                continue
            position = 0. if scroll.length == scroll.total else scroll.cal_position(self)
            if not np.isfinite(position):
                raise StorageRecognitionError('仓库滚动条位置无效')
            at_top = pixels[0] <= 1
            at_bottom = pixels[-1] >= scroll.total - 2
            if moving:
                edge_ready = (scroll.length == scroll.total
                              or (target == 0. and at_top or target == 1. and at_bottom) and dragged)
                target_ready = (dragged and 0. < target < 1.
                                and abs(position - target) * (scroll.total - scroll.length) <= 8)
                if edge_ready or target_ready:
                    moving = False
                    previous = None
                    if not recovery_count:
                        timeout.reset()
                    continue
                if action.reached():
                    start = random_rectangle_point(scroll.position_to_screen(position, (0, 0)), n=1)
                    # 顶/底额外拖过边界，再用当前滚动条端点确认，避免百分比取整漏首末行。
                    destination = -.1 if target == 0. else 1.1 if target == 1. else target
                    end = random_rectangle_point(scroll.position_to_screen(destination, (0, 0)), n=1)
                    if 0. < target < 1. and abs(end[1] - start[1]) < 12:
                        # 长列表的滑块很短；改在列表内翻半页，避免几像素手势不生效。
                        self.device.swipe((1180, 500), (1180, 245),
                                          name='StorageStatistics', distance_check=False)
                        moving = False
                    else:
                        self.device.swipe(start, end, name='StorageStatistics', distance_check=False)
                    dragged = True
                    action.reset()
                continue
            if at_bottom and target < 1. and scroll.length < scroll.total:
                # 中途位置被取整到端点附近时，先实际拖到底，再读取末行。
                target, moving, dragged = 1., True, False
                previous = None
                action.clear()
                timeout.reset()
                continue
            try:
                rows = recognize_rows(image, catalog)
                partial = [i for i, row in enumerate(rows) if any(not card.present for card in row)]
                if partial and (partial != [len(rows) - 1] or not at_bottom):
                    raise StorageRecognitionError('只有确认到底后的末行才允许空格')
                equal = (previous is not None and len(previous) == len(rows)
                         and abs(position - previous_position) <= .002
                         and all(same_row(a, b) and abs(a[0].area[1] - b[0].area[1]) <= 1
                                 for a, b in zip(previous, rows)))
                if not equal:
                    previous, previous_position = rows, position
                    stable.reset()
                    continue
                if not stable.reached():
                    continue
                traversal.append(rows, at_bottom=at_bottom)
            except StorageRecognitionError as error:
                if last_error != str(error):
                    logger.warning(f'仓库当前页待重读：{error}')
                last_error = str(error)
                previous = None
                if action.reached():
                    if recovery_count >= 4 or scroll.length == scroll.total:
                        raise
                    if recovery_origin is None:
                        recovery_origin = position
                    if not traversal.rows or at_bottom:
                        # 首末行必须在端点读取。先移开遮挡再回到同一端点，禁止漏首尾行。
                        delta = 64 if at_top else -64
                        target, moving, dragged = (0. if not traversal.rows else 1.), True, False
                    else:
                        # 围绕失败位置上下微调；拼接仍须唯一重叠，不能把跳页当成成功。
                        offsets = (64, -64, 96, -96) if isinstance(error, StorageNoProgressError) else (-64, 64, -96, 96)
                        shift = (position - recovery_origin) * (scroll.total - scroll.length) / scroll.length * 572
                        delta = int(np.clip(offsets[recovery_count] - shift, -256, 256))
                        if abs(delta) < 24:
                            delta = 64 if delta >= 0 else -64
                    self.device.swipe((1180, 365), (1180, 365 - delta),
                                      name='StorageStatistics', distance_check=False)
                    recovery_count += 1
                    logger.attr('仓库重读', f'第 {recovery_count} 次微调，向{"下" if delta > 0 else "上"}滚动 {abs(delta)}px')
                    action.reset()
                continue
            recovery_origin, recovery_count = None, 0
            logger.attr('仓库扫描', f'第 {traversal.pages} 页，累计 {len(traversal.rows)} 行')
            if at_bottom:
                return traversal
            target = min(1., position + .45 * scroll.length / (scroll.total - scroll.length))
            moving = True
            dragged = False
            previous = None
            action.clear()
            timeout.reset()
        raise StorageRecognitionError('仓库扫描中断')

    def run(self):
        """只在两次完整扫描一致时更新物品。

        Pages:
            in: 任意可导航页面
            out: page_storage, material
        """
        logger.hr('仓库统计', level=1)
        started_at = datetime.now().isoformat(sep=' ', timespec='seconds')
        try:
            catalog = StorageCatalog()
            if server.server not in catalog.servers:
                raise StorageRecognitionError('当前仓库模板仅经国服截图验证，尚不支持此服务器')
            self.ui_goto_storage()
            self._storage_enter_material()
            first = self._scan_pass(catalog)
            logger.hr('仓库统计复核', level=2)
            second = self._scan_pass(catalog)
            if len(first.rows) != len(second.rows) or any(
                not same_row(a, b) for a, b in zip(first.rows, second.rows)
            ):
                raise StorageRecognitionError('两次完整扫描不一致，可能仍在滚动或物品数量已变化')
            save_snapshot(self.config.config_name, server.server, catalog.snapshot_items(second.rows),
                          started_at=started_at, pages=first.pages + second.pages,
                          catalog_version=catalog.version)
        except (ValueError, sqlite3.Error, OSError) as error:
            self.config.task_delay(success=False)
            raise StorageStatisticsError(f'仓库统计未更新，保留上次完整快照：{error}') from error
        logger.info('仓库统计完整复核并保存成功')
        self.config.task_delay(success=True)
