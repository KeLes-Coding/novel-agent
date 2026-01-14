# src/core/state.py
import json
import os
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any


@dataclass
class SceneNode:
    id: int
    title: str
    status: str = "pending"
    content_path: str = ""
    # === 新增 ===
    summary: str = ""  # 本章剧情摘要 (Episodic Memory)
    characters_involved: List[str] = field(
        default_factory=list
    )  # 本章出场人物 (用于索引)
    # ============
    version: int = 1
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProjectState:
    """整个项目的状态快照"""

    run_id: str
    run_dir: str
    step: str = "init"  # ideation, outline, bible, drafting, done

    # 关键工件路径
    idea_path: str = ""
    outline_path: str = ""
    bible_path: str = ""
    scene_plan_path: str = ""

    # 场景列表（有序）
    scenes: List[SceneNode] = field(default_factory=list)

    # 全局元数据
    meta: Dict[str, Any] = field(default_factory=dict)

    def save(self):
        """保存状态到 run_dir/state.json"""
        path = os.path.join(self.run_dir, "state.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, run_dir: str) -> "ProjectState":
        """从文件加载状态"""
        path = os.path.join(run_dir, "state.json")
        if not os.path.exists(path):
            raise FileNotFoundError(f"State file not found at {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 反序列化 SceneNode
        scenes_data = data.pop("scenes", [])
        state = cls(**data)
        state.scenes = [SceneNode(**s) for s in scenes_data]
        return state
