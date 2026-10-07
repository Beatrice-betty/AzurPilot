"""运行参数集中定义。

本文件只包含纯字面量常量，零 import——部署与启动入口（deploy/、gui.py、
tui.py）在依赖尚未就绪的最早期阶段也需要导入其中部分常量，因此这里
不能引入任何模块，包括标准库。

两类内容：
1. 进程级参数：启动器与跨实例运行时服务使用，不存在“当前实例配置”
   的读取上下文，不开放到 WebUI 配置，直接 import 使用。
2. 配置兜底默认值：「运行参数」页（RunParams）各参数在配置读取失败
   或缺失时的回退值，与 module/config/argument/argument.yaml 中对应
   参数的 value 保持一致；修改默认值时两边同步。

各域以注释分节，搜索参数时优先按常量名全局搜索。
"""

# ==================== 配置兜底：调度与看门狗（RunParams.Watchdog） ====================

# 守护线程每 N 秒检查一次任务运行状态；任务执行期间若超过配置的
# 超时时间，则判定任务逻辑死循环，强制杀死模拟器进程以中断任务。
# 看门狗仅在任务执行阶段（self.run() 期间）激活，空闲等待（wait_until、
# 服务器维护检查）期间自动暂停，避免误触发。
WATCHDOG_CHECK_INTERVAL = 30
# 日报定时线程每 N 秒检查一次触发时刻，只有秒级精度的等待，取小值。
DAILY_SUMMARY_CHECK_INTERVAL = 1
# EmulatorManagement.RestartIntervalHours 读取失败时的兜底小时数
EMULATOR_RESTART_INTERVAL_HOURS_DEFAULT = 4
# Error.WatchdogTaskTimeout 读取失败时的兜底分钟数（0 表示禁用）
WATCHDOG_TASK_TIMEOUT_DEFAULT = 120

# ==================== 配置兜底：登录与重启（RunParams.Reboot） ====================

# 应用重启恢复策略：连续 N 次启动失败后进入观察阶段，观察期间仍无恢复
# 则由上层调度器执行模拟器重启，避免长时间无效重试。
RESTART_TRIES = 3
RESTART_FIRST_TRY_WAIT_SECONDS = 30
RESTART_SUBSEQUENT_TRY_WAIT_SECONDS = 20
RESTART_OBSERVE_SECONDS = 180
RESTART_OBSERVE_INTERVAL = 15
# 单次 app_stop/app_start 操作的硬超时秒数。
# 仅作为配置读取失败的兜底默认值；实际值从配置 Error.RestartOperationTimeout
# 读取，可在 WebUI「调试设置」中修改。
# atx-agent 自恢复可能耗时 70 秒以上，给 120 秒余量；超过则判定模拟器或
# atx-agent 卡死，立即抛出 EmulatorNotRunningError 触发模拟器重启，
# 避免 u2 调用无限挂起导致 LoginWaitTimeout / GameStuckRestart 等保护机制
# （依赖 screenshot() 中的 stuck_record_check）均无法触发的死锁。
RESTART_OPERATION_TIMEOUT = 120

# ==================== 配置兜底：设备与模拟器（RunParams.Device） ====================

# 启动监视期间打印进度的间隔（秒）。监视最长可达 480 秒且期间日志是静默的，
# 不打印进度的话，用户无法判断 ALAS 是在耐心等待还是已经卡死。
EMULATOR_START_PROGRESS_INTERVAL = 30
# 启动监视期间检查 MuMu 错误对话框的间隔（秒）。枚举窗口开销较大，不每次循环都做。
EMULATOR_START_DIALOG_CHECK_INTERVAL = 2
# 查询 MuMu12 实例状态的轮询间隔（秒）。同时用作无法查询状态时的兜底等待，
# 与旧版“等待2秒让进程状态稳定”保持一致。
MUMU12_STATE_POLL_INTERVAL = 2
# 确认 MuMu12 实例真正关闭的最长等待（秒）。
MUMU12_STOP_WAIT_TIMEOUT = 60
# 深度重启后等待全部 MuMu 进程退出的最长秒数（实测 5 秒内就干净了，留足余量）。
MUMU12_DEEP_WAIT_TIMEOUT = 30

# ==================== 进程级：设备连接重试 ====================

# ADB/u2 调用的重试次数与基础重试延迟（秒）。位于设备连接层装饰器与
# 独立工具函数内，不存在 config 读取上下文，且改动直接影响连接稳定性，
# 不开放到 WebUI 配置。
RETRY_TRIES = 5
RETRY_DELAY = 3
# 图像截断连续出现多少次后尝试恢复连接。
IMAGE_TRUNCATED_THRESHOLD = 3
