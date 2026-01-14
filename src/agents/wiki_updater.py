# src/agents/wiki_updater.py
from typing import Any, Dict
from providers.base import LLMProvider


class WikiUpdater:
    def __init__(self, provider: LLMProvider, sys_prompt: str):
        self.provider = provider
        self.sys_prompt = sys_prompt

    def summarize(self, text: str) -> str:
        """生成 100 字左右的精炼摘要"""
        prompt = (
            "请将以下小说章节压缩为100-150字的剧情摘要。\n"
            "要求：包含关键动作、信息增量和结果，忽略环境描写和废话。\n"
            "【正文】\n" + text[:6000]  # 截断防止溢出
        )
        # 这里可以使用 json 模式或者纯文本
        res = self.provider.generate(self.sys_prompt, prompt)
        return res.text.strip()

    def update_bible(self, old_bible: str, new_chapter: str) -> str:
        """识别新设定并更新 Bible"""
        # 为了稳定性，建议分两步：
        # 1. Ask "有哪些新设定？"
        # 2. Ask "请合并进 YAML"
        # (代码略，参考之前提供的逻辑)
        return old_bible  # 占位
