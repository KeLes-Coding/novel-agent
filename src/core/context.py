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

    def _find_node_recursive(self, nodes: List[SceneNode], target_id: int) -> Optional[SceneNode]:
        for node in nodes:
            if node.id == target_id:
                return node
            if node.branches:
                found = self._find_node_recursive(node.branches, target_id)
                if found:
                    return found
        return None

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
        scene_node = self._find_node_recursive(self.state.scenes, scene_id)
        if not scene_node:
            raise ValueError(f"Scene {scene_id} not found in project state.")

        # 3. 获取前情提要 (Previous Context / Sliding Window)
        # 3. 获取前情提要 (Dynamic Memory Assembly)
        # Structure: [History Arc] + [Recent Scenes]
        
        prev_context_parts = []
        
        # Part A: 历史长河 (Archived Chapter Summaries)
        if self.state.archived_summaries:
            history_text = "\n".join([f"- 卷{i+1}: {s}" for i, s in enumerate(self.state.archived_summaries)])
            prev_context_parts.append(f"【往事回顾】(已归档剧情):\n{history_text}")
            
        # Part B: 近期记忆 (Active Scenes since last archive)
        # Range: (last_archived_scene_id, current_scene_id - 1)
        recent_summaries = []
        start_recent = self.state.last_archived_scene_id + 1
        end_recent = scene_id - 1
        
        if start_recent <= end_recent:
             for sid in range(start_recent, end_recent + 1):
                node = next((s for s in self.state.scenes if s.id == sid), None)
                if node and node.summary:
                    recent_summaries.append(f"- Scene {sid}: {node.summary}")
        
        if recent_summaries:
            recent_text = "\n".join(recent_summaries)
            prev_context_parts.append(f"【近期剧情】(未归档):\n{recent_text}")
            
        # Fallback for very first scene
        if not prev_context_parts:
            prev_context_parts.append("【开篇】：这是故事的第一章。")
            
        prev_context = "\n\n".join(prev_context_parts)

        # === FIX: 斩断循环引用 ===
        # 创建 meta 的副本，并移除可能存在的旧 dynamic_context
        # 否则：meta -> dynamic_context -> payload -> meta -> ... (无限递归)
        safe_meta = scene_node.meta.copy()
        if "dynamic_context" in safe_meta:
            del safe_meta["dynamic_context"]

        # 4. 组装 Payload
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
            "scene_meta": safe_meta,  # 使用安全的副本
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
        """
        for filename in candidates:
            rel_path = f"{folder}/{filename}"
            abs_path = self.store._abs(rel_path)

            if os.path.exists(abs_path):
                try:
                    with open(abs_path, "r", encoding="utf-8") as f:
                        content = f.read().strip()
                        if content:
                            return content
                except Exception as e:
                    print(f"[ContextBuilder] Error reading {filename}: {e}")
                    continue

        return ""
