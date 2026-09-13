import unittest
from types import SimpleNamespace

from module.webui.app_shell import (
    BRANCH_WATERMARK_MAX_MESSAGE_LEN,
    BRANCH_WATERMARK_NOTICE,
    BRANCH_WATERMARK_NOTICE_EN,
    branch_is_unstable,
    branch_watermark_disabled,
    build_branch_watermark_lines,
)


class TestBranchIsUnstable(unittest.TestCase):
    """验证稳定/非稳定分支判定，决定是否注入未验证版本水印。"""

    def test_stable_branches(self):
        for branch in ["master", "main"]:
            self.assertFalse(branch_is_unstable(branch), branch)

    def test_unstable_branches(self):
        for branch in ["dev", "app", "v2020.07.15", "feature/new"]:
            self.assertTrue(branch_is_unstable(branch), branch)

    def test_case_and_whitespace(self):
        self.assertFalse(branch_is_unstable("Master"))
        self.assertFalse(branch_is_unstable("  main  "))
        self.assertTrue(branch_is_unstable("DEV"))

    def test_empty_and_none_fallback_to_stable(self):
        # 空值与 None 回退为稳定，避免配置缺失时误触发水印。
        self.assertFalse(branch_is_unstable(None))
        self.assertFalse(branch_is_unstable(""))
        self.assertFalse(branch_is_unstable("   "))


def _texts(lines):
    return [line["text"] for line in lines]


class TestBuildBranchWatermarkLines(unittest.TestCase):
    """验证水印文案包含中英提醒、分支名、版本哈希与提交信息，标签使用 ASCII。"""

    COMMIT = ("a1b2c3d", "Neko", "2026-09-13 16:00:00 +0800", "Fix login loop")

    def test_with_commit(self):
        lines = build_branch_watermark_lines("dev", self.COMMIT)
        texts = _texts(lines)
        # 中英提醒各占一行且位于最前，中文为主、英文为副
        self.assertEqual(lines[0]["text"], BRANCH_WATERMARK_NOTICE)
        self.assertEqual(lines[0]["kind"], "title")
        self.assertEqual(lines[1]["text"], BRANCH_WATERMARK_NOTICE_EN)
        self.assertEqual(lines[1]["kind"], "title-en")
        self.assertEqual(len(lines), 5)
        self.assertIn("Ver.dev.a1b2c3d", texts)
        self.assertIn("Branche is:dev", texts)
        self.assertIn("Fix login loop", texts)
        # 元信息都应标记为 meta，便于 CSS 用小字号淡化。
        for line in lines[2:]:
            self.assertEqual(line["kind"], "meta")

    def test_both_notices_are_single_line(self):
        # 中英提醒各自合并为一行，不再拆成标题 + 页脚两句。
        for notice in (BRANCH_WATERMARK_NOTICE, BRANCH_WATERMARK_NOTICE_EN):
            self.assertNotIn("\n", notice)
        self.assertIn("您正在使用未经验证的版本", BRANCH_WATERMARK_NOTICE)
        self.assertIn("可能存在未知问题", BRANCH_WATERMARK_NOTICE)
        self.assertIn("unverified", BRANCH_WATERMARK_NOTICE_EN)

    def test_notice_en_is_pure_ascii(self):
        self.assertTrue(BRANCH_WATERMARK_NOTICE_EN.isascii())

    def test_no_chinese_labels_in_meta(self):
        for line in build_branch_watermark_lines("dev", self.COMMIT)[2:]:
            self.assertFalse(
                any("\u4e00" <= ch <= "\u9fff" for ch in line["text"]),
                line["text"],
            )

    def test_without_commit_falls_back_to_unknown_version(self):
        for commit in (None, (), (None, None, None, None)):
            with self.subTest(commit=commit):
                lines = build_branch_watermark_lines("app", commit)
                texts = _texts(lines)
                self.assertIn("Ver.app.unknown", texts)
                self.assertIn("Branche is:app", texts)
                # 中英提醒 + 版本 + 分支，无提交行
                self.assertEqual(len(lines), 4)

    def test_empty_branch_drops_branch_segments(self):
        texts = _texts(build_branch_watermark_lines("", self.COMMIT))
        self.assertIn("Ver.a1b2c3d", texts)
        self.assertFalse(any(t.startswith("Branche is:") for t in texts))

    def test_multiline_message_is_flattened_and_clipped(self):
        message = "line one\nline two " + "x" * 100
        lines = build_branch_watermark_lines("dev", ("sha1", "a", "t", message))
        # 提交内容裸写，无前缀，是最后一行
        subject = lines[-1]["text"]
        self.assertNotIn("\n", subject)
        self.assertTrue(subject.endswith("…"))
        self.assertEqual(len(subject), BRANCH_WATERMARK_MAX_MESSAGE_LEN)


class TestBranchWatermarkDisabled(unittest.TestCase):
    """关闭水印开关的读取：默认关闭（即显示水印），只有显式配置才隐藏。

    配置缺失、属性不存在或读取异常都必须回退到「显示水印」，避免因为配置
    问题把提醒静默关掉——水印里的版本信息是定位问题的唯一线索。
    """

    def test_default_is_show(self):
        self.assertFalse(branch_watermark_disabled(None))
        self.assertFalse(branch_watermark_disabled(object()))
        self.assertFalse(branch_watermark_disabled(
            SimpleNamespace(DisableBranchWatermark=False)))
        # 空字符串（例如 yaml 写成 "") 也不能被当成真值
        self.assertFalse(branch_watermark_disabled(
            SimpleNamespace(DisableBranchWatermark="")))

    def test_explicit_true_hides(self):
        self.assertTrue(branch_watermark_disabled(
            SimpleNamespace(DisableBranchWatermark=True)))

    def test_read_error_falls_back_to_show(self):
        class Broken:
            @property
            def DisableBranchWatermark(self):
                raise RuntimeError("boom")

        self.assertFalse(branch_watermark_disabled(Broken()))


if __name__ == "__main__":
    unittest.main()
