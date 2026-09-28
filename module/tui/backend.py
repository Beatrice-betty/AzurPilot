"""AzurPilot TUI 桥接后端。

负责与 ConfigService、RuntimeService 及 ProcessManager 对接，
为 TUI 前端提供多实例状态查询、任务计划时间表、日志增量拉取及进程控制能力。
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from module.api.config_service import ConfigService
from module.api.protocol import ApiError
from module.api.runtime_service import RuntimeService
from module.submodule.utils import get_available_func


class TUIBackend:
    """TUI 运行时桥接后端。

    Attributes:
        config_service (ConfigService): 配置管理服务。
        runtime_service (RuntimeService): 运行时管理服务。
        current_instance (str): 当前选中的活动实例名称。
    """

    def __init__(self, root: Optional[Path] = None, default_instance: Optional[str] = None) -> None:
        """初始化 TUI 桥接后端。

        Args:
            root (Optional[Path]): 仓库根目录，缺省时使用 ConfigService 默认 ROOT。
            default_instance (Optional[str]): 启动时指定的默认实例名称。
        """
        self.config_service = ConfigService(root=root) if root else ConfigService()
        self.runtime_service = RuntimeService(self.config_service)
        
        # 确定初始活动实例
        instances = self.config_service.names()
        if default_instance and default_instance in instances:
            self.current_instance = default_instance
        elif instances:
            self.current_instance = instances[0]
        else:
            self.current_instance = "alas"

    def list_instances(self) -> List[Dict[str, Any]]:
        """获取所有可用配置实例的状态与元数据列表。

        Returns:
            List[Dict[str, Any]]: 包含各实例名称、运行状态、当前任务和模拟器信息的字典列表。
        """
        try:
            return self.runtime_service.instances()
        except Exception:
            # 容灾：直接从 config_service 获取实例列表
            names = self.config_service.names()
            return [{"name": name, "status": "stopped", "currentTask": None, "serial": "auto", "server": "cn"} for name in names]

    def switch_instance(self, instance: str) -> bool:
        """切换当前活动实例。

        Args:
            instance (str): 目标实例名称。

        Returns:
            bool: 切换成功返回 True，若实例不存在返回 False。
        """
        if instance in self.config_service.names():
            self.current_instance = instance
            return True
        return False

    def translate_task(self, task: str) -> str:
        """根据国际化字典翻译任务名称。

        Args:
            task (str): 任务标识符（如 Commission、Research 等）。

        Returns:
            str: 国际化中文名称；若无对应翻译则返回原名。
        """
        translated = self.config_service.translate(f"{task}._info.name")
        return translated if translated and translated != "_info.name" else task

    def get_overview(self, instance: Optional[str] = None) -> Dict[str, Any]:
        """获取指定实例的总览信息。

        Args:
            instance (Optional[str]): 实例名称；缺省时使用当前活动实例。

        Returns:
            Dict[str, Any]: 包含实例状态、任务队列（带翻译名称）、资源看板与模拟器设置的字典。
        """
        target = instance or self.current_instance
        try:
            data = self.runtime_service.overview(target)
            # 为 tasks 队列中的每个任务补充人类友好的翻译标题
            for task_item in data.get("tasks", []):
                task_item["title"] = self.translate_task(task_item.get("name", ""))
            return data
        except Exception as e:
            return {
                "instance": target,
                "status": "error",
                "error": str(e),
                "tasks": [],
                "resources": [],
                "emulator": {},
            }

    def start_scheduler(self, instance: Optional[str] = None) -> Tuple[bool, str]:
        """启动实例的主调度器。

        Args:
            instance (Optional[str]): 实例名称；缺省时使用当前活动实例。

        Returns:
            Tuple[bool, str]: (是否成功, 提示消息)。
        """
        target = instance or self.current_instance
        try:
            self.runtime_service.start(target, task=None)
            return True, f"实例 [{target}] 主调度器已启动"
        except ApiError as e:
            return False, f"启动失败: {e.message}"
        except Exception as e:
            return False, f"启动异常: {e}"

    def start_task(self, task: str, instance: Optional[str] = None) -> Tuple[bool, str]:
        """单独执行指定的功能任务。

        Args:
            task (str): 任务功能标识符。
            instance (Optional[str]): 实例名称；缺省时使用当前活动实例。

        Returns:
            Tuple[bool, str]: (是否成功, 提示消息)。
        """
        target = instance or self.current_instance
        try:
            self.runtime_service.start(target, task=task)
            task_title = self.translate_task(task)
            return True, f"实例 [{target}] 单任务 [{task_title}] 已启动"
        except ApiError as e:
            return False, f"任务启动失败: {e.message}"
        except Exception as e:
            return False, f"任务启动异常: {e}"

    def stop_instance(self, instance: Optional[str] = None) -> Tuple[bool, str]:
        """停止指定实例的运行。

        Args:
            instance (Optional[str]): 实例名称；缺省时使用当前活动实例。

        Returns:
            Tuple[bool, str]: (是否成功, 提示消息)。
        """
        target = instance or self.current_instance
        try:
            self.runtime_service.stop(target)
            return True, f"实例 [{target}] 停止请求已发出"
        except ApiError as e:
            return False, f"停止失败: {e.message}"
        except Exception as e:
            return False, f"停止异常: {e}"

    def get_logs(self, instance: Optional[str] = None, after: int = 0) -> Tuple[int, List[Dict[str, Any]]]:
        """增量拉取指定实例的新日志记录。

        Args:
            instance (Optional[str]): 实例名称；缺省时使用当前活动实例。
            after (int): 客户端当前的日志序列号游标。

        Returns:
            Tuple[int, List[Dict[str, Any]]]: (最新日志游标, 新日志条目列表)。
        """
        target = instance or self.current_instance
        try:
            res = self.runtime_service.logs(target, after=after)
            new_cursor = res.get("cursor", after)
            entries = res.get("entries", [])
            return new_cursor, entries
        except Exception:
            return after, []

    def get_available_tasks(self) -> List[Tuple[str, str]]:
        """获取支持单独执行的功能任务列表。

        Returns:
            List[Tuple[str, str]]: (任务代码, 人类友好中文名称) 元组列表。
        """
        funcs = get_available_func()
        result = []
        for func in funcs:
            title = self.translate_task(func)
            result.append((func, title))
        return result
