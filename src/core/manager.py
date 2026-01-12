# src/core/manager.py
import os
import datetime
import uuid
import yaml
from typing import Dict, Any, Optional

from utils.logger import RunContext, setup_loggers, StepTimer, LogAdapter, log_event
from utils.trace_logger import TraceLogger, TracingProvider
from utils.hashing import sha256_text, sha256_file
from providers.factory import build_provider
from storage.local_store import LocalStore
from core.state import ProjectState, SceneNode

# 引入 Pipeline Steps
from pipeline.step_02_outline import run as run_outline
from pipeline.step_03_bible import run as run_bible
from pipeline.step_04_drafting import generate_scene_plan, draft_single_scene


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
            # 也兼容旧结构
            if os.path.exists(os.path.join(runs_dir, run_id)):
                self.run_dir = os.path.join(runs_dir, run_id)
            else:
                # 遍历寻找
                for entry in os.listdir(runs_dir):
                    # 检查是否包含该 run_id (新命名规则后缀包含 uuid)
                    if run_id in entry:
                        full_path = os.path.join(runs_dir, entry)
                        if os.path.isdir(full_path):
                            self.run_dir = full_path
                            break
                    # 兼容旧的 date/run_id 结构
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
            # 格式: runs/YYYY-MM-DD_HH-MM-SS_{short_uuid}
            now_str = datetime.datetime.now().strftime("%Y-%m-%d/%H-%M-%S")
            short_uid = uuid.uuid4().hex[:8]
            self.run_id = f"{now_str}_{short_uid}"

            # 直接放在 runs 目录下，不再按日期分层，方便排序
            self.run_dir = os.path.join(runs_dir, self.run_id)
            os.makedirs(self.run_dir, exist_ok=True)

            self.state = ProjectState(run_id=self.run_id, run_dir=self.run_dir)
            self.state.save()

            self.logger_env = self._setup_logging(resume=False)
            self.log.info(f"Initialized new project: {self.run_id}")

        self.store = LocalStore(self.run_dir)

        # === Phase 1.4: 全链路追踪集成 ===
        # 1. 初始化 TraceLogger
        trace_path = os.path.join(self.run_dir, "logs", "llm_trace.jsonl")
        self.tracer = TraceLogger(trace_path)

        # 2. 构建原始 Provider
        raw_provider = build_provider(self.config)

        # 3. 包装 Provider
        # 动态获取当前 step 的 lambda
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
        # 保持原有应用日志配置
        ctx = RunContext(
            run_id=self.run_id,
            run_dir=self.run_dir,
            level=self.config["logging"]["level"],
            jsonl_events=self.config["logging"]["jsonl_events"],
        )
        return setup_loggers(ctx)

    def run_ideation(self):
        """执行创意生成 (Step 01)"""
        step_name = "ideation"
        log = LogAdapter(
            self.logger_env["logger"], {"run_id": self.run_id, "step": step_name}
        )

        log.info("Starting Ideation Step...")

        tags = self.config["content"]["tags"]
        genre = self.config["content"]["genre"]
        target_words = self.config["content"]["length"]["target_words"]

        ideation_prompt = (
            self.prompts["ideation"].strip()
            + f"\n\n约束：题材={genre}，tags={tags}，目标字数≈{target_words}"
        )
        system = self.prompts.get("global_system", "").strip()

        # 调用模型
        t_call = StepTimer()
        ideas_text = self.provider.generate(
            system=system, prompt=ideation_prompt, meta={"cfg": self.config}
        ).text
        call_ms = t_call.ms()

        # 保存
        path = self.store.save_text("01_ideation/ideas.txt", ideas_text)

        # 更新状态
        self.state.idea_path = path
        self.state.step = "ideation"
        self.state.save()

        log.info(f"Ideation saved to {path} (duration: {call_ms}ms)")

    def run_outline(self):
        """执行大纲生成 (Step 02)"""
        step_name = "outline"
        log = LogAdapter(
            self.logger_env["logger"], {"run_id": self.run_id, "step": step_name}
        )

        if not self.state.idea_path:
            log.error("Missing idea_path! Cannot generate Outline.")
            raise FileNotFoundError("idea_path is missing in state.")

        log.info("Starting Outline Step...")

        ctx = {
            "cfg": self.config,
            "prompts": self.prompts,
            "provider": self.provider,
            "store": self.store,
            "idea_path": self.state.idea_path,
        }
        res = run_outline(ctx)

        self.state.outline_path = res["outline_path"]
        self.state.step = "outline"
        self.state.save()
        log.info(f"Outline saved to {self.state.outline_path}")

    def run_bible(self):
        """执行设定集生成 (Step 03)"""
        step_name = "bible"
        log = LogAdapter(
            self.logger_env["logger"], {"run_id": self.run_id, "step": step_name}
        )

        if not self.state.outline_path:
            log.error("Missing outline! Cannot generate Bible.")
            return

        log.info("Starting Bible Step...")
        ctx = {
            "cfg": self.config,
            "prompts": self.prompts,
            "provider": self.provider,
            "store": self.store,
            "outline_path": self.state.outline_path,
        }
        res = run_bible(ctx)

        self.state.bible_path = res["bible_path"]
        self.state.step = "bible"
        self.state.save()
        log.info(f"Bible saved to {self.state.bible_path}")

    def init_scenes(self):
        """Step 04a: 生成分场表"""
        step_name = "scene_plan"
        log = LogAdapter(
            self.logger_env["logger"], {"run_id": self.run_id, "step": step_name}
        )

        log.info("Generating Scene Plan...")
        plan_data = generate_scene_plan(
            {
                "cfg": self.config,
                "prompts": self.prompts,
                "provider": self.provider,
                "store": self.store,
                "outline_path": self.state.outline_path,
                "bible_path": self.state.bible_path,
            }
        )

        self.state.scene_plan_path = plan_data["scene_plan_path"]
        raw_scenes = plan_data["scenes"]

        self.state.scenes = []
        for s in raw_scenes:
            node = SceneNode(
                id=s.get("id"), title=s.get("title"), status="pending", meta=s
            )
            self.state.scenes.append(node)

        self.state.step = "scene_plan"
        self.state.save()
        log.info(f"Initialized {len(self.state.scenes)} scenes.")

    def run_drafting_loop(self):
        """Step 04b: 循环生成正文（带自动重试机制）"""
        step_name = "drafting"
        log = LogAdapter(
            self.logger_env["logger"], {"run_id": self.run_id, "step": step_name}
        )
        jsonl = self.logger_env["jsonl"]

        if not self.state.scenes:
            log.warning("No scenes found. Run init_scenes() first.")
            return

        import time  # 引入时间模块用于冷却

        for i, scene_node in enumerate(self.state.scenes):
            # 检查状态，支持断点续传
            if scene_node.status == "done":
                log.info(f"Skipping Scene {scene_node.id} (Done)")
                continue

            # 计算目标路径
            rel_path = f"04_drafting/scenes/scene_{scene_node.id:03d}.md"

            # === 增加重试机制 ===
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    log.info(
                        f"Drafting Scene {scene_node.id}: {scene_node.title} (Attempt {attempt+1}/{max_retries})"
                    )

                    content = draft_single_scene(
                        scene_data=scene_node.meta,
                        cfg=self.config,
                        prompts=self.prompts,
                        provider=self.provider,
                        outline_path=self.state.outline_path,
                        bible_path=self.state.bible_path,
                        store=self.store,
                        rel_path=rel_path,
                        log=log,
                        jsonl=jsonl,
                        run_id=self.run_id,
                    )

                    # 成功后更新状态
                    scene_node.status = "done"
                    scene_node.content_path = self.store._abs(rel_path)
                    self.state.save()
                    break  # 跳出重试循环，进入下一章

                except Exception as e:
                    log.warning(f"Failed to draft scene {scene_node.id}: {e}")
                    if attempt < max_retries - 1:
                        log.info("Retrying in 5 seconds...")
                        time.sleep(5)  # 冷却一下
                    else:
                        log.error(
                            f"Scene {scene_node.id} failed after {max_retries} attempts."
                        )
                        raise e  # 重试耗尽，抛出异常终止程序

        self.state.step = "drafting_done"
        self.state.save()
        log.info("Drafting loop completed.")
