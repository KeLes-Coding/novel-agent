# src/core/workflow.py
import os
import time
import re
from typing import List, Dict, Any, Callable, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from core.state import SceneNode, SceneCandidate, ArtifactCandidate
from interfaces.base import UserInterface

class WorkflowEngine:
    def __init__(self, manager_ctx: Dict[str, Any]):
        self.ctx = manager_ctx
        self.cfg = manager_ctx["cfg"]
        self.log = manager_ctx["log"]
        self.prompts = manager_ctx["prompts"]
        self.provider = manager_ctx["provider"]
        self.store = manager_ctx["store"]
        self.run_id = manager_ctx["run_id"]
        self.state = manager_ctx.get("state")
        self.interface: UserInterface = manager_ctx.get("interface") # 依赖注入

        wf_cfg = self.cfg.get("workflow", {})
        self.branching_enabled = wf_cfg.get("branching", {}).get("enabled", False)
        self.num_candidates = wf_cfg.get("branching", {}).get("num_candidates", 2)
        self.interactive = wf_cfg.get("interactive", True)
        selection_mode = wf_cfg.get("branching", {}).get("selection_mode")
        # Default behavior:
        # - interactive runs default to manual selection
        # - non-interactive runs default to auto selection (avoid blocking)
        if not selection_mode:
            selection_mode = "manual" if self.interactive else "auto"
        self.selection_mode = selection_mode

    def run_step_with_hitl(
        self,
        step_name: str,
        generate_fn: Callable[[], List[ArtifactCandidate]],
        candidates_field: str,
        selected_path_field: str,
    ) -> ArtifactCandidate:
        """
        Headless (Non-interactive) step runner.
        """
        current_candidates = getattr(self.state, candidates_field, [])

        if not current_candidates:
            self.log.info(f"[{step_name}] 正在调用 AI 生成候选项...")
            try:
                new_candidates = generate_fn()
                setattr(self.state, candidates_field, new_candidates)
                self.state.save()
            except Exception as e:
                self.log.error(f"生成失败: {e}")
                raise e

        # 获取生成的或已存在的候选项
        candidates = getattr(self.state, candidates_field, [])
        if not candidates:
             raise ValueError(f"No candidates generated for step {step_name}")

        if not self.interactive:
            self.log.info(f"[{step_name}] 自动选择第一个候选项。")
            selected_candidate = candidates[0]
            selected_candidate.selected = True
        else:
            while True:
                self.log.info(f"\n[{step_name}] 等待用户从 {len(candidates)} 个候选项中选择...")
                for i, c in enumerate(candidates):
                    # 截取前 150 个字符
                    preview = c.content[:150].replace('\n', ' ') + "..."
                    print(f"  {i+1}. 选项 {i+1} (ID: {c.id}): {preview}")
                
                print("\n操作指引:")
                print("  [1-N] 直接选择对应编号的候选项")
                print("  [vN]  查看候选项 N 的完整全文 (例: v1)")
                print("  [eN]  选择候选项 N 并提供修改意见 (例: e1)")
                print("  [r]   全部重新生成 (Reroll)")
                
                user_in = self.interface.prompt_input("请选择操作", default="1").lower()
                
                if user_in == 'r':
                    self.log.info("用户请求全部重新生成...")
                    # Clear current candidates and regenerate
                    setattr(self.state, candidates_field, [])
                    new_candidates = generate_fn()
                    setattr(self.state, candidates_field, new_candidates)
                    self.state.save()
                    candidates = new_candidates
                    continue
                    
                if user_in.startswith('v'):
                    try:
                        idx = int(user_in[1:]) - 1
                        if 0 <= idx < len(candidates):
                            print(f"\n--- 选项 {idx+1} 完整内容 ---\n")
                            print(candidates[idx].content)
                            print("\n---------------------------\n")
                            self.interface.prompt_input("按回车键继续...")
                        else:
                            print("无效的编号。")
                    except ValueError:
                        print("格式错误，请使用 v1, v2 等。")
                    continue
                    
                if user_in.startswith('e'):
                    try:
                        idx = int(user_in[1:]) - 1
                        if 0 <= idx < len(candidates):
                            feedback = self.interface.prompt_multiline("请输入您的修改意见")
                            if not feedback.strip():
                                print("修改意见为空，取消修改。")
                                continue
                            
                            self.log.info(f"正在根据意见修改选项 {idx+1} ...")
                            # Trigger the AI to revise based on feedback
                            # We need a generic revision prompt
                            revise_tpl = self.prompts.get("global_system", "")
                            # Since we don't have the original context here easily, we rely on the provider
                            # For simplicity in the loop, we will prompt the provider with the feedback + original text
                            revised_text = self._revise_candidate(candidates[idx].content, feedback)
                            
                            candidates[idx].content = revised_text
                            self.state.save()
                            self.log.info(f"选项 {idx+1} 已根据您的意见更新！")
                        else:
                            print("无效的编号。")
                    except ValueError:
                        print("格式错误，请使用 e1, e2 等。")
                    except Exception as e:
                        print(f"修改失败: {e}")
                    continue
                
                # Default selection (1-N)
                if user_in.isdigit():
                    idx = int(user_in) - 1
                    if 0 <= idx < len(candidates):
                        selected_candidate = candidates[idx]
                        selected_candidate.selected = True
                        self.log.info(f"用户选择了候选项: 选项 {idx+1}")
                        break
                    else:
                        print("无效的编号。")
                else:
                    print("无法识别的输入，请重试。")
            
        # 保存状态
        self.state.system_status = "running"
        self.state.save()
        return selected_candidate

    def _revise_candidate(self, original_text: str, feedback: str) -> str:
        """
        根据用户意见修改指定的候选方案，通用方法。
        """
        sys_prompt = "你是一位专业的网文主编与作者，请听从用户的修改意见，对给定的文案进行针对性修改。"
        user_prompt = f"【原始内容】\n{original_text}\n\n【修改意见】\n{feedback}\n\n请严格尊崇修改意见，重新输出修改后的完整内容（不要包含任何解说或多余的 Markdown 代码块前缀）："
        
        # 使用 provider 的非流式 generate 快速生成
        response = self.provider.generate(system=sys_prompt, prompt=user_prompt)
        text = response.text.strip()
        
        # 清理多余的 Markdown backticks
        import re
        text = re.sub(r"^```[a-zA-Z]*\n", "", text, flags=re.MULTILINE)
        text = re.sub(r"^```", "", text, flags=re.MULTILINE)
        
        return text

    # 场景处理与 AB 测试逻辑
    def process_scene(self, scene_node: SceneNode, outline_path: str, bible_path: str):
        if not self.branching_enabled or self.num_candidates <= 1:
            self._generate_single(scene_node, outline_path, bible_path)
        else:
            self._generate_ab_test(scene_node, outline_path, bible_path)

    def _generate_single(self, scene_node: SceneNode, outline_path: str, bible_path: str):
         self.log.info(f"正在生成单线草稿: 场景 {scene_node.id}")
         # Output path is now .json
         rel_path = f"05_drafting/scenes/scene_{scene_node.id:03d}.json"
         
         from pipeline.step_05_drafting import DraftingStep
         step_ctx = {
             "cfg": self.ctx["cfg"],
             "prompts": self.ctx["prompts"],
             "provider": self.ctx["provider"],
             "store": self.ctx["store"],
             "log": self.ctx["log"],
             "jsonl": self.ctx["jsonl"],
         }
         drafting_step = DraftingStep(step_ctx)
         
         # 1. Initial Draft
         text_result = drafting_step.draft_single_scene(
             scene_data=scene_node.meta,
             outline_path=outline_path,
             bible_path=bible_path,
             rel_path=rel_path,
             jsonl=self.ctx["jsonl"],
             run_id=self.ctx["run_id"]
         )
         
         # 2. Auto-Polish is now handled in Review Phase (per user request)
         # But if we want to keep it optional in drafting, we could. 
         # User said: "Should be implemented in Review phase".
         # So we remove it from here to avoid double work, or make it configurable.
         # For now, we strictly follow the request to move it to Review.
         
         scene_node.content_path = self.ctx["store"]._abs(rel_path)
         scene_node.status = "done"

    def run_polish_cycle(self, scene_node: SceneNode) -> bool:
        """
        Run the Writer -> Reader -> Polisher loop for a single scene.
        Returns True if polished, False otherwise.
        """
        if not self.cfg.get("workflow", {}).get("auto_polish", False):
            return False

        self.log.info(f"Review Phase: Starting Auto-Polish for Scene {scene_node.id}: {scene_node.title}")
        
        # 1. Load Current Content
        if not scene_node.content_path or not os.path.exists(scene_node.content_path):
            self.log.warning(f"Scene {scene_node.id} content missing at {scene_node.content_path}. Trying fallback to drafting.")
            fallback_path = self.store._abs(f"05_drafting/scenes/scene_{scene_node.id:03d}.json")
            if os.path.exists(fallback_path):
                self.log.info(f"Fallback found at {fallback_path}. Restoring content_path.")
                scene_node.content_path = fallback_path
            else:
                self.log.error(f"Fallback also missing for Scene {scene_node.id}. Cannot polish.")
                return False
            
        import json
        current_text = ""
        current_data = {}
        
        try:
            if scene_node.content_path.endswith(".json"):
                with open(scene_node.content_path, "r", encoding="utf-8") as f:
                    current_data = json.load(f)
                    current_text = current_data.get("content", "")
            else:
                with open(scene_node.content_path, "r", encoding="utf-8") as f:
                    current_text = f.read()
                    current_data = {"content": current_text}
        except Exception as e:
            self.log.error(f"Failed to load scene {scene_node.id}: {e}")
            return False

        if not current_text:
            self.log.warning(f"Scene {scene_node.id} is empty. Skipping polish.")
            return False

        # 2. Reader - Critique
        from agents.reader import ReaderAgent
        reader = ReaderAgent(self.provider)
        self.log.info(f"Scene {scene_node.id}: Reader analyzing...")
        critique = reader.critique(current_text)
        score = critique.get("score", 0)
        self.log.info(f"Scene {scene_node.id} - Reader Score: {score}")

        # 3. Polisher - Refine
        from agents.polisher import PolisherAgent
        polisher = PolisherAgent(self.provider)
        style_guide = scene_node.meta.get("style_guide", "")
        if not style_guide:
            tone = self.cfg.get("story_constraints", {}).get("tone", [])
            pov = self.cfg.get("story_constraints", {}).get("pov", "第三人称")
            style_guide = f"视角：{pov}\n基调：{', '.join(tone) if isinstance(tone, list) else tone}"
        
        # --- Style RAG Integration for Polishing ---
        style_examples = []
        try:
            from style.retriever import StyleRetriever
            retriever = StyleRetriever()
            
            # Construct Query
            query = scene_node.summary if scene_node.summary else scene_node.title
            
            # Filter Logic
            filters = {}
            if "style_author" in scene_node.meta:
                filters["author"] = scene_node.meta["style_author"]
            
            # Retrieve
            if query:
                results = retriever.retrieve(query, n_results=3, filter_meta=filters)
                for r in results:
                    style_examples.append(r["text"])
        except Exception as e:
            self.log.error(f"Review phase style retrieval failed: {e}")
        # ---------------------------------------------
        
        # Define output paths for streaming
        polished_rel_path = f"06_polishing/scenes/scene_{scene_node.id:03d}.json"
        polished_md_path = polished_rel_path.replace(".json", ".md")
        bypass_md_path = polished_md_path.replace(".md", "_bypass.md")
        
        # Ensure directories exist
        os.makedirs(self.store._abs("06_polishing/scenes"), exist_ok=True)
        os.makedirs(self.store._abs("06_polishing/diffs"), exist_ok=True)
        os.makedirs(self.store._abs("06_polishing/critiques"), exist_ok=True)
        
        self.log.info(f"Scene {scene_node.id}: Polisher refining with style_guide...\n{style_guide}")
        polished_text = polisher.polish(current_text, critique, style_guide=style_guide, style_examples=style_examples, output_path=self.store._abs(polished_md_path))
        
        # 3.5. AI Bypass (Step 6.5) - Humanize
        from agents.ai_bypass import AIBypassAgent
        bypass_agent = AIBypassAgent(self.provider, self.prompts)
        self.log.info(f"Scene {scene_node.id}: AIBypass applying humanization...")
        final_text = bypass_agent.bypass(polished_text, output_path=self.store._abs(bypass_md_path))

        # 4. Save Result (Separate Directory: 06_polishing)
        critique_rel_path = f"06_polishing/critiques/scene_{scene_node.id:03d}_critique.json"
        diff_rel_path = f"06_polishing/diffs/scene_{scene_node.id:03d}_diff.md"
        
        # Generate Diff (Compare original draft with final bypassed text)
        import difflib
        diff_lines = list(difflib.unified_diff(
            current_text.splitlines(keepends=True),
            final_text.splitlines(keepends=True),
            fromfile='draft',
            tofile='polished_and_bypassed',
            n=3
        ))
        diff_text = "".join(diff_lines)
        if diff_text:
            self.log.info(f"Saving diff to {diff_rel_path}...")
            self.store.save_text(diff_rel_path, f"```diff\n{diff_text}\n```")
        
        # Save Critique Log
        critique_data = {
            "scene_id": scene_node.id,
            "timestamp": int(time.time()),
            "score": score,
            "critique": critique
        }
        self.log.info(f"Saving critique to {critique_rel_path}...")
        self.store.save_json(critique_rel_path, critique_data)

        # Update Draft Data with Polished Text
        current_data["content"] = final_text
        current_data["polish_timestamp"] = int(time.time())
        current_data["critique_ref"] = critique_rel_path
        
        self.log.info(f"Saving polished version to {polished_rel_path}...")
        self.store.save_json(polished_rel_path, current_data)
        
        # Also sync sidecar MD for easy reading
        self.store.save_text(polished_rel_path.replace(".json", ".md"), final_text)
        
        # Update scene node to point to the new polished version
        scene_node.content_path = self.ctx["store"]._abs(polished_rel_path)
        # We don't change status, it stays 'done'. 
        
        return True

    def _generate_ab_test(self, scene_node: SceneNode, outline_path: str, bible_path: str):
        self.log.info(f"正在进行 A/B 测试 (生成 {self.num_candidates} 个版本): 场景 {scene_node.id}")
        candidates = []
        futures = {}
        with ThreadPoolExecutor(max_workers=self.num_candidates) as executor:
            for i in range(self.num_candidates):
                cid = f"v{i+1}"
                # Output path is now .json
                rel_path = f"05_drafting/scenes/scene_{scene_node.id:03d}_{cid}.json"
                from pipeline.step_05_drafting import DraftingStep
                step_ctx = {
                    "cfg": self.ctx["cfg"],
                    "prompts": self.ctx["prompts"],
                    "provider": self.ctx["provider"],
                    "store": self.ctx["store"],
                    "log": self.log,
                    "jsonl": self.ctx["jsonl"],
                }
                drafting_step = DraftingStep(step_ctx)

                future = executor.submit(
                      drafting_step.draft_single_scene,
                      scene_data=scene_node.meta,
                      outline_path=outline_path,
                      bible_path=bible_path,
                      rel_path=rel_path,
                      jsonl=self.ctx["jsonl"],
                      run_id=self.ctx["run_id"]
                )
                futures[future] = (cid, rel_path)
        
        for f in as_completed(futures):
            cid, rpath = futures[f]
            try:
                # result is text content
                text = f.result()
                candidates.append(SceneCandidate(id=cid, content_path=self.ctx["store"]._abs(rpath), meta={"char_len": len(text)}))
            except Exception as e:
                self.log.error(f"版本 {cid} 失败: {e}")
        
        scene_node.candidates = candidates
        if not candidates:
             raise RuntimeError("所有候选版本生成均失败。")
        
        if self.selection_mode == "auto":
            winner_id = self._auto_evaluate(scene_node, candidates, bible_path)
        else:
            winner_id = self._manual_evaluate_ui(
                scene_node,
                candidates,
                outline_path=outline_path,
                bible_path=bible_path,
            )

        selected = next((c for c in candidates if c.id == winner_id), candidates[0])
        selected.selected = True
        scene_node.selected_candidate_id = winner_id
        
        # 保存标准路径 (Copy JSON content)
        standard_path = f"05_drafting/scenes/scene_{scene_node.id:03d}.json"
        
        # Read the selected JSON content
        import json
        with open(selected.content_path, "r", encoding="utf-8") as src:
             data = json.load(src)
        
        # Save to standard path
        self.ctx["store"].save_json(standard_path, data)
        
        # Also save sidecar MD for standard
        std_md_path = standard_path.replace(".json", ".md")
        self.ctx["store"].save_text(std_md_path, data.get("content", ""))

        scene_node.content_path = self.ctx["store"]._abs(standard_path)
        scene_node.status = "done"

    def _auto_evaluate(self, scene_node, candidates, bible_path):
        return candidates[0].id

    def _manual_evaluate_ui(
        self,
        scene_node: SceneNode,
        candidates: List[SceneCandidate],
        *,
        outline_path: str,
        bible_path: str,
    ) -> str:
        """
        Interactive A/B evaluation UI for a single scene.
        Supports view / select / feedback-rewrite / reroll.
        """
        import json

        def _load_candidate_text(c: SceneCandidate) -> str:
            try:
                if c.content_path and c.content_path.endswith(".json") and os.path.exists(c.content_path):
                    with open(c.content_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    return (data.get("content") or "").strip()
                if c.content_path and os.path.exists(c.content_path):
                    with open(c.content_path, "r", encoding="utf-8") as f:
                        return f.read().strip()
            except Exception as e:
                self.log.error(f"Failed to load candidate {c.id}: {e}")
            return ""

        def _save_candidate_text(c: SceneCandidate, new_text: str) -> None:
            if not c.content_path:
                raise ValueError("candidate.content_path is empty")
            if c.content_path.endswith(".json"):
                with open(c.content_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                data["content"] = new_text
                with open(c.content_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            else:
                with open(c.content_path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(new_text)
            c.meta["char_len"] = len(new_text)

        def _reroll_all() -> List[SceneCandidate]:
            self.log.info(f"场景 {scene_node.id}: reroll all candidates...")
            new_candidates: List[SceneCandidate] = []
            futures = {}
            with ThreadPoolExecutor(max_workers=self.num_candidates) as executor:
                for i in range(self.num_candidates):
                    cid = f"v{i+1}"
                    rel_path = f"05_drafting/scenes/scene_{scene_node.id:03d}_{cid}.json"
                    from pipeline.step_05_drafting import DraftingStep

                    step_ctx = {
                        "cfg": self.ctx["cfg"],
                        "prompts": self.ctx["prompts"],
                        "provider": self.ctx["provider"],
                        "store": self.ctx["store"],
                        "log": self.log,
                        "jsonl": self.ctx["jsonl"],
                    }
                    drafting_step = DraftingStep(step_ctx)
                    future = executor.submit(
                        drafting_step.draft_single_scene,
                        scene_data=scene_node.meta,
                        outline_path=outline_path,
                        bible_path=bible_path,
                        rel_path=rel_path,
                        jsonl=self.ctx["jsonl"],
                        run_id=self.ctx["run_id"],
                    )
                    futures[future] = (cid, rel_path)

            for f in as_completed(futures):
                cid, rpath = futures[f]
                try:
                    text = f.result()
                    new_candidates.append(
                        SceneCandidate(
                            id=cid,
                            content_path=self.ctx["store"]._abs(rpath),
                            meta={"char_len": len(text)},
                        )
                    )
                except Exception as e:
                    self.log.error(f"Reroll candidate {cid} failed: {e}")

            if not new_candidates:
                raise RuntimeError("Reroll produced no candidates.")
            return new_candidates

        while True:
            print(f"\n场景 {scene_node.id} 候选版本:")
            for i, c in enumerate(candidates):
                text = _load_candidate_text(c)
                preview = text[:120].replace("\n", " ")
                if len(text) > 120:
                    preview += "..."
                print(f"  {i+1}. [{c.id}] 字数: {c.meta.get('char_len', len(text))} 预览: {preview}")

            print("\n操作指引:")
            print("  [1-N] 选择候选版本")
            print("  [vN]  查看候选版本全文 (例: v1)")
            print("  [eN]  对候选版本提意见并重写 (例: e1)")
            print("  [r]   全部重新生成 (Reroll)")

            user_in = self.interface.prompt_input("请选择操作", default="1").lower().strip()

            if user_in == "r":
                candidates = _reroll_all()
                scene_node.candidates = candidates
                self.state.save()
                continue

            if user_in.startswith("v"):
                try:
                    idx = int(user_in[1:]) - 1
                    if 0 <= idx < len(candidates):
                        print(f"\n--- 候选 {idx+1} [{candidates[idx].id}] 全文 ---\n")
                        print(_load_candidate_text(candidates[idx]))
                        print("\n------------------------------\n")
                        self.interface.prompt_input("按回车继续...", default="")
                    else:
                        print("无效编号。")
                except ValueError:
                    print("格式错误，请使用 v1, v2 ...")
                continue

            if user_in.startswith("e"):
                try:
                    idx = int(user_in[1:]) - 1
                    if 0 <= idx < len(candidates):
                        feedback = self.interface.prompt_multiline("请输入您的修改意见")
                        if not feedback.strip():
                            print("修改意见为空，取消修改。")
                            continue
                        original = _load_candidate_text(candidates[idx])
                        if not original:
                            print("候选内容为空，无法修改。")
                            continue
                        revised = self._revise_candidate(original, feedback)
                        _save_candidate_text(candidates[idx], revised)
                        self.state.save()
                        self.log.info(f"场景 {scene_node.id}: candidate {candidates[idx].id} revised.")
                    else:
                        print("无效编号。")
                except ValueError:
                    print("格式错误，请使用 e1, e2 ...")
                except Exception as e:
                    print(f"修改失败: {e}")
                continue

            if user_in.isdigit():
                idx = int(user_in) - 1
                if 0 <= idx < len(candidates):
                    return candidates[idx].id
                print("无效编号。")
                continue

            print("无法识别的输入，请重试。")
