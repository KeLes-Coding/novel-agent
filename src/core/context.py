# src/core/context.py
import os
from typing import Dict, Any, List, Optional
from core.state import ProjectState, SceneNode


class ContextBuilder:
    """
    上下文构建器
    负责从文件系统中提取最新的、最高质量的项目上下文（Idea, Outline, Bible, History），
    并将其组装成 LLM 可理解的 Prompt Payload。
    """

    def __init__(self, state: ProjectState, store):
        self.state = state
        self.store = store

    def build(self, scene_id: int) -> Dict[str, Any]:
        """
        为指定场景构建上下文
        """
        # 1. 加载全局上下文 (Global Context)
        # 优先级：人工精修版 (_selected) > 自动合并版 (ideas.txt/bible.md) > 原始生成版

        # Step 01: 创意/核心梗
        idea_text = self._load_best_content(
            "01_ideation", ["ideas_selected.txt", "ideas.txt", "01_brainstorm.json"]
        )

        # Step 02: 大纲 (提取全书剧情走向)
        outline_text = self._load_best_content(
            "02_outline", ["outline_selected.md", "outline.md", "temp/volume_1.md"]
        )

        # Step 03: 设定集 (世界观、人设)
        bible_text = self._load_best_content(
            "03_bible", ["bible_selected.md", "bible.md"]
        )

        # 2. 定位当前场景节点
        scene_node = next((s for s in self.state.scenes if s.id == scene_id), None)
        if not scene_node:
            raise ValueError(f"Scene {scene_id} not found in project state.")

        # 3. 获取前情提要 (Previous Context / Sliding Window)
        # 简单逻辑：获取上一章的 Summary。
        # 进阶逻辑(TODO)：获取前N章的 Summary + 关键伏笔。
        prev_context = ""
        prev_node = next((s for s in self.state.scenes if s.id == scene_id - 1), None)
        if prev_node and prev_node.summary:
            prev_context = f"【上一章摘要】：{prev_node.summary}"
        elif scene_id > 1:
            prev_context = "【前情提要】：(暂无摘要，请根据大纲自行衔接)"
        else:
            prev_context = "【开篇】：这是故事的第一章。"

        # 4. 组装 Payload
        # 这个字典将被传递给 Jinja2 模板
        payload = {
            # 全局背景
            "idea": idea_text,
            "outline": outline_text,
            "bible": bible_text,
            # 动态上下文
            "prev_context": prev_context,
            # 当前任务信息
            "scene_id": scene_node.id,
            "scene_title": scene_node.title,
            "scene_meta": scene_node.meta,  # 包含 goal, conflict 等
        }

        # 5. 返回构建结果与调试信息
        return {
            "payload": payload,
            "debug_info": {
                "scene_id": scene_id,
                "idea_len": len(idea_text),
                "outline_len": len(outline_text),
                "bible_len": len(bible_text),
                "prev_context_preview": prev_context[:50],
            },
        }

    def _load_best_content(self, folder: str, candidates: List[str]) -> str:
        """
        尝试按顺序加载候选文件，返回第一个存在的文件的内容。
        这确保了我们总是使用用户精修过（_selected）的版本。
        """
        for filename in candidates:
            # 构造相对路径
            rel_path = f"{folder}/{filename}"
            # 获取绝对路径用于检查
            abs_path = self.store._abs(rel_path)

            if os.path.exists(abs_path):
                try:
                    with open(abs_path, "r", encoding="utf-8") as f:
                        content = f.read().strip()
                        if content:  # 确保内容不为空
                            return content
                except Exception as e:
                    # 仅记录日志或忽略，继续尝试下一个
                    print(f"[ContextBuilder] Error reading {filename}: {e}")
                    continue

        return ""  # 如果都没找到，返回空字符串
