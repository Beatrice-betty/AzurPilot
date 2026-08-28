import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from module.handler.assets import POPUP_CANCEL, POPUP_CONFIRM, USE_DATA_KEY
from module.handler.info_handler import InfoHandler


class TestUseDataKeyPopup(unittest.TestCase):
    def setUp(self):
        self.handler = object.__new__(InfoHandler)
        self.handler.config = SimpleNamespace(USE_DATA_KEY=True)
        self.handler.handle_popup_confirm = Mock(return_value=True)

    def test_confirm_when_content_template_is_missing(self):
        """专用模板失配时，作战档案仍应优先确认预期的数据密钥弹窗。"""
        self.handler.appear = Mock(side_effect=lambda button, **kwargs: {
            POPUP_CONFIRM: True,
            POPUP_CANCEL: True,
            USE_DATA_KEY: False,
        }.get(button, False))

        result = InfoHandler.handle_use_data_key(self.handler)

        self.assertTrue(result)
        self.assertFalse(self.handler.config.USE_DATA_KEY)
        self.handler.handle_popup_confirm.assert_called_once_with('USE_DATA_KEY')

    def test_keep_state_when_generic_popup_is_incomplete(self):
        """双按钮弹窗未完整出现时，不应提前清除数据密钥预期状态。"""
        self.handler.appear = Mock(side_effect=lambda button, **kwargs: {
            POPUP_CONFIRM: False,
            POPUP_CANCEL: False,
        }.get(button, False))

        result = InfoHandler.handle_use_data_key(self.handler)

        self.assertFalse(result)
        self.assertTrue(self.handler.config.USE_DATA_KEY)
        self.handler.handle_popup_confirm.assert_not_called()

    def test_keep_state_when_confirmation_fails(self):
        """点击确认未成功时，应保留状态供下一张截图重试。"""
        self.handler.handle_popup_confirm.return_value = False
        self.handler.appear = Mock(side_effect=lambda button, **kwargs: {
            POPUP_CONFIRM: True,
            POPUP_CANCEL: True,
            USE_DATA_KEY: False,
        }.get(button, False))

        result = InfoHandler.handle_use_data_key(self.handler)

        self.assertFalse(result)
        self.assertTrue(self.handler.config.USE_DATA_KEY)
        self.handler.handle_popup_confirm.assert_called_once_with('USE_DATA_KEY')


if __name__ == '__main__':
    unittest.main()
