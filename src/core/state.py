# src/core/state.py
import json
import os
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any


# 复用之前的 SceneCandidate
@dataclass
class ArtifactCandidate:
    """通用的候选项 (用于创意、大纲等)"""

    id: str
    content: str  # 内容直接存文本，或者存路径
    score: float = 0.0
    critique: str = ""
    selected: bool = False


@dataclass
class SceneCandidate:
    id: str
    content_path: str
    summary: str = ""
    score: float = 0.0
    critique: str = ""
    selected: bool = False
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SceneNode:
    id: int
    title: str
    status: str = "pending"  # pending, drafting, review, done
    content_path: str = ""
    summary: str = ""
    characters_involved: List[str] = field(default_factory=list)

    # A/B 测试与 HITL 支持
    candidates: List[SceneCandidate] = field(default_factory=list)
    selected_candidate_id: Optional[str] = None

    version: int = 1
    meta: Dict[str, Any] = field(default_factory=dict)

    # === Phase 3: 分支支持 ===
    parent_id: Optional[int] = None
    branches: List["SceneNode"] = field(default_factory=list)
    preconditions: str = ""  # 进入此分支的条件 (自然语言或逻辑表达式)

    def to_dict(self) -> Dict[str, Any]:
        """递归序列化"""
        data = asdict(self)
        # 处理 branches 递归
        data["branches"] = [b.to_dict() for b in self.branches]
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SceneNode":
        """递归反序列化"""
        cands_data = data.pop("candidates", [])
        branches_data = data.pop("branches", [])
        
        # 实例化自身
        node = cls(**data)
        
        # 恢复 Candidates
        node.candidates = [SceneCandidate(**c) for c in cands_data]
        
        # 恢复 Branches (递归调用)
        node.branches = [cls.from_dict(b) for b in branches_data]
        
        return node


@dataclass
class ProjectState:
    run_id: str
    run_dir: str
    step: str = "init"

    # === 新增：人机交互状态控制 ===
    # status: "idle", "running", "paused_for_input"
    system_status: str = "idle"
    # 记录当前阻塞在哪一步，等待什么类型的输入
    pending_action: Optional[Dict[str, Any]] = None

    # 关键工件路径
    idea_path: str = ""
    outline_path: str = ""
    bible_path: str = ""
    scene_plan_path: str = ""

    # === 新增：全局步骤的候选项存储 ===
    idea_candidates: List[ArtifactCandidate] = field(default_factory=list)
    outline_candidates: List[ArtifactCandidate] = field(default_factory=list)
    bible_candidates: List[ArtifactCandidate] = field(default_factory=list)
    scene_plan_candidates: List[ArtifactCandidate] = field(default_factory=list)

    # === Phase 2: Memory Enhancement ===
    # 归档的卷摘要列表 (List of Chapter Summaries)
    archived_summaries: List[str] = field(default_factory=list)
    # 最后一个已归档的场景 ID (Last scene ID included in archives)
    last_archived_scene_id: int = 0

    scenes: List[SceneNode] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def save(self):
        path = os.path.join(self.run_dir, "state.json")
        # 使用自定义的 to_dict 逻辑处理嵌套
        data = asdict(self)
        data["scenes"] = [s.to_dict() for s in self.scenes]
        
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, run_dir: str) -> "ProjectState":
        path = os.path.join(run_dir, "state.json")
        if not os.path.exists(path):
            raise FileNotFoundError(f"State file not found at {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 反序列化处理
        scenes_data = data.pop("scenes", [])

        # 处理新增字段的向后兼容
        idea_cands = data.pop("idea_candidates", [])
        outline_cands = data.pop("outline_candidates", [])
        bible_cands = data.pop("bible_candidates", [])

        state = cls(**data)

        # 恢复 Scenes (使用递归的 from_dict)
        state.scenes = []
        for s_data in scenes_data:
            state.scenes.append(SceneNode.from_dict(s_data))

        # 恢复 Global Candidates
        state.idea_candidates = [ArtifactCandidate(**c) for c in idea_cands]
        state.outline_candidates = [ArtifactCandidate(**c) for c in outline_cands]
        state.bible_candidates = [ArtifactCandidate(**c) for c in bible_cands]

        return state
