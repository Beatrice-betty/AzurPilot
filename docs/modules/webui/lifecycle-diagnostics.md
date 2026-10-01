# 后端生命周期诊断

用于排查启动器仍在运行，但 WebUI 连接失败且 ALAS 日志突然停止的问题。第一轮仅采样和留存现场，不调整自动更新、看门狗或恢复策略。

## 文件与会话

- 启动器继续使用启动当日本地日期的 `log/YYYY-MM-DD_launcher.txt`，再次启动追加而非清空；跨午夜仍写入本次启动文件。
- Python 父监督器单独写 `log/YYYY-MM-DD_gui-supervisor.txt`。
- `log/diagnostics/<session>-launcher.jsonl` 是启动器中的独立观察线程，每 5 秒记录进程、端口监听者、TCP 和 `/healthz` 状态。它位于 Python Job 外部，后端退出不影响其记录。
- `log/diagnostics/<session>-<pid>.jsonl` 是各 Python 进程的直接落盘事件和心跳，包含 PID、PPID、创建时间、本地时间、UTC、单调时钟及退出码。
- `<session>-<pid>.stacks.txt` 保存原生异常或健康检查连续失败时的所有线程栈，不记录局部变量。连续 3 次健康检查失败请求一次，每次启动最多请求 8 次；进程已经死亡或诊断线程被卡住时无法补采线程栈。

JSONL 不通过 Manager 或业务日志队列。状态仅包含任务名、定时计划、重启事件、更新状态及看门狗阈值；不要将配置字典、命令行、账号密钥或异常原文加入事件。

记录是尽力落盘：写入锁忙或磁盘故障时允许丢弃，不能保证强杀前的最后一条记录保存。5 秒采样可能漏掉短命子进程；Windows 对已观察到的进程保留只读句柄，以便进程消失后读取退出码。观察器跟随启动器退出，不负责调查启动器自身被强杀。

## 一次复现的读取顺序

1. 先找启动器 `backend_exit_observed`（根进程退出码）或 HTTP/TCP 从正常到异常的心跳。
2. 对照所有 Python 文件中的最后心跳、`terminate_intent`、`restart_request`、`watchdog_trigger`。
3. 查看父监督器的 `webui_exit_observed`、`webui_cleanup_result`、`webui_spawn`、`webui_ready` 或 `supervisor_exit_complete`。
4. 若 PID 仍在但 HTTP 无响应，查看对应线程栈。`process_disappeared` 只代表观察到进程身份消失，不能据此声称是谁结束它。

启动器日志内的 `Z` 表示 UTC，换算北京时间加 8 小时；JSONL 同时提供两种时间。内部 `terminate_result.signal_sent` 表示终止调用已返回，不等于所有目标已退出；最终退出结果以父进程退出码、清理结果和观察器为准。

## 部署与下一步

本体和启动器两边都需要新版才能自动关联同一会话。不要在当前运行实例上覆盖 Python 文件或替换启动器；先结束本轮运行，再按正常更新流程部署。保留故障会话整套文件后再重启。

复现后复制本次会话的整个 `log/diagnostics/`、启动当日的启动器日志、`gui-supervisor` 日志、GUI 和实例业务日志，再进行人工重启。现有“下载 WebUI 日志”和“下载启动器日志”入口保持原样，独立诊断文件需从目录收集。

本轮不提供异常后自动拉起的新行为。获得退出/挂死/清理失败证据后，修复对应分支，再增加有限恢复，并验证旧实例已清理、端口健康且没有重复控制设备。外部强杀的发起者可能需要额外系统级取证；Python 线程栈不能替代原生内存转储。
