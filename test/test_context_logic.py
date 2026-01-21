# test/test_context_logic.py
import sys
import os
import shutil
import unittest
from dataclasses import dataclass

# 将 src 加入路径以便导入
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from core.context import ContextBuilder
from core.state import ProjectState, SceneNode
from utils.notifier import Notifier


# Mock Store
class MockStore:
    def __init__(self, root):
        self.root = root

    def _abs(self, rel):
        return os.path.join(self.root, rel)


class TestCoreLogic(unittest.TestCase):
    def setUp(self):
        self.test_dir = "test_run_tmp"
        os.makedirs(self.test_dir, exist_ok=True)

        # 模拟文件结构
        os.makedirs(os.path.join(self.test_dir, "01_ideation"), exist_ok=True)
        os.makedirs(os.path.join(self.test_dir, "02_outline"), exist_ok=True)

        # 写入模拟数据
        # 1. 只有原始文件
        with open(
            os.path.join(self.test_dir, "01_ideation/ideas.txt"), "w", encoding="utf-8"
        ) as f:
            f.write("Raw Idea Content")

        # 2. 既有原始文件又有精修文件
        with open(
            os.path.join(self.test_dir, "02_outline/outline.md"), "w", encoding="utf-8"
        ) as f:
            f.write("Raw Outline")
        with open(
            os.path.join(self.test_dir, "02_outline/outline_selected.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write("Selected Outline Content")

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_context_priority(self):
        """测试 ContextBuilder 是否优先读取 _selected 文件"""
        print("\nTesting Context Priority...")

        # Mock State
        state = ProjectState(run_id="test", run_dir=self.test_dir)
        state.scenes = [SceneNode(id=1, title="Test Scene")]

        store = MockStore(self.test_dir)
        builder = ContextBuilder(state, store)

        # 构建上下文
        ctx = builder.build(1)["payload"]

        # 断言：Idea 应该读取原始的（因为没有 selected）
        self.assertEqual(ctx["idea"], "Raw Idea Content")
        print("✅ Idea fallback logic passed.")

        # 断言：Outline 应该读取 Selected 的
        self.assertEqual(ctx["outline"], "Selected Outline Content")
        print("✅ Outline priority logic passed.")

    def test_notifier(self):
        """测试通知器不报错"""
        print("\nTesting Notifier...")
        cfg = {"notification": {"enabled": True, "method": ["console"]}}
        notifier = Notifier(cfg, run_id="test_run")
        notifier.notify("Test Title", "This is a test message")
        print("✅ Notifier execution passed.")


if __name__ == "__main__":
    unittest.main()
