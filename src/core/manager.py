# src/core/manager.py
import os
import datetime
import uuid
import yaml
import re
from typing import Dict, Any, Optional, List

from utils.logger import RunContext, setup_loggers, LogAdapter, log_event
from utils.trace_logger import TraceLogger, TracingProvider
from providers.factory import build_provider
from storage.local_store import LocalStore
from core.state import ProjectState, SceneNode, ArtifactCandidate
from core.fsm import StateMachine, ProjectPhase

from pipeline.step_01_ideation import run as run_ideation
from pipeline.step_02_outline import run as run_outline
from pipeline.step_03_bible import run as run_bible
from pipeline.step_05_drafting import draft_single_scene

from core.workflow import WorkflowEngine
from interfaces.base import UserInterface

class ProjectManager:
    def __init__(self, config_path: str, interface: UserInterface, run_id: Optional[str] = None):
        self.config = self._load_yaml(config_path)
        self.prompts = self._load_yaml("config/prompts.yaml")
        self.interface = interface

        runs_dir = self.config["output"]["runs_dir"]

        if run_id:
            self.run_id = run_id
            self.run_dir = None
            if os.path.exists(os.path.join(runs_dir, run_id)):
                self.run_dir = os.path.join(runs_dir, run_id)
            else:
                for entry in os.listdir(runs_dir):
                    if run_id in entry:
                        full_path = os.path.join(runs_dir, entry)
                        if os.path.isdir(full_path):
                            self.run_dir = full_path
                            break
                    candidate_sub = os.path.join(runs_dir, entry, run_id)
                    if os.path.exists(candidate_sub):
                        self.run_dir = candidate_sub
                        break

            if not self.run_dir:
                raise ValueError(f"Run ID {run_id} not found in {runs_dir}")

            self.state = ProjectState.load(self.run_dir)
            self.logger_env = self._setup_logging(resume=True)
            self.log.info(f"已加载项目: {run_id}")
        else:
            now_str = datetime.datetime.now().strftime("%Y-%m-%d/%H-%M-%S")
            short_uid = uuid.uuid4().hex[:8]
            self.run_id = f"{now_str}_{short_uid}"

            self.run_dir = os.path.join(runs_dir, self.run_id)
            os.makedirs(self.run_dir, exist_ok=True)

            self.state = ProjectState(run_id=self.run_id, run_dir=self.run_dir)
            self.state.step = ProjectPhase.INIT.value
            self.state.save()

            self.logger_env = self._setup_logging(resume=False)
            self.log.info(f"初始化新项目: {self.run_id}")

        self.fsm = StateMachine(self.state)

        self.store = LocalStore(self.run_dir)
        trace_path = os.path.join(self.run_dir, "logs", "llm_trace.jsonl")
        self.tracer = TraceLogger(trace_path)
        raw_provider = build_provider(self.config)
        get_step = lambda: self.state.step
        self.provider = TracingProvider(raw_provider, self.tracer, self.run_id, get_step)

    @property
    def log(self):
        base_logger = self.logger_env["logger"]
        return LogAdapter(base_logger, {"run_id": self.run_id, "step": "manager"})

    def _load_yaml(self, path: str) -> Dict[str, Any]:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _setup_logging(self, resume: bool):
        ctx = RunContext(
            run_id=self.run_id,
            run_dir=self.run_dir,
            level=self.config["logging"]["level"],
            jsonl_events=self.config["logging"]["jsonl_events"],
        )
        return setup_loggers(ctx)

    def _get_workflow(self, step_name: str):
        return WorkflowEngine({
            "cfg": self.config,
            "prompts": self.prompts,
            "provider": self.provider,
            "store": self.store,
            "log": LogAdapter(self.logger_env["logger"], {"run_id": self.run_id, "step": step_name}),
            "jsonl": self.logger_env["jsonl"],
            "run_id": self.run_id,
            "state": self.state,
            "interface": self.interface,
        })

    def rollback(self, target_phase_str: str):
        try:
            target = ProjectPhase(target_phase_str)
        except ValueError:
            self.interface.notify("错误", f"无效的阶段名称: {target_phase_str}")
            return

        if self.fsm.can_transition(target):
            self.log.warning(f"正在执行回退操作: {self.fsm.current_phase.value} -> {target.value}")
            self.fsm.transition_to(target)
            self.interface.notify("回退成功", f"当前阶段已重置为: {target.value}")
        else:
            self.interface.notify("错误", f"无法回退到 {target.value}，状态机不允许此流转。")

    def execute_next_step(self):
        current = self.fsm.current_phase
        self.log.info(f"当前阶段: {current.value}")
        
        if current == ProjectPhase.INIT:
            self.fsm.transition_to(ProjectPhase.IDEATION)
            self.run_ideation()
        elif current == ProjectPhase.IDEATION:
            if not self.state.idea_path:
                self.run_ideation()
            else:
                self.fsm.transition_to(ProjectPhase.OUTLINE)
        elif current == ProjectPhase.OUTLINE:
            if not self.state.outline_path:
                self.run_outline()
            else:
                self.fsm.transition_to(ProjectPhase.BIBLE)
        elif current == ProjectPhase.BIBLE:
            if not self.state.bible_path:
                self.run_bible()
            else:
                self.fsm.transition_to(ProjectPhase.SCENE_PLAN)
        elif current == ProjectPhase.SCENE_PLAN:
            if not self.state.scenes:
                self.init_scenes()
            else:
                self.fsm.transition_to(ProjectPhase.DRAFTING)
        elif current == ProjectPhase.DRAFTING:
            self.run_drafting_loop(auto_mode=True)
        elif current == ProjectPhase.DONE:
            self.interface.notify("完成", "项目已完成。")

    # --- Specific Steps ---

    def run_ideation(self, force: bool = False):
        self.fsm.transition_to(ProjectPhase.IDEATION, force=True)
        step_name = "ideation"
        log = self._get_workflow(step_name).log
        workflow = self._get_workflow(step_name)

        log.info("开始创意生成...")

        def _generate_ideas() -> list:
            ctx = {"cfg": self.config, "prompts": self.prompts, "provider": self.provider, "store": self.store, "log": log}
            res = run_ideation(ctx)
            raw = res.get("candidates_list", [])
            if not raw:
                 full_text = res.get("idea_text", "")
                 raw = [full_text] if full_text else []
            return [ArtifactCandidate(id=f"v{i+1}", content=text) for i, text in enumerate(raw)]

        selected = workflow.run_step_with_hitl("ideation", _generate_ideas, "idea_candidates", "idea_path")
        
        final_path = self.store.save_text("01_ideation/ideas_selected.txt", selected.content)
        self.state.idea_path = final_path
        self.state.save()
        log.info(f"创意已确认: {final_path}")

    def run_outline(self, force: bool = False):
        self.fsm.transition_to(ProjectPhase.OUTLINE, force=True)
        step_name = "outline"
        workflow = self._get_workflow(step_name)
        log = workflow.log

        if not self.state.idea_path:
            self.interface.notify("错误", "缺少创意文件 (Idea Path)，无法生成大纲。")
            return

        def _generate() -> list:
            ctx = {"cfg": self.config, "prompts": self.prompts, "provider": self.provider, "store": self.store, "idea_path": self.state.idea_path, "log": log}
            res = run_outline(ctx)
            raw = res.get("candidates_list", [])
            if not raw:
                 val = res.get("outline_text", "")
                 raw = [val] if val else []
            return [ArtifactCandidate(id=f"v{i+1}", content=t) for i, t in enumerate(raw)]

        selected = workflow.run_step_with_hitl("outline", _generate, "outline_candidates", "outline_path")
        self.state.outline_path = self.store.save_text("02_outline/outline_selected.md", selected.content)
        self.state.save()
        log.info("大纲已确认。")

    def run_bible(self, force: bool = False):
        self.fsm.transition_to(ProjectPhase.BIBLE, force=True)
        step_name = "bible"
        workflow = self._get_workflow(step_name)
        log = workflow.log
        
        if not self.state.outline_path:
            self.interface.notify("错误", "缺少大纲文件，无法生成设定集。")
            return

        def _generate() -> list:
            ctx = {"cfg": self.config, "prompts": self.prompts, "provider": self.provider, "store": self.store, "outline_path": self.state.outline_path, "log": log}
            res = run_bible(ctx)
            raw = res.get("candidates_list", [])
            if not raw:
                 val = res.get("bible_text", "")
                 raw = [val] if val else []
            return [ArtifactCandidate(id=f"v{i+1}", content=t) for i, t in enumerate(raw)]

        selected = workflow.run_step_with_hitl("bible", _generate, "bible_candidates", "bible_path")
        self.state.bible_path = self.store.save_text("03_bible/bible_selected.md", selected.content)
        self.state.save()
        log.info("设定集已确认。")

    def init_scenes(self, force: bool = False):
        self.fsm.transition_to(ProjectPhase.SCENE_PLAN, force=True)
        step_name = "scene_plan"
        workflow = self._get_workflow(step_name)
        log = workflow.log

        if not self.state.outline_path:
            self.interface.notify("错误", "缺少大纲，无法生成分场。")
            return

        def _generate() -> list:
            ctx = {"cfg": self.config, "prompts": self.prompts, "provider": self.provider, "store": self.store, "outline_path": self.state.outline_path, "bible_path": self.state.bible_path, "log": log}
            from pipeline.step_04_scene_plan import run as pipe
            res = pipe(ctx)
            raw = res.get("candidates_list", [])
            if not raw:
                 val = res.get("scene_plan_text", "")
                 raw = [val] if val else []
            return [ArtifactCandidate(id=f"v{i+1}", content=t) for i, t in enumerate(raw)]

        selected = workflow.run_step_with_hitl("scene_plan", _generate, "scene_plan_candidates", "scene_plan_path")
        self.state.scene_plan_path = self.store.save_text("04_scene_plan/scene_plan_selected.md", selected.content)
        
        scenes = self._parse_scene_plan_text(selected.content)
        self.state.scenes = scenes
        self.state.save()
        log.info(f"分场已确认，包含 {len(scenes)} 个根场景。")

    def run_drafting_loop(self, force: bool = False, auto_mode: bool = False):
        self.fsm.transition_to(ProjectPhase.DRAFTING, force=True)
        step_name = "drafting"
        self.workflow = self._get_workflow(step_name)
        log = self.workflow.log
        
        if not self.state.scenes:
            self.interface.notify("提示", "未找到场景信息，请先运行 init_scenes。")
            return

        from core.context import ContextBuilder
        from agents.wiki_updater import WikiUpdater
        self.ctx_builder = ContextBuilder(self.state, self.store)
        self.wiki_updater = WikiUpdater(self.provider, self.prompts.get("global_system", ""))
        self.jsonl = self.logger_env["jsonl"]

        # 遍历所有根节点 (及其子节点)
        for i, scene_node in enumerate(self.state.scenes):
             self._process_scene_recursive(scene_node, auto_mode)
                 
        self.interface.notify("完成", "正文生成循环结束 (包含所有选定分支)。")
        self.fsm.transition_to(ProjectPhase.REVIEW)

    def _process_scene_recursive(self, scene_node: SceneNode, auto_mode: bool):
        """递归处理场景节点 (支持分支选择)"""
        
        # 1. 如果已完成，跳过
        if scene_node.status == "done":
            self.log.info(f"场景 {scene_node.title} 已完成，跳过。")
            # 仍然需要递归处理子分支，因为可能父节点完成了但子分支没完成
            self._handle_branches(scene_node, auto_mode)
            return

        self.log.info(f"正在处理场景 {scene_node.id}: {scene_node.title} ...")
        
        # 2. 生成正文
        try:
            # 构建 Context
            build_res = self.ctx_builder.build(scene_node.id)
            dynamic_ctx = build_res["payload"]
            
            # Inject chapter_words for prompt
            avg_chapter_words = self.config.get("content", {}).get("length", {}).get("avg_chapter_words", 3000)
            dynamic_ctx["chapter_words"] = avg_chapter_words
            
            scene_node.meta["dynamic_context"] = dynamic_ctx
            
            # 执行生成 (WorkflowEngine)
            self.workflow.process_scene(scene_node, self.state.outline_path, self.state.bible_path)
            
            # 后处理 (摘要与保存)
            final_text = ""
            if scene_node.content_path.endswith(".json"):
                with open(scene_node.content_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    final_text = data.get("content", "")
            else:
                with open(scene_node.content_path, "r", encoding="utf-8") as f:
                    final_text = f.read()
            
            # Piggyback Extraction: Summary + New Facts
            analysis = self.wiki_updater.analyze_scene(final_text)
            
            scene_node.summary = analysis.get("summary", "Summary failed.")
            new_facts = analysis.get("new_facts", [])
            
            self.state.save()
            
            # 2.1 触发动态设定更新 (Dynamic Bible Update)
            if new_facts:
                self.log.info(f"Scene {scene_node.id} triggered bible update with {len(new_facts)} new facts.")
                self.wiki_updater.patch_bible(
                    self.state.bible_path, 
                    new_facts, 
                    scene_node.title
                )
            
            # 2.2 触发记忆归档
            self._consolidate_memory(scene_node.id)
            
        except Exception as e:
            self.log.error(f"场景 {scene_node.id} 处理失败: {e}")
            raise e

    def _consolidate_memory(self, current_scene_id: int):
        """
        Check if we need to consolidate old scene summaries into archive.
        Buffer: Keep last 5 scenes active. Archive scenes before that in batches of 5.
        """
        # Buffer size = 5. We need at least 10 scenes since last archive to trigger a new archive of 5.
        # Actually, let's keep it simple:
        # If (current_scene_id - last_archived) >= 10:
        #    Archive range: [last_archived + 1, last_archived + 5]
        #    New last_archived = last_archived + 5
        
        last_archived = self.state.last_archived_scene_id
        if (current_scene_id - last_archived) >= 10:
            start_id = last_archived + 1
            end_id = last_archived + 5
            
            self.log.info(f"Consolidating memory for scenes {start_id} to {end_id} ...")
            
            # Find these scenes
            # Note: self.state.scenes is a list, but IDs might not be continuous indices if we have branches.
            # However, for linear history (archived memory), we usually track the 'main timeline'.
            # Simplification: We only archive linear segments. 
            # Or we just find scenes by ID if IDs are globally unique and sequential-ish.
            
            scenes_to_archive = []
            for sid in range(start_id, end_id + 1):
                # Find scene with this ID
                # FIXME: This linear search is slow for large N, but N is small for now.
                # Also, we need to handle if scene ID doesn't exist (e.g. skipped numbers?)
                # Assumes scenes have sequential IDs for now.
                node = next((s for s in self.state.scenes if s.id == sid), None)
                if node and node.summary:
                    scenes_to_archive.append(node.summary)
                else:
                    self.log.warning(f"Scene {sid} not found or missing summary during consolidation.")
            
            if scenes_to_archive:
                chapter_summary = self.wiki_updater.consolidate_summaries(scenes_to_archive)
                self.state.archived_summaries.append(chapter_summary)
                self.state.last_archived_scene_id = end_id
                self.state.save()
                self.log.info(f"Memory consolidated. New archive count: {len(self.state.archived_summaries)}")

                self.state.save()
                self.log.info(f"Memory consolidated. New archive count: {len(self.state.archived_summaries)}")

    def _handle_branches(self, scene_node: SceneNode, auto_mode: bool):
        """处理子分支选择与递归"""
        if not scene_node.branches:
            return

        self.log.info(f"场景 {scene_node.title} 存在 {len(scene_node.branches)} 个后续分支。")
        
        selected_branch = None
        
        # 自动模式下，默认选择第一个分支，避免阻塞
        if auto_mode:
            self.log.info(f"自动模式: 默认选择第一个分支 ({scene_node.branches[0].title})")
            selected_branch = scene_node.branches[0]
        else:
            options = [f"{b.title} (ID: {b.id}) - {b.meta.get('preconditions', '')}" for b in scene_node.branches]
            descriptions = [b.summary[:50] + "..." for b in scene_node.branches]
            
            # 使用 Interface 询问
            choice_idx = self.interface.ask_choice(
                f"分支点: {scene_node.title} 结束。\n请选择接下来的剧情走向:",
                options,
                descriptions
            )
            
            selected_branch = scene_node.branches[choice_idx]
        
        # 递归处理选定的分支
        self.log.info(f"进入分支: {selected_branch.title}")
        self._process_scene_recursive(selected_branch, auto_mode)

    def _parse_scene_plan_text(self, text: str) -> List[SceneNode]:
        """
        解析支持分支结构的场景大纲。
        格式:
        # 1. 主场景
        > ...
            ## 1.1 分支 A
            > Precondition: 选择 A
        """
        scenes: List[SceneNode] = []
        stack: List[SceneNode] = [] 
        
        pattern = r"^(#+)\s*(?:(\d+(?:\.\d+)*)\.?\s*)?(.*)$"
        
        lines = text.split("\n")
        current_node: Optional[SceneNode] = None
        auto_id_counter = 1
        
        for line in lines:
            line = line.strip()
            if not line:
                continue

            match = re.match(pattern, line)
            if match:
                level_marker = match.group(1)
                user_id_str = match.group(2)
                title = match.group(3).strip()
                
                level = len(level_marker) - 1
                
                new_node = SceneNode(
                    id=auto_id_counter,
                    title=title,
                    status="pending",
                    meta={"display_id": user_id_str, "level": level}
                )
                auto_id_counter += 1
                
                if level == 0:
                    scenes.append(new_node)
                    stack = [new_node]
                else:
                    if level <= len(stack):
                        parent = stack[level - 1]
                        new_node.parent_id = parent.id
                        parent.branches.append(new_node)
                        stack = stack[:level] + [new_node]
                    else:
                        if stack:
                            parent = stack[-1]
                            new_node.parent_id = parent.id
                            parent.branches.append(new_node)
                            stack.append(new_node)
                        else:
                            scenes.append(new_node)
                            stack = [new_node]

                current_node = new_node
                
            elif current_node:
                if line.startswith("> 梗概：") or line.startswith("> Summary:"):
                    current_node.summary = line.split("：", 1)[-1].strip()
                elif line.startswith("> Precondition:") or line.startswith("> 前置条件:"):
                    cond = line.split(":", 1)[-1].strip()
                    current_node.preconditions = cond
                    current_node.meta["preconditions"] = cond
                elif line.startswith(">"):
                    current_node.summary += "\n" + line.lstrip("> ").strip()

        return scenes
