# src/core/fsm.py
from enum import Enum, auto
from typing import List, Optional, Dict, Any
import logging

from core.state import ProjectState

class ProjectPhase(Enum):
    INIT = "init"
    IDEATION = "ideation"
    OUTLINE = "outline"
    BIBLE = "bible"
    SCENE_PLAN = "scene_plan"
    DRAFTING = "drafting"
    REVIEW = "review"
    DONE = "done"

class StateMachine:
    """
    管理项目生命周期状态流转的有限状态机 (Finite State Machine)。
    支持状态校验、自动推进和回溯 (Rollback)。
    """
    
    # 允许的流转路径 (Adjacency List)
    TRANSITIONS = {
        ProjectPhase.INIT: [ProjectPhase.IDEATION],
        ProjectPhase.IDEATION: [ProjectPhase.OUTLINE],
        ProjectPhase.OUTLINE: [ProjectPhase.BIBLE, ProjectPhase.IDEATION], # 允许回退到创意
        ProjectPhase.BIBLE: [ProjectPhase.SCENE_PLAN, ProjectPhase.OUTLINE], # 允许回退到大纲
        ProjectPhase.SCENE_PLAN: [ProjectPhase.DRAFTING, ProjectPhase.BIBLE, ProjectPhase.OUTLINE], # 允许回退
        ProjectPhase.DRAFTING: [ProjectPhase.REVIEW, ProjectPhase.SCENE_PLAN],
        ProjectPhase.REVIEW: [ProjectPhase.DONE, ProjectPhase.DRAFTING],
        ProjectPhase.DONE: []
    }

    def __init__(self, state: ProjectState):
        self.state = state
        self.log = logging.getLogger("StateMachine")

    @property
    def current_phase(self) -> ProjectPhase:
        try:
            return ProjectPhase(self.state.step)
        except ValueError:
            # 如果 state.step 存的是旧字符串或不匹配，默认回退安全值
            return ProjectPhase.INIT

    def can_transition(self, target: ProjectPhase) -> bool:
        """检查是否可以流转到目标状态"""
        current = self.current_phase
        allowed = self.TRANSITIONS.get(current, [])
        return target in allowed

    def transition_to(self, target: ProjectPhase, force: bool = False):
        """执行状态流转"""
        if not force and not self.can_transition(target):
            raise ValueError(f"非法状态流转: {self.current_phase} -> {target}")
        
        self.log.info(f"状态流转: {self.current_phase} -> {target}")
        self.state.step = target.value
        self.state.save()

    def get_available_actions(self) -> List[str]:
        """获取当前状态下可执行的动作 (用于 UI 显示)"""
        current = self.current_phase
        if current == ProjectPhase.INIT:
            return ["start_ideation"]
        
        elif current == ProjectPhase.IDEATION:
            return ["run_ideation", "finalize_ideation"]
        
        elif current == ProjectPhase.OUTLINE:
            return ["run_outline", "finalize_outline", "back_to_ideation"]
        
        elif current == ProjectPhase.BIBLE:
            return ["run_bible", "finalize_bible", "back_to_outline"]
        
        elif current == ProjectPhase.SCENE_PLAN:
            return ["init_scenes", "finalize_scene_plan", "back_to_bible"]
        
        elif current == ProjectPhase.DRAFTING:
            return ["resume_drafting", "review_progress", "back_to_scene_plan"]
        
        elif current == ProjectPhase.REVIEW:
            return ["finalize_project", "back_to_drafting"]
            
        elif current == ProjectPhase.DONE:
            return ["view_result", "export", "back_to_review"]
            
        return []
