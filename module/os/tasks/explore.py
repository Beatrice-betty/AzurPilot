"""
大世界每月开荒模块。

执行大世界海域的全面探索，自动遍历并清理未完成的海域区域。
探索期间会延迟其他大世界任务（隐秘、深渊、要塞等）以避免冲突。
记录探索失败的海域 ID，支持从上次中断的位置继续探索。

Classes:
    OpsiExplore: 每月开荒处理器，继承 OSMap。
"""

from module.config.utils import get_os_next_reset, DEFAULT_TIME
from module.exception import GameStuckError, RequestHumanTakeover, ScriptError
from module.logger import logger
from module.os.globe_operation import OSExploreError
from module.os.map import OSMap


class OpsiExplore(OSMap):
    # 探索失败的区域 ID 列表
    _os_explore_failed_zone = []

    def _os_explore_post_processing_pending(self):
        """重启后仍在本月后处理阶段时，不再执行初始化战斗。"""
        state = self.config.OpsiExplore_MeowfficerCleanupState
        return isinstance(state, dict) and state.get('reset') == get_os_next_reset().isoformat() \
            and state.get('phase') in ('cleanup', 'done')

    def _os_explore_confirm_complete(self, order):
        """百分比仅表示遍历进度；逐海域确认安全解锁才允许补扫。"""
        if not order:
            raise ScriptError('每月开荒海域顺序为空，无法确认完成')
        self.os_map_goto_globe()
        self.globe_update()
        for zone in order:
            self.globe_focus_to(self.name_to_zone(zone))
            if not self.zone_has_safe():
                logger.warning(f'[大世界-补扫] 海域 {zone} 尚未解锁安全海域，继续正常开荒')
                raise OSExploreError

    def _os_explore_meowfficer_cleanup(self):
        """只进入已开荒普通海域，复用全图事件扫描和短猫的逐队雷达补查。

        每张图退出成功后保存断点；失败保留当前图，最多跨重启尝试三次。

        Pages:
            in: IN_MAP 或 IN_GLOBE
            out: IN_GLOBE
        """
        state = self.config.OpsiExplore_MeowfficerCleanupState
        reset = get_os_next_reset().isoformat()
        if not self._os_explore_post_processing_pending() or state['phase'] == 'done':
            return
        self._opsi_meowfficer_cleanup = True
        try:
            for index in range(state['next'], len(state['order'])):
                if get_os_next_reset().isoformat() != reset:
                    raise GameStuckError('补扫期间跨月，停止旧月份补扫')
                if state['attempts'] >= 3:
                    raise RequestHumanTakeover(f"海域 {state['order'][index]} 补扫连续三次未完成，请检查日志")
                state = dict(state, attempts=state['attempts'] + 1)
                self.config.OpsiExplore_MeowfficerCleanupState = state
                zone = state['order'][index]
                logger.hr(f'每月开荒后事件补扫 {index + 1}/{len(state["order"])}: {zone}', level=1)
                # SAFE 解锁只用作开荒完成的证明；补查原始普通海域，不刷新安全海域。
                self.globe_goto(zone, types='DANGEROUS', require_cleared=True)
                if self.zone.zone_id != zone:
                    raise GameStuckError(f'补扫未进入目标海域 {zone}')
                self._solved_map_event = set()
                self._solved_fleet_mechanism = False
                self.fleet_set(self.config.OpsiFleet_Fleet)
                if self.fleet_selector.get() != self.config.OpsiFleet_Fleet:
                    raise GameStuckError('补扫主舰队切换失败')
                self.map_init(map_=None)
                # must_scan 保证已清理的地图不会因为没有敌人而提前结束识别。
                self.full_scan(must_scan=self.map.camera_data)
                if not self.map_rescan(rescan_mode='full'):
                    raise GameStuckError(f'海域 {zone} 全图补扫未完成')
                self.clear_question_any_fleet()
                self.fleet_set(self.config.OpsiFleet_Fleet)
                self.os_map_goto_globe()
                if get_os_next_reset().isoformat() != reset:
                    raise GameStuckError('补扫期间跨月，停止保存旧月份进度')
                state = dict(state, next=index + 1, attempts=0)
                self.config.OpsiExplore_MeowfficerCleanupState = state
                self.config.check_task_switch()
            self.config.OpsiExplore_MeowfficerCleanupState = dict(state, phase='done')
        finally:
            self._opsi_meowfficer_cleanup = False

    def _os_explore_end(self):
        """完成开荒后运行独立补扫阶段，全部结束才延迟到下月。"""
        reset = get_os_next_reset().isoformat()
        state = self.config.OpsiExplore_MeowfficerCleanupState
        if isinstance(state, dict) and state.get('reset') != reset:
            raise GameStuckError('开荒期间跨月，停止本轮收尾，下次重新开荒')
        if self.config.OpsiExplore_MeowfficerCleanup:
            if not self._os_explore_post_processing_pending():
                order = [int(f.strip()) for f in self.config.OS_EXPLORE_FILTER.split('>')]
                self._os_explore_confirm_complete(order)
                if get_os_next_reset().isoformat() != reset:
                    raise GameStuckError('开荒完成确认期间跨月，停止本轮收尾')
                with self.config.multi_set():
                    self.config.OpsiExplore_ExploreProgress = '已完成百分之100.00'
                    self.config.OpsiExplore_MeowfficerCleanupState = {
                        'reset': reset, 'phase': 'cleanup',
                        'order': order, 'next': 0, 'attempts': 0,
                    }
            self._os_explore_meowfficer_cleanup()
        logger.info('每月开荒+已完成，延迟到下次重置')
        with self.config.multi_set():
            self.config.OpsiExplore_LastZone = 0
            self.config.OpsiExplore_ExploreProgress = '已完成百分之100.00'
            self.config.OpsiExplore_SpecialRadar = False
            self.config.task_delay(target=get_os_next_reset())
            self.config.task_call('OpsiDaily', force_call=False)
            self.config.task_call('OpsiShop', force_call=False)
        self.config.task_stop()

    def _os_explore_task_delay(self):
        """在大世界探索期间延迟其他大世界任务。"""
        logger.info('每月开荒+运行中，延迟其他大世界任务')
        with self.config.multi_set():
            next_run = self.config.Scheduler_NextRun
            delay_tasks = ['OpsiObscure', 'OpsiAbyssal', 'OpsiArchive', 'OpsiStronghold', 'OpsiMeowfficerFarming',
                         'OpsiMonthBoss', 'OpsiShop', 'OpsiScheduling']
            can_hazard1_leveling = (
                self.config.OpsiExplore_AllowHazard1Leveling and
                self.name_to_zone(self.config.OpsiExplore_LastZone).zone_id not in [0, 44, 24]
            )
            if not can_hazard1_leveling:
                delay_tasks.append('OpsiHazard1Leveling')
            for task in delay_tasks:
                keys = f'{task}.Scheduler.NextRun'
                current = self.config.cross_get(keys=keys, default=DEFAULT_TIME)
                if current < next_run:
                    logger.info(f'[大世界-探索] 延迟任务 `{task}` 到 {next_run}')
                    self.config.cross_set(keys=keys, value=next_run)

    def _os_explore(self):
        """月初探索所有危险海域区域。

        按配置顺序逐一前往各海域，已完成安全海域的区域会自动跳过。
        失败的区域 ID 会记录到 `_os_explore_failed_zone`。

        Pages:
            in: page_os, 大世界地图
            out: page_os, 大世界地图
        """

        logger.hr('大世界-每月开荒+', level=1)
        full_order = [int(f.strip(' \t\r\n')) for f in self.config.OS_EXPLORE_FILTER.split('>')]
        total_zones = len(full_order)
        # 转换用户输入
        try:
            last_zone = self.name_to_zone(self.config.OpsiExplore_LastZone).zone_id
        except ScriptError:
            logger.warning(f'[大世界-探索] 无效的 OpsiExplore_LastZone={self.config.OpsiExplore_LastZone}, 重新探索')
            last_zone = 0

        # 从上次探索的区域继续
        if last_zone in full_order:
            index = full_order.index(last_zone)
            completed_count = index + 1
            order = full_order[index + 1:]
            if total_zones > 0:
                percentage = completed_count / total_zones * 100
                self.config.OpsiExplore_ExploreProgress = f'已完成百分之{percentage:.2f}'
            logger.info(f'上次区域: {self.name_to_zone(last_zone)}, next zone: {order[:1]}')
        elif last_zone == 0:
            completed_count = 0
            order = full_order
            self.config.OpsiExplore_ExploreProgress = '已完成百分之0.00'
            logger.info(f'首次运行，下一个区域: {order[:1]}')
        else:
            raise ScriptError(f'Invalid last_zone: {last_zone}')

        if not len(order):
            self._os_explore_end()

        # 开始探索
        self._os_explore_failed_zone = []
        for zone in order:
            # 检查区域是否已解锁为安全海域
            if not self.globe_goto(zone, stop_if_safe=True):
                completed_count += 1
                if total_zones > 0:
                    percentage = completed_count / total_zones * 100
                    self.config.OpsiExplore_ExploreProgress = f'已完成百分之{percentage:.2f}'
                self.config.OpsiExplore_LastZone = zone
                continue

            # 运行区域
            logger.hr(f'大世界-每月开荒+ {zone}', level=1)
            if not self.config.OpsiExplore_SpecialRadar:
                # 特殊雷达提供 90 个调谐样本，没有特殊雷达时使用仓库中的调谐样本强化舰队
                self.tuning_sample_use()
            self.fleet_set(self.config.OpsiFleet_Fleet)
            self.os_order_execute(
                recon_scan=not self.config.OpsiExplore_SpecialRadar,
                submarine_call=self.config.OpsiFleet_Submarine)
            self._os_explore_task_delay()

            finished_combat = self.run_auto_search(question = False, rescan = 'full')
            self.config.OpsiExplore_LastZone = zone
            completed_count += 1
            if total_zones > 0:
                percentage = completed_count / total_zones * 100
                self.config.OpsiExplore_ExploreProgress = f'已完成百分之{percentage:.2f}'
            if finished_combat == 0:
                if 'is_exploration_container' in self._solved_map_event:
                    logger.info('区域已由探索容器清除')
                else:
                    logger.warning('区域已清除但未完成任何战斗')
                    self._os_explore_failed_zone.append(zone)
            self.handle_after_auto_search()
            self.config.check_task_switch()

            # 到达最后一个区域
            if zone == order[-1]:
                self._os_explore_end()

    def os_explore(self):
        """执行大世界每月开荒任务主流程。

        循环执行开荒逻辑，若遇到探索异常则返回母港重新尝试；
        连续失败时抛出异常提示检查未完成事件。

        Raises:
            GameStuckError: 开荒重试失败且无法解锁目标海域时抛出。
        """
        state = self.config.OpsiExplore_MeowfficerCleanupState
        reset = get_os_next_reset().isoformat()
        if isinstance(state, dict) and state.get('reset') != reset:
            with self.config.multi_set():
                self.config.OpsiExplore_LastZone = 0
                self.config.OpsiExplore_ExploreProgress = '已完成百分之0.00'
                self.config.OpsiExplore_MeowfficerCleanupState = None
            state = None
        if self._os_explore_post_processing_pending():
            self._os_explore_end()
            return
        if state is None:
            self.config.OpsiExplore_MeowfficerCleanupState = {'reset': reset, 'phase': 'explore'}
        for _ in range(2):
            try:
                self._os_explore()
            except OSExploreError:
                logger.info('返回 NY，重新执行每月开荒+')
                self.config.OpsiExplore_LastZone = 0
                self.globe_goto(0)

        failed_zone = [self.name_to_zone(zone) for zone in self._os_explore_failed_zone]
        logger.error(f'[大世界-每月开荒+] 以下区域开荒失败，请检查游戏设置和区域内未完成事件: {failed_zone}')
        logger.critical('[大世界-每月开荒+] 无法解锁该区域')
        raise GameStuckError
