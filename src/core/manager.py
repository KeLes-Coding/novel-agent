# src/core/manager.py
import os
import datetime
import uuid
import yaml
import re
import json
from typing import Dict, Any, Optional, List

from utils.logger import RunContext, setup_loggers, LogAdapter, log_event
from utils.trace_logger import TraceLogger, TracingProvider
from providers.factory import build_provider
from storage.local_store import LocalStore
from core.state import ProjectState, SceneNode, ArtifactCandidate
import core.fsm as fsm_lib
from core.fsm import ProjectPhase

from pipeline.step_01_ideation import run as run_ideation
from pipeline.step_02_outline import run as run_outline
from pipeline.step_03_bible import run as run_bible

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

        self.fsm = fsm_lib.StateMachine(self.state)

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
            target = fsm_lib.ProjectPhase(target_phase_str)
        except ValueError:
            self.interface.notify("错误", f"无效的阶段名称: {target_phase_str}")
            return

        if self.fsm.can_transition(target):
            self.log.warning(f"正在执行回退操作: {self.fsm.current_phase.value} -> {target.value}")
            self.fsm.transition_to(target)
            self.interface.notify("回退成功", f"当前阶段已重置为: {target.value}")
        else:
            self.interface.notify("错误", f"无法回退到 {target.value}，状态机不允许此流转。")

    def _is_step_executed(self, phase_name: str) -> bool:
        if phase_name == "ideation":
            return bool(self.state.idea_path)
        elif phase_name == "outline":
            return bool(self.state.outline_path)
        elif phase_name == "bible":
            return bool(self.state.bible_path)
        elif phase_name == "scene_plan":
            return bool(self.state.scenes)
        elif phase_name == "drafting":
            return any(s.status == "done" and self.state._abs_path_exists(s.content_path) for s in self.state.scenes)
        elif phase_name == "review":
            for s in self.state.scenes:
                if s.status == "done" and "06_polishing" in str(s.content_path) and os.path.exists(s.content_path):
                    return True
            return False
        return False

    def _prompt_rewrite(self, phase_name: str, reset_callback) -> bool:
        """
        Check if the phase has been executed. If so, prompt the user.
        Return True if we should proceed with generation (either brand new, or user chose to rewrite).
        Return False if the user chose to skip (so we should just transition to the next state).
        """
        if not self._is_step_executed(phase_name):
            return True
            
        choice = self.interface.ask_choice(
            f"检测到阶段 [{phase_name}] 已经执行过或有历史数据。\n请选择操作:",
            ["跳过 (Skip) - 保持当前数据并进入下一阶段", "重写 (Rewrite) - 清除记录并重新生成"]
        )
        if choice == 0:
            self.log.info(f"用户选择跳过阶段: {phase_name}")
            return False
        else:
            if self.interface.confirm(f"警告：重写将丢弃 [{phase_name}] 的现有数据，确定继续？"):
                self.log.info(f"用户选择重写阶段: {phase_name}。正在清理数据...")
                reset_callback()
                return True
            else:
                 self.log.info(f"用户取消重写。跳过阶段: {phase_name}")
                 return False

    def _reset_ideation(self):
        self.state.idea_path = ""
        self.state.idea_candidates = []
        import shutil
        dir_path = self.store._abs("01_ideation")
        if os.path.exists(dir_path): shutil.rmtree(dir_path)
        self.state.save()

    def _reset_outline(self):
        self.state.outline_path = ""
        self.state.outline_candidates = []
        import shutil
        dir_path = self.store._abs("02_outline")
        if os.path.exists(dir_path): shutil.rmtree(dir_path)
        self.state.save()

    def _reset_bible(self):
        self.state.bible_path = ""
        self.state.bible_candidates = []
        import shutil
        dir_path = self.store._abs("03_bible")
        if os.path.exists(dir_path): shutil.rmtree(dir_path)
        self.state.save()

    def _reset_scene_plan(self):
        self.state.scenes = []
        self.state.scene_plan_path = ""
        self.state.scene_plan_candidates = []
        import shutil
        dir_path = self.store._abs("04_scene_plan")
        if os.path.exists(dir_path): shutil.rmtree(dir_path)
        self.state.save()

    def _reset_drafting(self):
        for s in self.state.scenes:
            s.status = "pending"
            s.content_path = ""
            s.candidates = []
        import shutil
        dir_path = self.store._abs("05_drafting")
        if os.path.exists(dir_path): shutil.rmtree(dir_path)
        self.state.save()

    def _reset_review(self):
        import shutil
        dir_path = self.store._abs("06_polishing")
        if os.path.exists(dir_path): shutil.rmtree(dir_path)
        # We don't change scene status back to pending, they remain 'done' but we removed the polished files.
        # Fallback mechanism will kick in next time review is run, reading from drafting.
        # However, to be fully clean, we should clear the critique refs from the current scene.content_path if it points to polishing.
        for s in self.state.scenes:
             if s.content_path and "06_polishing" in s.content_path:
                 s.content_path = "" # Force fallback
        self.state.save()

    def execute_next_step(self):
        current = self.fsm.current_phase
        self.log.info(f"当前阶段: {current.value}")
        
        if current == fsm_lib.ProjectPhase.INIT:
            self.fsm.transition_to(fsm_lib.ProjectPhase.IDEATION)
            self.run_ideation()
        elif current == fsm_lib.ProjectPhase.IDEATION:
            self.run_ideation()
        elif current == fsm_lib.ProjectPhase.OUTLINE:
            self.run_outline()
        elif current == fsm_lib.ProjectPhase.BIBLE:
            self.run_bible()
        elif current == fsm_lib.ProjectPhase.SCENE_PLAN:
            self.init_scenes()
        elif current == fsm_lib.ProjectPhase.DRAFTING:
            self.run_drafting_loop(auto_mode=True)
        elif current == fsm_lib.ProjectPhase.REVIEW:
            self.run_review()
        elif current == fsm_lib.ProjectPhase.EXPORT:
            self.run_export()
        elif current == fsm_lib.ProjectPhase.DONE:
            self.interface.notify("完成", "项目已完成。")

    # --- Specific Steps ---

    def run_ideation(self, force: bool = False):
        if not force and not self._prompt_rewrite("ideation", self._reset_ideation):
            self.fsm.transition_to(fsm_lib.ProjectPhase.OUTLINE)
            return
            
        self.fsm.transition_to(fsm_lib.ProjectPhase.IDEATION, force=True)
        step_name = "ideation"
        log = self._get_workflow(step_name).log
        workflow = self._get_workflow(step_name)

        user_input_mode = 0
        if not self.state.idea_candidates and not self.state.idea_path:
            user_input_mode = self.interface.ask_choice(
                "准备开始生成创意，请选择创意的提供方式:",
                ["由 AI 自由头脑风暴生成 (自动模式)", "由我提供一个初步的想法 (作为核心灵感附加给 AI)", "直接输入完整的创意文本 (完全跳过 AI 生成)"]
            )
            
            if user_input_mode == 1:
                user_idea = self.interface.prompt_multiline("请输入您的初步想法/灵感")
                if "content" not in self.config:
                    self.config["content"] = {}
                self.config["content"]["user_prompt"] = user_idea
            elif user_input_mode == 2:
                user_idea = self.interface.prompt_multiline("请输入完整的创意内容 (此步骤后将直接进入大纲生成)")
                final_path = self.store.save_text("01_ideation/ideas_selected.txt", user_idea)
                self.state.idea_path = final_path
                self.state.save()
                log.info(f"人工创意已确认，直接进入下一阶段: {final_path}")
                self.fsm.transition_to(fsm_lib.ProjectPhase.OUTLINE)
                return

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
        
        # 推进到下一阶段
        self.fsm.transition_to(fsm_lib.ProjectPhase.OUTLINE)

    def run_outline(self, force: bool = False):
        if not force and not self._prompt_rewrite("outline", self._reset_outline):
            self.fsm.transition_to(fsm_lib.ProjectPhase.BIBLE)
            return
            
        self.fsm.transition_to(fsm_lib.ProjectPhase.OUTLINE, force=True)
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
        
        # 推进到下一阶段
        self.fsm.transition_to(fsm_lib.ProjectPhase.BIBLE)

    def run_bible(self, force: bool = False):
        if not force and not self._prompt_rewrite("bible", self._reset_bible):
            self.fsm.transition_to(fsm_lib.ProjectPhase.SCENE_PLAN)
            return
            
        self.fsm.transition_to(fsm_lib.ProjectPhase.BIBLE, force=True)
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
        
        # 推进到下一阶段
        self.fsm.transition_to(fsm_lib.ProjectPhase.SCENE_PLAN)

    def init_scenes(self, force: bool = False):
        if not force and not self._prompt_rewrite("scene_plan", self._reset_scene_plan):
            self.fsm.transition_to(fsm_lib.ProjectPhase.DRAFTING)
            return

        self.fsm.transition_to(fsm_lib.ProjectPhase.SCENE_PLAN, force=True)
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
        
        # 推进到下一阶段
        self.fsm.transition_to(fsm_lib.ProjectPhase.DRAFTING)

    def run_drafting_loop(self, force: bool = False, auto_mode: bool = False):
        if not force and not self._prompt_rewrite("drafting", self._reset_drafting):
            self.fsm.transition_to(fsm_lib.ProjectPhase.REVIEW)
            return

        self.fsm.transition_to(fsm_lib.ProjectPhase.DRAFTING, force=True)
        step_name = "drafting"
        self.workflow = self._get_workflow(step_name)
        log = self.workflow.log
        
        if not self.state.scenes:
            self.interface.notify("提示", "未找到场景信息，请先运行 init_scenes。")
            return

        from core.context import ContextBuilder
        from agents.wiki_updater import WikiUpdater
        from core.memory import MemoryManager
        self.ctx_builder = ContextBuilder(self.state, self.store, self.config)
        self.wiki_updater = WikiUpdater(self.provider, self.prompts.get("global_system", ""))
        self.memory = MemoryManager(self.state, self.wiki_updater, self.log)
        self.jsonl = self.logger_env["jsonl"]

        # 遍历所有根节点 (及其子节点)
        for i, scene_node in enumerate(self.state.scenes):
             self._process_scene_recursive(scene_node, auto_mode)
                 
        self.interface.notify("完成", "正文生成循环结束 (包含所有选中分支)。")
        
        # 推进到下一阶段
        self.fsm.transition_to(fsm_lib.ProjectPhase.REVIEW)

    def run_review(self, force: bool = False):
        if not force and not self._prompt_rewrite("review", self._reset_review):
            self.fsm.transition_to(fsm_lib.ProjectPhase.EXPORT)
            return

        self.fsm.transition_to(fsm_lib.ProjectPhase.REVIEW, force=True)
        self.log.info("进入 Review 阶段: 开始自动润色与审阅...")
        
        done_scenes = [s for s in self.state.scenes if s.status == "done"]
        import os
        
        valid_scenes = []
        for s in done_scenes:
            if s.content_path and os.path.exists(s.content_path):
                valid_scenes.append(s)
            else:
                fallback_json = self.store._abs(f"05_drafting/scenes/scene_{s.id:03d}.json")
                fallback_md = getattr(s, "fallback_md", self.store._abs(f"05_drafting/scenes/scene_{s.id:03d}_{s.selected_candidate_id}.json") if s.selected_candidate_id else "")

                if os.path.exists(fallback_json):
                    s.content_path = fallback_json
                    valid_scenes.append(s)
                elif fallback_md and os.path.exists(fallback_md):
                    s.content_path = fallback_md
                    valid_scenes.append(s)
                else:
                    self.log.warning(f"Scene {s.id} is marked done but no valid drafted files found. Cannot review. Consider rerolling drafting for this scene.")
                    # 触发状态回拨
                    s.status = "pending"
                     
        if not valid_scenes:
            self.log.warning("没有可供 Review 的有效文件。")
            self.fsm.transition_to(fsm_lib.ProjectPhase.EXPORT)
            return

        done_scenes = valid_scenes
        self.workflow = self._get_workflow("review")
        
        count = 0
        total = len(done_scenes)
        
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        # Determine number of workers based on config or default to 3
        max_workers = self.config.get("workflow", {}).get("max_parallel_reviews", 3)
        self.log.info(f"Starting parallel review with {max_workers} workers.")
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(self.workflow.run_polish_cycle, scene): scene for scene in done_scenes}
            
            for i, future in enumerate(as_completed(futures)):
                scene = futures[future]
                self.log.info(f"[{i+1}/{total}] Completed Review for Scene {scene.id}")
                try:
                    if future.result():
                        count += 1
                except Exception as e:
                    self.log.error(f"Failed to polish scene {scene.id}: {e}")
        
        if count > 0:
            self.state.save()
            self.interface.notify("Review 完成", f"已对 {count} 个场景进行了自动润色。")
        else:
            self.log.info("Review 结束，未触发任何润色操作 (可能 auto_polish=False 或所有步骤均跳过)。")

        self.log.info("Review 阶段完成，进入 EXPORT。")
        self.fsm.transition_to(fsm_lib.ProjectPhase.EXPORT)
        self.execute_next_step()

    def run_export(self):
        """
        导出阶段：将所有完成的场景合并为完整的 Markdown 和 TXT 文件
        """
        self.fsm.transition_to(fsm_lib.ProjectPhase.EXPORT, force=True)
        self.log.info("========== EXPORT 阶段开始 ==========")
        export_dir = "07_export"
        os.makedirs(self.store._abs(export_dir), exist_ok=True)
        
        done_scenes = [s for s in self.state.scenes if s.status == "done"]
        if not done_scenes:
            self.log.warning("没有已完成的场景可以导出。")
            self.fsm.transition_to(fsm_lib.ProjectPhase.DONE)
            return
            
        done_scenes.sort(key=lambda s: s.id)
        
        import re
        scenes_export_dir = f"{export_dir}/scenes"
        os.makedirs(self.store._abs(scenes_export_dir), exist_ok=True)
        
        full_text = []
        for scene in done_scenes:
            rel_polish_json = f"06_polishing/scenes/scene_{scene.id:03d}.json"
            rel_drafting_json = f"05_drafting/scenes/scene_{scene.id:03d}.json"
            
            content_data = None
            if os.path.exists(self.store._abs(rel_polish_json)):
                content_data = self.store.load_json(rel_polish_json)
            elif os.path.exists(self.store._abs(rel_drafting_json)):
                content_data = self.store.load_json(rel_drafting_json)
            else:
                self.log.warning(f"无法找到场景 {scene.id} 的 json 文件，跳过此章。")
                continue
                
            if content_data:
                title = content_data.get("title", f"第{scene.id}章")
                content = content_data.get("content", "")
                
                # Check if it's actually the "全书分场表" (in case it wasn't caught by the bugfix during generation)
                if title in ["全书分场表", "全书分场表 (Scene Plan)", "Scene Plan"]:
                    continue
                
                # 清洗正文
                match = re.search(r"正文[:：\n](.*)", content, re.DOTALL)
                if match:
                    content = match.group(1).strip()
                else:
                    content = re.sub(r"^(?:【写作指导】|【细纲】|【本章任务】|【.*?提示】).*?(?:\n\n|\n$)", "", content, flags=re.DOTALL)
                    content = content.strip()
                
                chapter_text = f"## {title}\n\n{content}\n"
                full_text.append(chapter_text)
                
                # 导出独立的章节文件
                scene_md_path = f"{scenes_export_dir}/chapter_{scene.id:03d}.md"
                self.store.save_text(scene_md_path, f"# {title}\n\n{content}")
                
        final_md_path = f"{export_dir}/full_novel.md"
        final_txt_path = f"{export_dir}/full_novel.txt"
        
        combined_text = "\n".join(full_text)
        self.store.save_text(final_md_path, combined_text)
        self.store.save_text(final_txt_path, combined_text)
        
        self.log.info(f"最终小说已导出至 {final_md_path} 和 {final_txt_path} (含独立章节文件)")
        self.interface.notify("导出完成", f"最终稿和独立章节已保存至 {self.store._abs(export_dir)}")
        
        self.fsm.transition_to(fsm_lib.ProjectPhase.DONE)
        self.state.save()
        self.log.info("导出操作已完成，项目完结。")

    def _process_scene_recursive(self, scene_node: SceneNode, auto_mode: bool):
        """递归处理场景节点 (支持分支选择)"""
        
        # 1. 如果已完成，跳过
        # 1. 如果已完成，跳过
        if scene_node.status == "done":
            if scene_node.content_path and os.path.exists(scene_node.content_path):
                self.log.info(f"场景 {scene_node.title} 已完成，跳过。")
                # 仍然需要递归处理子分支，因为可能父节点完成了但子分支没完成
                self._handle_branches(scene_node, auto_mode)
                return
            else:
                 self.log.info(f"场景 {scene_node.title} 状态为 done 但文件缺失，重新生成。")
                 scene_node.status = "pending"

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
                new_bible_path = self.wiki_updater.patch_bible(
                    self.state.bible_path, 
                    new_facts, 
                    scene_node.title,
                    branch_id=str(scene_node.id)
                )
                self.log.info(f"Bible patched: {new_bible_path}")
            
            # 2.2 触发记忆归档
            self.memory.consolidate_memory(scene_node.id)
            
        except Exception as e:
            self.log.error(f"场景 {scene_node.id} 处理失败: {e}")
            raise e

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
                
                if title in ["全书分场表", "全书分场表 (Scene Plan)", "Scene Plan"]:
                    continue
                
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
