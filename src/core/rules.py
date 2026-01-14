# src/core/rules.py
import os
import glob
from typing import Dict, List


class RuleLoader:
    def __init__(self, run_dir: str):
        # 假设规则都在 run_dir/rules 或者项目根目录 config/rules
        # 这里为了简单，我们让它支持从 config/rules 读取通用规则
        self.rules_dir = os.path.join(run_dir, "rules")  # 或者 config/rules

    def load_global_rules(self) -> str:
        """加载基础写作规则 (NOVEL.md + style.md)"""
        # 实现读取逻辑...
        return "规则内容..."

    def load_specific_rules(self, tags: List[str]) -> str:
        """根据 tags (e.g., '战斗', '感情') 加载特定规则"""
        content = []
        if "战斗" in tags:
            # 读取 combat.md
            pass
        return "\n".join(content)
