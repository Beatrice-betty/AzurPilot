"""AzurPilot TUI 桥接后端与界面挂载单元测试。"""

import unittest

from module.tui.app import AzurPilotTUI
from module.tui.backend import TUIBackend
from module.tui.widgets import HeaderBar, LogView, ResourceBar, Sidebar, TaskTable


class TestTUIBackend(unittest.TestCase):
    """测试 TUIBackend 数据桥接与业务逻辑。"""

    def setUp(self) -> None:
        self.backend = TUIBackend()

    def test_list_instances(self) -> None:
        """测试实例列表获取。"""
        instances = self.backend.list_instances()
        self.assertIsInstance(instances, list)
        self.assertGreater(len(instances), 0)
        first = instances[0]
        self.assertIn("name", first)
        self.assertIn("status", first)
        self.assertIn("serial", first)
        self.assertIn("server", first)

    def test_switch_instance(self) -> None:
        """测试实例切换。"""
        instances = self.backend.list_instances()
        target_name = instances[0]["name"]
        self.assertTrue(self.backend.switch_instance(target_name))
        self.assertEqual(self.backend.current_instance, target_name)

        # 切换不存在的实例应返回 False
        self.assertFalse(self.backend.switch_instance("non_existent_instance_9999"))

    def test_get_overview(self) -> None:
        """测试总览信息获取与任务翻译补充。"""
        overview = self.backend.get_overview()
        self.assertIsInstance(overview, dict)
        self.assertIn("status", overview)
        self.assertIn("tasks", overview)
        self.assertIn("resources", overview)
        self.assertIn("emulator", overview)

        # 校验任务列表结构
        for task in overview.get("tasks", []):
            self.assertIn("name", task)
            self.assertIn("title", task)

    def test_get_available_tasks(self) -> None:
        """测试可独立执行的任务列表。"""
        tasks = self.backend.get_available_tasks()
        self.assertIsInstance(tasks, list)
        self.assertGreater(len(tasks), 0)
        for code, title in tasks:
            self.assertIsInstance(code, str)
            self.assertIsInstance(title, str)

    def test_get_logs(self) -> None:
        """测试日志拉取契约。"""
        cursor, entries = self.backend.get_logs(after=0)
        self.assertIsInstance(cursor, int)
        self.assertIsInstance(entries, list)


class TestTUIApp(unittest.IsolatedAsyncioTestCase):
    """测试 Textual TUI 应用生命周期与组件挂载。"""

    async def test_app_compose_and_actions(self) -> None:
        """在无头模式下测试 App 挂载与快捷指令。"""
        app = AzurPilotTUI()
        async with app.run_test():
            # 验证各主要组件成功挂载
            header = app.query_one(HeaderBar)
            sidebar = app.query_one(Sidebar)
            task_table = app.query_one(TaskTable)
            res_bar = app.query_one(ResourceBar)
            log_view = app.query_one(LogView)

            self.assertIsNotNone(header)
            self.assertIsNotNone(sidebar)
            self.assertIsNotNone(task_table)
            self.assertIsNotNone(res_bar)
            self.assertIsNotNone(log_view)

            # 模拟触发清屏快捷动作
            app.action_clear_log()
            # 模拟切换滚屏动作
            initial_scroll = log_view.auto_scroll_enabled
            app.action_toggle_scroll()
            self.assertNotEqual(initial_scroll, log_view.auto_scroll_enabled)

            # 模拟手动刷新动作
            app.action_refresh_data()


if __name__ == "__main__":
    unittest.main()
