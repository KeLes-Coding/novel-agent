# src/core/context.py
import os
from typing import Dict, Any, List, Tuple
from core.state import ProjectState


class ContextBuilder:
    def __init__(self, state: ProjectState, store: Any, token_limit: int = 25000):
        self.state = state
        self.store = store
        self.token_limit = token_limit  # 上下文预算

    def build(self, current_scene_id: int, tags: List[str] = None) -> Dict[str, Any]:
        """
        组装 Prompt 所需的上下文，并返回详细的组装日志用于 Trace。
        """
        context_log = {"budget": self.token_limit, "components": {}}

        # 1. 必选：最新 Bible (P0)
        # TODO: 这里未来可以做成只提取相关人物
        bible_text = self._read_file(self.state.bible_path)
        context_log["components"]["bible"] = "Full Loaded"

        # 2. 必选：上文接龙 (P1) - 滑动窗口
        # 取上一个场景的最后 800 字
        prev_text = self._get_previous_text_tail(current_scene_id, chars=800)
        context_log["components"]["prev_text_chars"] = len(prev_text)

        # 3. 可选：剧情回顾 (P2) - Auto-Compaction
        # 获取所有已完成场景的摘要
        story_so_far, summary_count = self._compile_summaries(current_scene_id)
        context_log["components"]["past_summaries_count"] = summary_count

        # 4. 可选：特定规则 (P3)
        # rules_text = rule_loader.load(tags) ...

        return {
            "payload": {
                "bible_text": bible_text,
                "prev_text": prev_text,
                "story_so_far": story_so_far,
            },
            "debug_info": context_log,  # 这将被写入 TraceLogger
        }

    def _read_file(self, path: str) -> str:
        if not path or not os.path.exists(path):
            return ""
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def _get_previous_text_tail(self, current_id: int, chars: int) -> str:
        # 寻找 ID 小于 current_id 的最大 ID
        prev_node = None
        for s in self.state.scenes:
            if s.id < current_id:
                prev_node = s
            else:
                break

        if (
            prev_node
            and prev_node.content_path
            and os.path.exists(prev_node.content_path)
        ):
            text = self._read_file(prev_node.content_path)
            return text[-chars:]
        return ""

    def _compile_summaries(self, current_id: int) -> Tuple[str, int]:
        summaries = []
        for s in self.state.scenes:
            if s.id < current_id and s.status == "done" and s.summary:
                summaries.append(f"【第{s.id}章】{s.summary}")
        return "\n".join(summaries), len(summaries)
