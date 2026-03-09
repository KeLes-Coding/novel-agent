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

    def _phase_order(self) -> List[ProjectPhase]:
        return [
            ProjectPhase.IDEATION,
            ProjectPhase.OUTLINE,
            ProjectPhase.BIBLE,
            ProjectPhase.SCENE_PLAN,
            ProjectPhase.DRAFTING,
            ProjectPhase.REVIEW,
            ProjectPhase.EXPORT,
            ProjectPhase.DONE,
        ]

    def _get_previous_phases(self, phase_name: str) -> List[ProjectPhase]:
        try:
            current_phase = ProjectPhase(phase_name)
        except Exception:
            return []
        order = self._phase_order()
        idx = order.index(current_phase) if current_phase in order else -1
        if idx <= 0:
            return []
        return order[:idx]

    def _backtrack_interactive(self, phase_name: str) -> bool:
        previous_phases = self._get_previous_phases(phase_name)
        if not previous_phases:
            self.interface.notify("提示", f"阶段 [{phase_name}] 没有可回溯的更早阶段。")
            return False

        options = [p.value for p in previous_phases]
        idx = self.interface.ask_choice("请选择要回溯到的阶段:", options)
        target = previous_phases[idx]
        if self.interface.confirm(
            f"确认回溯到 [{target.value}] 吗？该操作只重置阶段指针，不删除已有文件。"
        ):
            self.log.warning(f"用户选择回溯: {phase_name} -> {target.value}")
            self._create_checkpoint(f"before_backtrack_{phase_name}_to_{target.value}")
            self.fsm.transition_to(target, force=True)
            self.interface.notify("回溯完成", f"当前阶段已切换为: {target.value}")
            return True
        self.log.info(f"用户取消回溯，保持当前阶段: {phase_name}")
        return False

    def _prompt_existing_phase_action(self, phase_name: str, reset_callback) -> str:
        """
        Return one of:
        - proceed: continue generation for current phase
        - skip: keep existing data and move to next phase
        - backtrack: rollback to an earlier phase and stop current phase execution
        - partial: enter partial edit workflow for current phase and stop current phase execution
        """
        if not self._is_step_executed(phase_name):
            return "proceed"

        options = [
            "跳过 (Skip) - 保持当前数据并进入下一阶段",
            "重写 (Rewrite) - 清空记录并重新生成",
            "回溯 (Backtrack) - 回到更早阶段",
        ]
        if phase_name in ("outline", "bible"):
            options.append("局部修改 (Partial Edit) - 只修改指定章节/设定块")
        if phase_name in ("ideation", "outline", "bible", "scene_plan"):
            options.append("编辑/重选 (Reselect) - 进入候选交互并可提意见微调")

        stale_tip = ""
        if phase_name in set(self.state.stale_phases or []):
            stale_tip = " [已失效: 上游内容发生变化]"
        choice = self.interface.ask_choice(
            f"检测到阶段 [{phase_name}] 已有历史数据{stale_tip}。\n请选择操作:",
            options,
        )

        if choice == 0:
            self.log.info(f"用户选择跳过阶段: {phase_name}")
            return "skip"

        if choice == 1:
            if self.interface.confirm(f"重写会丢弃阶段 [{phase_name}] 的现有记录，是否继续？"):
                self.log.info(f"用户选择重写阶段: {phase_name}，开始清理数据...")
                self._create_checkpoint(f"before_rewrite_{phase_name}")
                reset_callback()
                return "proceed"
            self.log.info(f"用户取消重写，阶段保持不变: {phase_name}")
            return "skip"

        if choice == 2:
            backtracked = self._backtrack_interactive(phase_name)
            return "backtrack" if backtracked else "skip"

        extra_idx = 3
        if phase_name in ("outline", "bible"):
            if choice == extra_idx:
                self.log.info(f"用户选择局部修改阶段: {phase_name}")
                return "partial"
            extra_idx += 1
        if phase_name in ("ideation", "outline", "bible", "scene_plan") and choice == extra_idx:
            self.log.info(f"用户选择编辑/重选阶段: {phase_name}")
            return "reselect"

        self.log.info(f"未识别的操作索引 {choice}，默认跳过阶段: {phase_name}")
        return "skip"

    def _phase_candidate_mapping(self, phase_name: str) -> Dict[str, str]:
        mapping = {
            "ideation": {"candidates_field": "idea_candidates", "path_field": "idea_path", "selected_relpath": "01_ideation/ideas_selected.txt"},
            "outline": {"candidates_field": "outline_candidates", "path_field": "outline_path", "selected_relpath": "02_outline/outline_selected.md"},
            "bible": {"candidates_field": "bible_candidates", "path_field": "bible_path", "selected_relpath": "03_bible/bible_selected.md"},
            "scene_plan": {"candidates_field": "scene_plan_candidates", "path_field": "scene_plan_path", "selected_relpath": "04_scene_plan/scene_plan_selected.md"},
        }
        return mapping.get(phase_name, {})

    def _seed_candidates_from_selected_if_needed(self, phase_name: str):
        mp = self._phase_candidate_mapping(phase_name)
        if not mp:
            return
        cands_field = mp["candidates_field"]
        path_field = mp["path_field"]
        cands = getattr(self.state, cands_field, [])
        if cands:
            return
        selected_path = getattr(self.state, path_field, "")
        if not selected_path or not os.path.exists(selected_path):
            return
        try:
            with open(selected_path, "r", encoding="utf-8") as f:
                content = f.read()
            setattr(self.state, cands_field, [ArtifactCandidate(id="current", content=content)])
            self.state.save()
        except Exception as e:
            self.log.warning(f"为阶段 {phase_name} 初始化候选失败: {e}")

    def _save_selected_artifact(self, phase_name: str, selected: ArtifactCandidate):
        mp = self._phase_candidate_mapping(phase_name)
        if not mp:
            return
        if phase_name == "ideation":
            # Keep every idea version instead of overwriting the previous one.
            history_rel_dir = "01_ideation/history"
            history_abs_dir = self.store._abs(history_rel_dir)
            os.makedirs(history_abs_dir, exist_ok=True)

            max_ver = 0
            for name in os.listdir(history_abs_dir):
                m = re.match(r"idea_v(\d+)\.txt$", name)
                if m:
                    max_ver = max(max_ver, int(m.group(1)))
            next_ver = max_ver + 1

            version_rel = f"{history_rel_dir}/idea_v{next_ver:03d}.txt"
            version_abs = self.store.save_text(version_rel, selected.content)

            # Keep a latest pointer for compatibility with existing downstream logic/tools.
            self.store.save_text("01_ideation/ideas_selected.txt", selected.content)

            # Append-only history log for quick review.
            _, f = self.store.open_text("01_ideation/ideas_history.md", mode="a")
            with f:
                ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"\n\n## Idea v{next_ver:03d} ({ts})\n\n")
                f.write(selected.content.strip())
                f.write("\n")

            setattr(self.state, mp["path_field"], version_abs)
            self.state.save()
            return

        final_path = self.store.save_text(mp["selected_relpath"], selected.content)
        setattr(self.state, mp["path_field"], final_path)
        self.state.save()

    def _resolve_draft_mode(self, auto_mode: bool, draft_mode: Optional[str]) -> str:
        if draft_mode in ("scene_by_scene", "full_auto"):
            return draft_mode
        override = getattr(self, "draft_mode_override", None)
        if override in ("scene_by_scene", "full_auto"):
            return override
        cfg_mode = self.config.get("workflow", {}).get("drafting_mode")
        if cfg_mode in ("scene_by_scene", "full_auto"):
            return cfg_mode
        return "full_auto" if auto_mode else "scene_by_scene"

    def _run_reselect_phase(
        self,
        phase_name: str,
        generate_fn,
        candidates_field: str,
        selected_path_field: str,
    ) -> ArtifactCandidate:
        workflow = self._get_workflow(phase_name)
        self._seed_candidates_from_selected_if_needed(phase_name)
        return workflow.run_step_with_hitl(phase_name, generate_fn, candidates_field, selected_path_field)

    def _extract_chapter_title(self, chapter: Dict[str, Any]) -> str:
        title = str(chapter.get("title", "")).strip()
        cid = chapter.get("chapter_id", "")
        return f"第{cid}章 {title}".strip() if cid else title

    def _outline_json_path(self) -> str:
        return self.store._abs("02_outline/outline.json")

    def _bible_json_path(self) -> str:
        return self.store._abs("03_bible/bible.json")

    def _render_outline_from_json(self, outline_data: List[Dict[str, Any]]) -> str:
        parts = ["# 全书大纲", ""]
        for vol in outline_data:
            vol_id = vol.get("volume_id", "")
            vol_title = vol.get("title", "")
            vol_summary = vol.get("summary", "")
            parts.append(f"## 第{vol_id}卷：{vol_title}".strip())
            if vol_summary:
                parts.append(f"**本卷摘要**：{vol_summary}")
            parts.append("")
            for chap in vol.get("chapters", []) or []:
                cid = chap.get("chapter_id", "")
                ctitle = chap.get("title", "")
                csum = chap.get("summary", "")
                parts.append(f"### 第{cid}章 {ctitle}".strip())
                parts.append(csum)
                parts.append("")
            parts.append("---")
            parts.append("")
        return "\n".join(parts).strip() + "\n"

    def _render_bible_from_json(self, bible_data: List[Dict[str, Any]]) -> str:
        parts = ["# 全书设定集", ""]
        for profile in bible_data:
            cat = profile.get("category", "未知")
            name = profile.get("name", "未命名")
            parts.append(f"## {cat}：{name}")
            parts.append("")
            parts.append(f"**基础信息**：{profile.get('base_info', '')}")
            parts.append("")
            parts.append(f"**核心特质**：{profile.get('traits', '')}")
            parts.append("")
            parts.append(f"**背景故事**：{profile.get('backstory', '')}")
            parts.append("")
            parts.append(f"**关联角色**：{profile.get('role', '')}")
            parts.append("")
            parts.append(f"**高光时刻**：{profile.get('highlight', '')}")
            parts.append("")
            parts.append("---")
            parts.append("")
        return "\n".join(parts).strip() + "\n"

    def _ai_revise_text(self, original_text: str, instruction: str, mode: str) -> str:
        if mode == "rewrite":
            mode_prompt = "请按用户要求重写目标内容，并保持与全局上下文一致。"
        else:
            mode_prompt = "请按用户要求进行局部微调，尽量最小改动。"
        sys_prompt = "你是一名资深小说编辑，擅长结构化改稿。"
        user_prompt = (
            f"{mode_prompt}\n\n"
            f"【用户需求】\n{instruction}\n\n"
            f"【原始内容】\n{original_text}\n\n"
            f"请仅输出修改后的完整内容。"
        )
        return self.provider.generate(system=sys_prompt, prompt=user_prompt).text.strip()

    def _phase_downstream(self) -> Dict[str, List[str]]:
        return {
            "ideation": ["outline", "bible", "scene_plan", "drafting", "review", "export"],
            "outline": ["bible", "scene_plan", "drafting", "review", "export"],
            "bible": ["scene_plan", "drafting", "review", "export"],
            "scene_plan": ["drafting", "review", "export"],
            "drafting": ["review", "export"],
            "review": ["export"],
            "export": [],
        }

    def _mark_stale_from(self, phase_name: str):
        stale = set(self.state.stale_phases or [])
        for p in self._phase_downstream().get(phase_name, []):
            stale.add(p)
        self.state.stale_phases = sorted(stale)
        self.state.save()

    def _clear_stale_for(self, phase_name: str):
        stale = [p for p in (self.state.stale_phases or []) if p != phase_name]
        self.state.stale_phases = stale
        self.state.save()

    def _record_changeset(self, phase: str, tasks: List[Dict[str, Any]], applied: int, failed: int):
        rec = {
            "phase": phase,
            "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
            "tasks": tasks,
            "applied": applied,
            "failed": failed,
        }
        self.state.change_sets.append(rec)
        self.state.save()

    def _detect_outline_conflicts(self, tasks: List[Dict[str, Any]]) -> List[str]:
        buckets: Dict[str, int] = {}
        for t in tasks:
            if not isinstance(t, dict):
                continue
            key = f"chapter_id={t.get('chapter_id')}"
            buckets[key] = buckets.get(key, 0) + 1
        return [k for k, c in buckets.items() if c > 1]

    def _detect_bible_conflicts(self, tasks: List[Dict[str, Any]]) -> List[str]:
        buckets: Dict[str, int] = {}
        for t in tasks:
            if not isinstance(t, dict):
                continue
            name = str(t.get("name", "")).strip()
            cat = str(t.get("category", "")).strip()
            key = f"name={name}|category={cat}" if cat else f"name={name}"
            buckets[key] = buckets.get(key, 0) + 1
        return [k for k, c in buckets.items() if c > 1]

    def _create_checkpoint(self, label: str):
        cp = {
            "label": label,
            "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
            "step": self.state.step,
            "idea_path": self.state.idea_path,
            "outline_path": self.state.outline_path,
            "bible_path": self.state.bible_path,
            "scene_plan_path": self.state.scene_plan_path,
            "stale_phases": list(self.state.stale_phases or []),
        }
        self.state.checkpoints.append(cp)
        if len(self.state.checkpoints) > 30:
            self.state.checkpoints = self.state.checkpoints[-30:]
        self.state.save()

    def _apply_outline_task(self, chapter_index: List[Dict[str, Any]], task: Dict[str, Any]) -> bool:
        chapter_id = task.get("chapter_id")
        mode = str(task.get("mode", "refine")).strip().lower()
        instruction = str(task.get("instruction", "")).strip()
        target = next((c for c in chapter_index if str(c.get("chapter_id")) == str(chapter_id)), None)
        if not target:
            return False
        if mode == "manual":
            if "title" in task and str(task.get("title", "")).strip():
                target["title"] = str(task["title"]).strip()
            if "summary" in task and str(task.get("summary", "")).strip():
                target["summary"] = str(task["summary"]).strip()
            return True

        if not instruction:
            return False
        original = f"Chapter Title: {target.get('title', '')}\nChapter Summary: {target.get('summary', '')}"
        revised = self._ai_revise_text(
            original_text=original,
            instruction=instruction,
            mode="rewrite" if mode == "rewrite" else "refine",
        )
        m = re.search(r"Chapter\s*Title:\s*(.+)", revised, flags=re.IGNORECASE)
        s = re.search(r"Chapter\s*Summary:\s*([\s\S]+)", revised, flags=re.IGNORECASE)
        if m:
            target["title"] = m.group(1).strip()
        if s:
            target["summary"] = s.group(1).strip()
        else:
            target["summary"] = revised.strip()
        return True

    def _apply_bible_task(self, bible_data: List[Dict[str, Any]], task: Dict[str, Any]) -> bool:
        mode = str(task.get("mode", "refine")).strip().lower()
        name = str(task.get("name", "")).strip()
        category = str(task.get("category", "")).strip()
        instruction = str(task.get("instruction", "")).strip()
        target = None
        if name and category:
            target = next((p for p in bible_data if str(p.get("name", "")) == name and str(p.get("category", "")) == category), None)
        if not target and name:
            target = next((p for p in bible_data if str(p.get("name", "")) == name), None)
        if not target:
            return False

        fields = ["base_info", "traits", "backstory", "role", "highlight"]
        if mode == "manual":
            updates = task.get("fields", {})
            if isinstance(updates, dict):
                for key in ["name", "category"] + fields:
                    if key in updates and str(updates[key]).strip():
                        target[key] = updates[key]
                return True
            return False

        if not instruction:
            return False
        original = json.dumps(target, ensure_ascii=False, indent=2)
        revised = self._ai_revise_text(
            original_text=original,
            instruction=instruction,
            mode="rewrite" if mode == "rewrite" else "refine",
        )
        try:
            parsed = json.loads(revised)
            if isinstance(parsed, dict):
                for key in ["name", "category"] + fields:
                    if key in parsed and str(parsed[key]).strip():
                        target[key] = parsed[key]
                return True
        except Exception:
            pass
        target["backstory"] = revised.strip()
        return True

    def _partial_edit_outline(self):
        path = self._outline_json_path()
        if not os.path.exists(path):
            self.interface.notify("错误", f"未找到大纲 JSON 文件: {path}")
            return
        self._create_checkpoint("before_partial_edit_outline")
        with open(path, "r", encoding="utf-8") as f:
            outline_data = json.load(f)
        if not isinstance(outline_data, list):
            self.interface.notify("错误", "outline.json 格式无效，预期为 list。")
            return

        chapter_index: List[Dict[str, Any]] = []
        for vol in outline_data:
            for chap in vol.get("chapters", []) or []:
                chapter_index.append(chap)
        if not chapter_index:
            self.interface.notify("提示", "没有可编辑的章节。")
            return

        while True:
            options = [self._extract_chapter_title(c) for c in chapter_index] + ["完成并保存"]
            idx = self.interface.ask_choice("请选择要修改的章节:", options)
            if idx == len(options) - 1:
                break

            target = chapter_index[idx]
            mode_idx = self.interface.ask_choice(
                "请选择修改方式:",
                [
                    "AI 微调 (Refine)",
                    "AI 重写 (Rewrite)",
                    "人工编辑 (Manual)",
                    "批量 ChangeSet (JSON)",
                    "跳过该章节",
                ],
            )
            if mode_idx == 4:
                continue

            if mode_idx == 2:
                new_summary = self.interface.prompt_multiline("请输入新章节摘要（输入 END 结束）")
                if new_summary.strip():
                    target["summary"] = new_summary.strip()
                new_title = self.interface.prompt_input("可选：新章节标题（留空保持不变）", default="")
                if new_title.strip():
                    target["title"] = new_title.strip()
                continue

            if mode_idx == 3:
                raw = self.interface.prompt_multiline(
                    "请输入 ChangeSet JSON 列表，例如 [{\"chapter_id\":1,\"mode\":\"refine\",\"instruction\":\"...\"}]"
                )
                if not raw.strip():
                    continue
                try:
                    tasks = json.loads(raw)
                    if not isinstance(tasks, list):
                        raise ValueError("changeset 必须是 list")
                except Exception as e:
                    self.interface.notify("错误", f"ChangeSet JSON 非法: {e}")
                    continue
                conflicts = self._detect_outline_conflicts(tasks)
                if conflicts:
                    msg = "检测到目标冲突（同一章节被多次修改）:\n- " + "\n- ".join(conflicts)
                    if not self.interface.confirm(msg + "\n是否仍继续按顺序执行？", default=False):
                        self.interface.notify("取消", "已取消本次批量修改。")
                        continue
                    self.log.warning(f"Outline ChangeSet conflicts: {conflicts}")
                if not tasks:
                    self.interface.notify("提示", "ChangeSet 为空，已跳过。")
                    continue
                applied = 0
                failed = 0
                for task in tasks:
                    if isinstance(task, dict) and self._apply_outline_task(chapter_index, task):
                        applied += 1
                    else:
                        failed += 1
                self._record_changeset("outline", tasks, applied, failed)
                self.interface.notify("批量完成", f"大纲 ChangeSet 完成: 成功 {applied}，失败 {failed}")
                continue

            instruction = self.interface.prompt_multiline("请输入修改要求（输入 END 结束）")
            if not instruction.strip():
                continue

            original = f"Chapter Title: {target.get('title', '')}\nChapter Summary: {target.get('summary', '')}"
            revised = self._ai_revise_text(
                original_text=original,
                instruction=instruction,
                mode="rewrite" if mode_idx == 1 else "refine",
            )
            m = re.search(r"Chapter\s*Title:\s*(.+)", revised, flags=re.IGNORECASE)
            s = re.search(r"Chapter\s*Summary:\s*([\s\S]+)", revised, flags=re.IGNORECASE)
            if m:
                target["title"] = m.group(1).strip()
            if s:
                target["summary"] = s.group(1).strip()
            else:
                target["summary"] = revised.strip()

        with open(path, "w", encoding="utf-8") as f:
            json.dump(outline_data, f, ensure_ascii=False, indent=2)

        rendered = self._render_outline_from_json(outline_data)
        self.state.outline_path = self.store.save_text("02_outline/outline_selected.md", rendered)
        self.state.save()
        self._clear_stale_for("outline")
        self._mark_stale_from("outline")
        self.interface.notify("完成", "Outline 局部修改已保存。")

    def _partial_edit_bible(self):
        path = self._bible_json_path()
        if not os.path.exists(path):
            self.interface.notify("错误", f"未找到 Bible JSON 文件: {path}")
            return
        self._create_checkpoint("before_partial_edit_bible")
        with open(path, "r", encoding="utf-8") as f:
            bible_data = json.load(f)
        if not isinstance(bible_data, list):
            self.interface.notify("错误", "bible.json 格式无效，预期为 list。")
            return
        if not bible_data:
            self.interface.notify("提示", "没有可编辑的设定档案。")
            return

        def _label(profile: Dict[str, Any]) -> str:
            return f"{profile.get('category', '未知')} - {profile.get('name', '未命名')}"

        while True:
            options = [_label(p) for p in bible_data] + ["完成并保存"]
            idx = self.interface.ask_choice("请选择要修改的设定档案:", options)
            if idx == len(options) - 1:
                break

            target = bible_data[idx]
            mode_idx = self.interface.ask_choice(
                "请选择修改方式:",
                [
                    "AI 微调 (Refine)",
                    "AI 重写 (Rewrite)",
                    "人工编辑 (Manual)",
                    "批量 ChangeSet (JSON)",
                    "跳过该档案",
                ],
            )
            if mode_idx == 4:
                continue

            fields = ["base_info", "traits", "backstory", "role", "highlight"]
            if mode_idx == 2:
                for field in fields:
                    new_val = self.interface.prompt_multiline(f"编辑字段 {field}（输入 END 结束，留空保持不变）")
                    if new_val.strip():
                        target[field] = new_val.strip()
                new_name = self.interface.prompt_input("可选：新名称（留空保持不变）", default="")
                if new_name.strip():
                    target["name"] = new_name.strip()
                continue

            if mode_idx == 3:
                raw = self.interface.prompt_multiline(
                    "请输入 ChangeSet JSON 列表，例如 [{\"name\":\"xxx\",\"mode\":\"refine\",\"instruction\":\"...\"}]"
                )
                if not raw.strip():
                    continue
                try:
                    tasks = json.loads(raw)
                    if not isinstance(tasks, list):
                        raise ValueError("changeset 必须是 list")
                except Exception as e:
                    self.interface.notify("错误", f"ChangeSet JSON 非法: {e}")
                    continue
                conflicts = self._detect_bible_conflicts(tasks)
                if conflicts:
                    msg = "检测到目标冲突（同一档案被多次修改）:\n- " + "\n- ".join(conflicts)
                    if not self.interface.confirm(msg + "\n是否仍继续按顺序执行？", default=False):
                        self.interface.notify("取消", "已取消本次批量修改。")
                        continue
                    self.log.warning(f"Bible ChangeSet conflicts: {conflicts}")
                if not tasks:
                    self.interface.notify("提示", "ChangeSet 为空，已跳过。")
                    continue
                applied = 0
                failed = 0
                for task in tasks:
                    if isinstance(task, dict) and self._apply_bible_task(bible_data, task):
                        applied += 1
                    else:
                        failed += 1
                self._record_changeset("bible", tasks, applied, failed)
                self.interface.notify("批量完成", f"设定集 ChangeSet 完成: 成功 {applied}，失败 {failed}")
                continue

            instruction = self.interface.prompt_multiline("请输入修改要求（输入 END 结束）")
            if not instruction.strip():
                continue

            original = json.dumps(target, ensure_ascii=False, indent=2)
            revised = self._ai_revise_text(
                original_text=original,
                instruction=instruction,
                mode="rewrite" if mode_idx == 1 else "refine",
            )
            try:
                parsed = json.loads(revised)
                if isinstance(parsed, dict):
                    for key in ["name", "category"] + fields:
                        if key in parsed and str(parsed[key]).strip():
                            target[key] = parsed[key]
                else:
                    target["backstory"] = revised.strip()
            except Exception:
                target["backstory"] = revised.strip()

        with open(path, "w", encoding="utf-8") as f:
            json.dump(bible_data, f, ensure_ascii=False, indent=2)

        rendered = self._render_bible_from_json(bible_data)
        self.state.bible_path = self.store.save_text("03_bible/bible_selected.md", rendered)
        self.state.save()
        self._clear_stale_for("bible")
        self._mark_stale_from("bible")
        self.interface.notify("完成", "Bible 局部修改已保存。")

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
            self.run_drafting_loop(
                auto_mode=True,
                draft_mode=getattr(self, "draft_mode_override", None),
            )
        elif current == fsm_lib.ProjectPhase.REVIEW:
            self.run_review()
        elif current == fsm_lib.ProjectPhase.EXPORT:
            self.run_export()
        elif current == fsm_lib.ProjectPhase.DONE:
            self.interface.notify("完成", "项目已完成。")

    # --- Specific Steps ---

    def run_ideation(self, force: bool = False):
        action = "proceed"
        if not force:
            action = self._prompt_existing_phase_action("ideation", self._reset_ideation)
            if action == "skip":
                self.fsm.transition_to(fsm_lib.ProjectPhase.OUTLINE)
                return
            if action in ("backtrack", "partial"):
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
                self._save_selected_artifact("ideation", ArtifactCandidate(id="manual_input", content=user_idea))
                log.info(f"????????????????: {self.state.idea_path}")
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

        if action == "reselect":
            selected = self._run_reselect_phase("ideation", _generate_ideas, "idea_candidates", "idea_path")
        else:
            selected = workflow.run_step_with_hitl("ideation", _generate_ideas, "idea_candidates", "idea_path")

        self._save_selected_artifact("ideation", selected)
        self._clear_stale_for("ideation")
        self._mark_stale_from("ideation")
        log.info(f"创意已确认: {self.state.idea_path}")
        
        # 推进到下一阶段
        self.fsm.transition_to(fsm_lib.ProjectPhase.OUTLINE)

    def run_outline(self, force: bool = False):
        action = "proceed"
        if not force:
            action = self._prompt_existing_phase_action("outline", self._reset_outline)
            if action == "skip":
                self.fsm.transition_to(fsm_lib.ProjectPhase.BIBLE)
                return
            if action == "backtrack":
                return
            if action == "partial":
                self._partial_edit_outline()
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

        if action == "reselect":
            selected = self._run_reselect_phase("outline", _generate, "outline_candidates", "outline_path")
        else:
            selected = workflow.run_step_with_hitl("outline", _generate, "outline_candidates", "outline_path")
        self._save_selected_artifact("outline", selected)
        self._clear_stale_for("outline")
        self._mark_stale_from("outline")
        log.info("大纲已确认。")
        
        # 推进到下一阶段
        self.fsm.transition_to(fsm_lib.ProjectPhase.BIBLE)

    def run_bible(self, force: bool = False):
        action = "proceed"
        if not force:
            action = self._prompt_existing_phase_action("bible", self._reset_bible)
            if action == "skip":
                self.fsm.transition_to(fsm_lib.ProjectPhase.SCENE_PLAN)
                return
            if action == "backtrack":
                return
            if action == "partial":
                self._partial_edit_bible()
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

        if action == "reselect":
            selected = self._run_reselect_phase("bible", _generate, "bible_candidates", "bible_path")
        else:
            selected = workflow.run_step_with_hitl("bible", _generate, "bible_candidates", "bible_path")
        self._save_selected_artifact("bible", selected)
        self._clear_stale_for("bible")
        self._mark_stale_from("bible")
        log.info("设定集已确认。")
        
        # 推进到下一阶段
        self.fsm.transition_to(fsm_lib.ProjectPhase.SCENE_PLAN)

    def init_scenes(self, force: bool = False):
        action = "proceed"
        if not force:
            action = self._prompt_existing_phase_action("scene_plan", self._reset_scene_plan)
            if action == "skip":
                self.fsm.transition_to(fsm_lib.ProjectPhase.DRAFTING)
                return
            if action in ("backtrack", "partial"):
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

        if action == "reselect":
            selected = self._run_reselect_phase("scene_plan", _generate, "scene_plan_candidates", "scene_plan_path")
        else:
            selected = workflow.run_step_with_hitl("scene_plan", _generate, "scene_plan_candidates", "scene_plan_path")
        self._save_selected_artifact("scene_plan", selected)
        
        scenes = self._parse_scene_plan_text(selected.content)
        self.state.scenes = scenes
        self.state.save()
        self._clear_stale_for("scene_plan")
        self._mark_stale_from("scene_plan")
        log.info(f"分场已确认，包含 {len(scenes)} 个根场景。")
        
        # 推进到下一阶段
        self.fsm.transition_to(fsm_lib.ProjectPhase.DRAFTING)

    def run_drafting_loop(self, force: bool = False, auto_mode: bool = False, draft_mode: Optional[str] = None):
        effective_mode = self._resolve_draft_mode(auto_mode=auto_mode, draft_mode=draft_mode)
        full_auto = effective_mode == "full_auto"
        resume_only = auto_mode and full_auto

        if not force:
            # In auto + full_auto, resume unfinished scenes by default instead of forcing rewrite/skip choice.
            if not (resume_only and self._is_step_executed("drafting")):
                action = self._prompt_existing_phase_action("drafting", self._reset_drafting)
                if action == "skip":
                    self.fsm.transition_to(fsm_lib.ProjectPhase.REVIEW)
                    return
                if action in ("backtrack", "partial"):
                    return

        self.fsm.transition_to(fsm_lib.ProjectPhase.DRAFTING, force=True)
        step_name = "drafting"
        self.workflow = self._get_workflow(step_name)
        log = self.workflow.log
        # Force A/B selection to auto in full_auto mode.
        self.workflow.selection_mode = "auto" if full_auto else "manual"
        self.interface.notify("Draft 模式", f"当前模式: {effective_mode}")
        
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
        if resume_only:
            total_scenes = len(self.state.scenes)
            done_scenes = sum(
                1
                for s in self.state.scenes
                if s.status == "done" and self.state._abs_path_exists(s.content_path)
            )
            self.interface.notify(
                "Draft Resume",
                f"Resuming drafting: completed {done_scenes}/{total_scenes}, remaining {total_scenes - done_scenes}.",
            )

        # 遍历所有根节点 (及其子节点)
        for i, scene_node in enumerate(self.state.scenes):
             if not full_auto:
                 proceed = self.interface.confirm(
                     f"是否处理场景 {scene_node.id}: {scene_node.title} ? (否=停止本次 Draft 并可后续继续)",
                     default=True,
                 )
                 if not proceed:
                     self.log.info(f"用户停止逐场景 Draft，停在 scene {scene_node.id}")
                     break
             self._process_scene_recursive(scene_node, full_auto)
                 
        unfinished = [s for s in self.state.scenes if s.status != "done"]
        if unfinished:
            self.interface.notify("??", f"Draft ?????? {len(unfinished)} ???????????? drafting?")
            self.state.save()
            return

        self.interface.notify("??", "??????????")
        self._clear_stale_for("drafting")
        self._mark_stale_from("drafting")

        # ???????
        self.fsm.transition_to(fsm_lib.ProjectPhase.REVIEW)
    def run_review(self, force: bool = False):
        if not force:
            action = self._prompt_existing_phase_action("review", self._reset_review)
            if action == "skip":
                self.fsm.transition_to(fsm_lib.ProjectPhase.EXPORT)
                return
            if action in ("backtrack", "partial"):
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
        self._clear_stale_for("review")
        self._mark_stale_from("review")
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
        self._clear_stale_for("export")
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
