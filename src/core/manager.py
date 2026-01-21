# src/core/manager.py
import os
import datetime
import uuid
import yaml
import re  # === 新增：确保引入正则模块 ===
from typing import Dict, Any, Optional, List

from utils.logger import RunContext, setup_loggers, StepTimer, LogAdapter, log_event
from utils.trace_logger import TraceLogger, TracingProvider
from utils.hashing import sha256_text, sha256_file
from providers.factory import build_provider
from storage.local_store import LocalStore
from core.state import ProjectState, SceneNode, ArtifactCandidate

# 引入 Pipeline Steps
from pipeline.step_01_ideation import run as run_ideation
from pipeline.step_02_outline import run as run_outline
from pipeline.step_03_bible import run as run_bible

# === 修改点：移除 generate_scene_plan，只保留 draft_single_scene ===
from pipeline.step_04_drafting import draft_single_scene

from utils.graph_parser import GraphParser
from core.workflow import WorkflowEngine


class ProjectManager:
    def __init__(self, config_path: str, run_id: Optional[str] = None):
        self.config = self._load_yaml(config_path)
        self.prompts = self._load_yaml("config/prompts.yaml")

        runs_dir = self.config["output"]["runs_dir"]

        if run_id:
            # === 加载逻辑优化 ===
            # 搜索匹配的文件夹
            self.run_id = run_id
            self.run_dir = None

            # 支持 Phase 1.5 的新命名结构 (runs/YYYY-MM-DD_HH-MM-SS_{uuid})
            if os.path.exists(os.path.join(runs_dir, run_id)):
                self.run_dir = os.path.join(runs_dir, run_id)
            else:
                # 遍历寻找
                for entry in os.listdir(runs_dir):
                    if run_id in entry:
                        full_path = os.path.join(runs_dir, entry)
                        if os.path.isdir(full_path):
                            self.run_dir = full_path
                            break
                    # 兼容旧结构
                    candidate_sub = os.path.join(runs_dir, entry, run_id)
                    if os.path.exists(candidate_sub):
                        self.run_dir = candidate_sub
                        break

            if not self.run_dir:
                raise ValueError(f"Run ID {run_id} not found in {runs_dir}")

            self.state = ProjectState.load(self.run_dir)
            self.logger_env = self._setup_logging(resume=True)
            self.log.info(f"Loaded project: {run_id} from {self.run_dir}")

        else:
            # === Phase 1.5: 目录结构规范化 ===
            now_str = datetime.datetime.now().strftime("%Y-%m-%d/%H-%M-%S")
            short_uid = uuid.uuid4().hex[:8]
            self.run_id = f"{now_str}_{short_uid}"

            self.run_dir = os.path.join(runs_dir, self.run_id)
            os.makedirs(self.run_dir, exist_ok=True)

            self.state = ProjectState(run_id=self.run_id, run_dir=self.run_dir)
            self.state.save()

            self.logger_env = self._setup_logging(resume=False)
            self.log.info(f"Initialized new project: {self.run_id}")

        self.store = LocalStore(self.run_dir)

        # === Phase 1.4: 全链路追踪集成 ===
        trace_path = os.path.join(self.run_dir, "logs", "llm_trace.jsonl")
        self.tracer = TraceLogger(trace_path)

        raw_provider = build_provider(self.config)

        get_step = lambda: self.state.step
        self.provider = TracingProvider(
            raw_provider, self.tracer, self.run_id, get_step
        )

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

    def run_ideation(self):
        """执行创意生成 (Step 01) - HITL 集成版 (Two-Layer Agent)"""
        step_name = "ideation"
        log = LogAdapter(
            self.logger_env["logger"], {"run_id": self.run_id, "step": step_name}
        )

        workflow = WorkflowEngine(
            {
                "cfg": self.config,
                "prompts": self.prompts,
                "provider": self.provider,
                "store": self.store,
                "log": log,
                "jsonl": self.logger_env["jsonl"],
                "run_id": self.run_id,
                "state": self.state,
            }
        )

        log.info("Starting Ideation Step (2-Layer Agent Mode)...")

        def _generate_ideas() -> list:
            ctx = {
                "cfg": self.config,
                "prompts": self.prompts,
                "provider": self.provider,
                "store": self.store,
                "log": log,  # 传入 log 以便 pipeline 打印进度
            }
            from pipeline.step_01_ideation import run as pipe_run_ideation

            # 执行两阶段生成
            res = pipe_run_ideation(ctx)

            # === 直接获取结构化候选项 ===
            raw_candidates = res.get("candidates_list", [])

            # 如果列表为空（回退逻辑）
            if not raw_candidates:
                log.warning(
                    "Pipeline returned no structured candidates, fallback to text read."
                )
                full_text = res.get("idea_text", "")
                if not full_text:
                    path = res.get("idea_path", "")
                    if path and os.path.exists(path):
                        with open(path, "r", encoding="utf-8") as f:
                            full_text = f.read()
                return [ArtifactCandidate(id="v1_fallback", content=full_text)]

            # 封装为 ArtifactCandidate
            candidates = []
            for i, text in enumerate(raw_candidates):
                # 提取标题用于 ID（可选，这里简单用 v1, v2）
                cid = f"v{i+1}"
                candidates.append(ArtifactCandidate(id=cid, content=text))

            return candidates

        # 执行 HITL
        selected = workflow.run_step_with_hitl(
            step_name="ideation",
            generate_fn=_generate_ideas,
            candidates_field="idea_candidates",
            selected_path_field="idea_path",
        )

        final_path = self.store.save_text(
            "01_ideation/ideas_selected.txt", selected.content
        )
        self.state.idea_path = final_path
        self.state.step = "ideation"
        self.state.save()

        log.info(f"Ideation finalized: {final_path}")

    def run_outline(self):
        """执行大纲生成 (Step 02) - HITL 集成版 (Two-Layer Agent)"""
        step_name = "outline"
        log = LogAdapter(
            self.logger_env["logger"], {"run_id": self.run_id, "step": step_name}
        )

        workflow = WorkflowEngine(
            {
                "cfg": self.config,
                "prompts": self.prompts,
                "provider": self.provider,
                "store": self.store,
                "log": log,
                "jsonl": self.logger_env["jsonl"],
                "run_id": self.run_id,
                "state": self.state,
            }
        )

        if not self.state.idea_path:
            log.error("Missing idea_path!")
            return

        def _generate_outline() -> list:
            ctx = {
                "cfg": self.config,
                "prompts": self.prompts,
                "provider": self.provider,
                "store": self.store,
                "idea_path": self.state.idea_path,
                "log": log,  # 传入 log
            }
            from pipeline.step_02_outline import run as pipe_run_outline

            res = pipe_run_outline(ctx)

            # === 获取候选项 ===
            # Step 02 产生的 candidates_list 通常只有一个元素（完整大纲）
            raw_candidates = res.get("candidates_list", [])

            # 回退逻辑
            if not raw_candidates:
                content = res.get("outline_text", "")
                if not content:
                    path = res.get("outline_path", "")
                    if path and os.path.exists(path):
                        with open(path, "r", encoding="utf-8") as f:
                            content = f.read()
                raw_candidates = [content] if content else []

            candidates = []
            for i, text in enumerate(raw_candidates):
                # 默认大纲只有一版 v1，但如果未来支持并行生成多种风格大纲，这里自动兼容
                cid = f"v{i+1}"
                candidates.append(ArtifactCandidate(id=cid, content=text))

            return candidates

        # 执行 HITL
        selected = workflow.run_step_with_hitl(
            step_name="outline",
            generate_fn=_generate_outline,
            candidates_field="outline_candidates",
            selected_path_field="outline_path",
        )

        final_path = self.store.save_text(
            "02_outline/outline_selected.md", selected.content
        )
        self.state.outline_path = final_path
        self.state.step = "outline"
        self.state.save()
        log.info(f"Outline finalized: {final_path}")

    def run_bible(self):
        """执行设定集生成 (Step 03) - HITL 集成版"""
        step_name = "bible"
        log = LogAdapter(
            self.logger_env["logger"], {"run_id": self.run_id, "step": step_name}
        )

        workflow = WorkflowEngine(
            {
                "cfg": self.config,
                "prompts": self.prompts,
                "provider": self.provider,
                "store": self.store,
                "log": log,
                "jsonl": self.logger_env["jsonl"],
                "run_id": self.run_id,
                "state": self.state,
            }
        )

        if not self.state.outline_path:
            log.error("Missing outline! Cannot generate Bible.")
            return

        def _generate_bible() -> list:
            ctx = {
                "cfg": self.config,
                "prompts": self.prompts,
                "provider": self.provider,
                "store": self.store,
                "outline_path": self.state.outline_path,
                "log": log,
            }
            from pipeline.step_03_bible import run as pipe_run_bible

            res = pipe_run_bible(ctx)

            # HITL 候选处理
            raw_candidates = res.get("candidates_list", [])

            # 回退逻辑
            if not raw_candidates:
                content = res.get("bible_text", "")
                if not content:
                    path = res.get("bible_path", "")
                    if path and os.path.exists(path):
                        with open(path, "r", encoding="utf-8") as f:
                            content = f.read()
                raw_candidates = [content] if content else []

            candidates = []
            for i, text in enumerate(raw_candidates):
                cid = f"v{i+1}"
                candidates.append(ArtifactCandidate(id=cid, content=text))

            return candidates

        log.info("Starting Bible Step with HITL...")

        selected = workflow.run_step_with_hitl(
            step_name="bible",
            generate_fn=_generate_bible,
            candidates_field="bible_candidates",  # 需要在 state.py 增加这个字段
            selected_path_field="bible_path",
        )

        final_path = self.store.save_text(
            "03_bible/bible_selected.md", selected.content
        )
        self.state.bible_path = final_path
        self.state.step = "bible"
        self.state.save()
        log.info(f"Bible saved to {self.state.bible_path}")

    def init_scenes(self):
        """Step 04a: 生成分场表 (HITL 集成版)"""
        step_name = "scene_plan"
        log = LogAdapter(
            self.logger_env["logger"], {"run_id": self.run_id, "step": step_name}
        )

        workflow = WorkflowEngine(
            {
                "cfg": self.config,
                "prompts": self.prompts,
                "provider": self.provider,
                "store": self.store,
                "log": log,
                "jsonl": self.logger_env["jsonl"],
                "run_id": self.run_id,
                "state": self.state,
            }
        )

        if not self.state.outline_path:
            log.error("Missing outline! Cannot generate Scene Plan.")
            return

        def _generate_scene_plan() -> list:
            ctx = {
                "cfg": self.config,
                "prompts": self.prompts,
                "provider": self.provider,
                "store": self.store,
                "outline_path": self.state.outline_path,
                "bible_path": self.state.bible_path,
                "log": log,
            }
            # 引入新写的 pipeline
            from pipeline.step_04_scene_plan import run as pipe_run_scene_plan

            res = pipe_run_scene_plan(ctx)

            # 获取文本内容
            candidates_list = res.get("candidates_list", [])
            if not candidates_list:
                # Fallback
                text = res.get("scene_plan_text", "")
                candidates_list = [text] if text else []

            candidates = []
            for i, text in enumerate(candidates_list):
                candidates.append(ArtifactCandidate(id=f"v{i+1}", content=text))

            return candidates

        log.info("Generating Scene Plan with HITL...")

        # 1. 运行 HITL 生成与精修
        selected = workflow.run_step_with_hitl(
            step_name="scene_plan",
            generate_fn=_generate_scene_plan,
            candidates_field="scene_plan_candidates",
            selected_path_field="scene_plan_path",
        )

        final_path = self.store.save_text(
            "04_scene_plan/scene_plan_selected.md", selected.content
        )
        self.state.scene_plan_path = final_path

        # 2. 解析最终选定的文本，转换为 SceneNode 对象
        log.info("Parsing selected scene plan into SceneNodes...")
        scenes = self._parse_scene_plan_text(selected.content)

        if not scenes:
            log.warning(
                "Parsing failed or empty. Fallback to raw generation logic needed?"
            )
            # TODO: 可以考虑从 json 文件恢复，但为了由文本驱动，这里最好保证 parser 健壮

        self.state.scenes = scenes
        self.state.step = "scene_plan"
        self.state.save()

        log.info(f"Initialized {len(self.state.scenes)} scenes.")

    def _parse_scene_plan_text(self, text: str) -> List[SceneNode]:
        """
        从 Markdown 文本中解析出 SceneNode。
        格式约定：
        # <ID>. <Title>
        > 梗概：<Summary>
        ...
        """
        scenes = []
        # 简单的正则匹配：匹配以 # 开头的行，提取 ID 和 Title
        # 假设格式：# 1. 第一章 遭遇战
        pattern = r"^#\s+(\d+)\.\s+(.*)$"

        current_id = None
        current_title = ""
        current_summary = ""

        lines = text.split("\n")
        for line in lines:
            line = line.strip()
            match = re.match(pattern, line)
            if match:
                # 如果已经有上一个场景，先保存 (简化逻辑，暂不保存上一个的内容细纲到 meta，只存结构)
                if current_id is not None:
                    node = SceneNode(
                        id=int(current_id),
                        title=current_title,
                        summary=current_summary,
                        status="pending",
                    )
                    scenes.append(node)

                # 开始新场景
                current_id = match.group(1)
                current_title = match.group(2)
                current_summary = ""

            elif line.startswith("> 梗概：") or line.startswith("> Summary:"):
                current_summary = line.split("：", 1)[-1].strip()

        # 保存最后一个
        if current_id is not None:
            node = SceneNode(
                id=int(current_id),
                title=current_title,
                summary=current_summary,
                status="pending",
            )
            scenes.append(node)

        return scenes

    def run_drafting_loop(self):
        """Step 04b: 循环生成正文（集成 WorkflowEngine 支持并行、A/B测试与 HITL）"""
        step_name = "drafting"
        log = LogAdapter(
            self.logger_env["logger"], {"run_id": self.run_id, "step": step_name}
        )
        jsonl = self.logger_env["jsonl"]

        if not self.state.scenes:
            log.warning("No scenes found. Run init_scenes() first.")
            return

        from core.context import ContextBuilder
        from agents.wiki_updater import WikiUpdater

        ctx_builder = ContextBuilder(self.state, self.store)
        wiki_updater = WikiUpdater(self.provider, self.prompts.get("global_system", ""))

        # === 修改点 6: 初始化 WorkflowEngine 并传入 state ===
        workflow = WorkflowEngine(
            {
                "cfg": self.config,
                "prompts": self.prompts,
                "provider": self.provider,
                "store": self.store,
                "log": log,
                "jsonl": jsonl,
                "run_id": self.run_id,
                "state": self.state,  # 关键：让 drafting 过程也能访问 notify/pause 状态
            }
        )

        import time

        for i, scene_node in enumerate(self.state.scenes):
            # 检查状态，支持断点续传（如果之前暂停了，state 会保存，这里重新进入循环）
            if scene_node.status == "done":
                log.info(f"Skipping Scene {scene_node.id} (Done)")
                continue

            # === Phase 2.1: 动态构建上下文 ===
            # === 关键：构建并注入上下文 ===
            log.info(f"Building context for Scene {scene_node.id}...")
            build_result = ctx_builder.build(scene_node.id)

            # 将构建好的 payload 注入到 node.meta，供 draft_single_scene 使用
            scene_node.meta["dynamic_context"] = build_result["payload"]
            context_payload = build_result["payload"]
            debug_info = build_result["debug_info"]

            scene_node.meta["dynamic_context"] = context_payload

            log_event(
                jsonl,
                {
                    "ts": datetime.datetime.now().isoformat(),
                    "run_id": self.run_id,
                    "step": step_name,
                    "event": "CONTEXT_ASSEMBLED",
                    "scene_id": scene_node.id,
                    "debug_info": debug_info,
                },
            )

            try:
                # === Phase 3: 调用 WorkflowEngine 执行生成 ===
                # process_scene 内部需自行处理 HITL 逻辑（如并行生成 -> 通知用户选择）
                # 传入 outline/bible 用于生成
                workflow.process_scene(
                    scene_node, self.state.outline_path, self.state.bible_path
                )

                # === Phase 2.2: 记忆维护 (Compaction) ===
                log.info(f"Updating memory for Scene {scene_node.id}...")

                # 读取最终选定的内容 (process_scene 会确保 content_path 指向选定文件)
                with open(scene_node.content_path, "r", encoding="utf-8") as f:
                    final_text = f.read()

                # A. 生成摘要
                summary = wiki_updater.summarize(final_text)
                scene_node.summary = summary

                log_event(
                    jsonl,
                    {
                        "ts": datetime.datetime.now().isoformat(),
                        "run_id": self.run_id,
                        "step": step_name,
                        "event": "MEMORY_UPDATED",
                        "scene_id": scene_node.id,
                        "new_summary_preview": summary[:50] + "...",
                    },
                )

                # 状态保存
                self.state.save()

            except Exception as e:
                log.error(f"Failed to process Scene {scene_node.id}: {e}")
                # 异常抛出，中断流程以便人工排查
                raise e

        self.state.step = "drafting_done"
        self.state.save()
        log.info("Drafting loop completed.")
