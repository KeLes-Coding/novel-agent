# src/core/workflow.py
import os
import time
import re
from typing import List, Dict, Any, Callable, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from core.state import SceneNode, SceneCandidate, ArtifactCandidate
from pipeline.step_05_drafting import draft_single_scene
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
        self.selection_mode = wf_cfg.get("branching", {}).get("selection_mode", "auto")
        self.interactive = wf_cfg.get("interactive", True)

    def run_step_with_hitl(
        self,
        step_name: str,
        generate_fn: Callable[[], List[ArtifactCandidate]],
        candidates_field: str,
        selected_path_field: str,
    ) -> ArtifactCandidate:
        """
        使用 UserInterface 的通用 HITL (Human-In-The-Loop) 步骤执行器。
        """
        # 1. 检查是否需要生成
        current_candidates = getattr(self.state, candidates_field, [])

        if not current_candidates:
            # 交互式询问生成方式
            source_choice = 0
            if self.interactive and self.interface:
                source_choice = self.interface.ask_choice(
                    f"[{step_name}] 准备生成内容，请选择来源:",
                    ["AI 自动生成 (AI Generation)", "上传本地文件 (Upload File)", "直接输入文本 (Direct Input)"],
                    ["调用大模型生成", "读取本地已有文件作为草稿", "在终端直接输入/粘贴文本"]
                )
            
            # 分支处理
            if source_choice == 1: # Upload
                path = self.interface.prompt_input("请输入文件的绝对路径")
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        content = f.read()
                    user_cand = ArtifactCandidate(id="用户上传", content=content, selected=True)
                    # 直接返回，不再进入循环，或者也进入循环让用户确认？
                    # 这里选择进入循环以便用户可以继续修改或重写
                    current_candidates = [user_cand]
                    setattr(self.state, candidates_field, current_candidates)
                    self.state.save()
                else:
                    self.interface.notify("错误", f"找不到文件: {path}")
                    # 失败回退到 AI 生成
                    source_choice = 0
            
            elif source_choice == 2: # Direct Input
                content = self.interface.prompt_multiline("请输入内容")
                if content:
                    user_cand = ArtifactCandidate(id="用户输入", content=content, selected=True)
                    current_candidates = [user_cand]
                    setattr(self.state, candidates_field, current_candidates)
                    self.state.save()
                else:
                    self.interface.notify("提示", "输入为空，转为 AI 生成")
                    source_choice = 0

            # 如果也是 Source Choice == 0 或者上面的失败回退
            if not current_candidates:
                self.log.info(f"[{step_name}] 正在调用 AI 生成候选项...")
                try:
                    new_candidates = generate_fn()
                    setattr(self.state, candidates_field, new_candidates)
                    self.state.save()
                except Exception as e:
                    self.log.error(f"生成失败: {e}")
                    raise e

        selected_candidate = None

        while True:
            candidates = getattr(self.state, candidates_field, [])

            # 非交互模式
            if not self.interactive:
                self.log.info(f"[{step_name}] 非交互模式，默认自动选择第一个。")
                selected_candidate = candidates[0]
                break
            
            # 通知用户
            if self.interface:
                self.interface.notify(
                    title=f"人工介入请求: {step_name}",
                    message=f"已生成 {len(candidates)} 个版本，请审核并选择。",
                    payload={"当前步骤": step_name}
                )

            self.state.system_status = "paused_for_input"
            self.state.save()

            if not self.interface:
                # 理论上不应该发生
                self.log.warning("未提供界面接口 (Interface)，自动选择第一个。")
                selected_candidate = candidates[0]
                break

            # 选项菜单
            options_display = []
            for c in candidates:
                preview = c.content[:100].replace("\n", " ") + "..."
                options_display.append(f"[{c.id}] {preview}")
            
            # 扩展指令
            menu_options = [
                "选择一个版本",
                "重写 (Reroll) - 放弃当前所有结果",
                "精修 (Edit/Refine) - 微调选定版本",
                "上传本地文件 (Upload)"
            ]

            choice_idx = self.interface.ask_choice(
                f"当前步骤: {step_name}\n候选项列表:\n" + "\n".join([f"  - {o}" for o in options_display]),
                menu_options,
                ["确认最终使用版本", "重新生成所有内容", "对特定版本进行基于 AI 的修改", "使用本地已有的文件"]
            )

            # 逻辑映射
            if choice_idx == 0: # 选择
                cand_idx = self.interface.ask_choice("请选择最终版本:", options_display)
                selected_candidate = candidates[cand_idx]
                break
            
            elif choice_idx == 1: # 重写
                if self.interface.confirm("确定要丢弃当前所有结果并重写吗? 这里的操作不可逆。"):
                    self.log.info("用户请求重写。")
                    setattr(self.state, candidates_field, [])
                    self.state.save()
                    return self.run_step_with_hitl(step_name, generate_fn, candidates_field, selected_path_field)

            elif choice_idx == 2: # 精修
                cand_idx = self.interface.ask_choice("请选择要精修的基础版本:", options_display)
                target_cand = candidates[cand_idx]
                
                refined_cand = self._interactive_refine_session(target_cand, step_name)
                if refined_cand:
                    candidates.append(refined_cand)
                    setattr(self.state, candidates_field, candidates)
                    self.state.save()
                    self.interface.notify("成功", "精修完成，已作为新版本添加。")

            elif choice_idx == 3: # 上传
                path = self.interface.prompt_input("请输入文件的绝对路径")
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        content = f.read()
                    user_cand = ArtifactCandidate(id="用户上传", content=content, selected=True)
                    candidates.append(user_cand)
                    setattr(self.state, candidates_field, candidates)
                    self.state.save()
                    self.interface.notify("成功", "文件已加载。")
                else:
                    self.interface.notify("错误", f"找不到文件: {path}")

        selected_candidate.selected = True
        self.state.system_status = "running"
        self.state.save()
        return selected_candidate

    def _interactive_refine_session(self, base_cand: ArtifactCandidate, step_name: str) -> Optional[ArtifactCandidate]:
        """
        交互式精修会话。
        """
        current_content = base_cand.content
        refine_dir = f"{step_name}/refinements"
        try:
             os.makedirs(self.store._abs(refine_dir), exist_ok=True)
        except Exception:
            pass
        
        while True:
            # 解析章节
            sections = self._parse_sections(current_content)
            has_structure = len(sections) > 1
            
            # 可视化结构
            section_opts = [f"{t[0]} ({len(t[1])} 字)" for t in sections] if has_structure else []
            
            menu_ops = ["保存并退出 (Save & Exit)", "放弃 (Cancel)"]
            if has_structure:
                menu_ops.extend(["查看章节 (View Section)", "修改章节 (Modify Section - AI)", "手动编辑章节 (Edit Section - Manual)"])
            menu_ops.extend(["查看全文 (View Full Text)", "修改全文 (Modify Full Text - AI)", "手动编辑全文 (Edit Full Text - Manual)", "一致性检查 (Check Consistency)"])
            
            choice = self.interface.ask_choice(
                f"精修模式 (当前基底: {base_cand.id}) - 总字数: {len(current_content)}", 
                menu_ops
            )
            
            op = menu_ops[choice]
            
            if "放弃" in op:
                return None
            
            if "保存" in op:
                 new_id = f"{base_cand.id}_精修版_{int(time.time())}"
                 return ArtifactCandidate(id=new_id, content=current_content)
                 
            if "查看章节" in op:
                s_idx = self.interface.ask_choice("选择要查看的章节:", section_opts)
                self.interface.notify(sections[s_idx][0], sections[s_idx][1])
                
            if "修改章节 (Modify Section - AI)" in op:
                s_idx = self.interface.ask_choice("选择要修改的章节:", section_opts)
                feedback = self.interface.prompt_input("请输入您的修改意见")
                if feedback:
                    self.interface.notify("AI 助手", "正在根据您的意见进行修改...")
                    timestamp = int(time.time())
                    rel_path = f"{refine_dir}/{base_cand.id}_mod_{s_idx}_{timestamp}.md"
                    target_text = sections[s_idx][1]
                    
                    try:
                        revised = self._call_llm_refine(target_text, feedback, rel_path)
                        current_content = self._replace_section(current_content, sections, s_idx, revised)
                        self.interface.notify("成功", "章节修改已应用。")
                    except Exception as e:
                        self.interface.notify("错误", f"修改失败: {e}")
            
            if "手动编辑章节" in op:
                s_idx = self.interface.ask_choice("选择要手动编辑的章节:", section_opts)
                original_text = sections[s_idx][1]
                print(f"--- 原文 ---\n{original_text}\n---")
                new_text = self.interface.prompt_multiline("请输入新的章节内容")
                if new_text:
                    current_content = self._replace_section(current_content, sections, s_idx, new_text)
                    self.interface.notify("成功", "手动修改已应用。")

            if "查看全文" in op:
                self.interface.notify("全文预览", current_content[:2000] + "\n...(已截断，太长无法完全显示)")

            if "修改全文 (Modify Full Text - AI)" in op:
                if self.interface.confirm("修改全文可能会导致内容不稳定，确定继续吗?"):
                     feedback = self.interface.prompt_input("请输入针对全文的修改意见")
                     if feedback:
                        self.interface.notify("AI 助手", "正在修改全文，请稍候...")
                        timestamp = int(time.time())
                        rel_path = f"{refine_dir}/{base_cand.id}_mod_all_{timestamp}.md"
                        try:
                            current_content = self._call_llm_refine(current_content, feedback, rel_path)
                            self.interface.notify("成功", "全文修改已应用。")
                        except Exception as e:
                             self.interface.notify("错误", f"失败: {e}")
            
            if "手动编辑全文" in op:
                 if self.interface.confirm("手动重写全文?"):
                     new_full = self.interface.prompt_multiline("请输入新的全文内容")
                     if new_full:
                         current_content = new_full
                         self.interface.notify("成功", "全文已手动覆盖。")

            if "一致性检查" in op:
                 self.interface.notify("AI 助手", "正在运行检查...")
                 report = self._run_consistency_check(current_content, step_name)
                 self.interface.notify("检查报告", report)

    def _parse_sections(self, content: str) -> List[Tuple[str, str]]:
        # 相同的正则逻辑
        pattern = r"(^|\n)(#{2,3}\s+.*)"
        parts = re.split(pattern, content)
        sections = []
        if len(parts) < 2:
            return []
        
        current_body = parts[0]
        if current_body.strip():
            sections.append(("导语", current_body))
            
        i = 1
        while i < len(parts) - 1:
            sep = parts[i]
            title_line = parts[i + 1].strip()
            body_text = parts[i + 2] if i + 2 < len(parts) else ""
            full_section = f"{sep}{title_line}{body_text}"
            clean_title = title_line.lstrip("#").strip()
            sections.append((clean_title, full_section))
            i += 3
        return sections

    def _replace_section(self, full_content: str, sections: List[Tuple[str, str]], idx: int, new_text: str) -> str:
        sections[idx] = (sections[idx][0], new_text)
        new_full = ""
        for title, body in sections:
            new_full += body
        return new_full

    def _call_llm_refine(self, content: str, feedback: str, rel_path: str) -> str:
        # Prompt 逻辑保持不变，但日志中文化
        refine_cfg = self.prompts.get("refinement", {})
        system_prompt = refine_cfg.get("system", "你是一位专业的网文编辑。")
        user_template = refine_cfg.get("user_template", "反馈意见: {feedback}\n原始内容: {content}")
        prompt = user_template.format(feedback=feedback, content=content)
        
        abs_path = self.store._abs(rel_path)
        full_text = ""
        
        try:
             with open(abs_path, "w", encoding="utf-8") as f:
                if hasattr(self.provider, "stream_generate"):
                    # 如果不需要在 CLI 打印密集的流式点，可保持安静
                    for chunk in self.provider.stream_generate(system=system_prompt, prompt=prompt):
                        f.write(chunk)
                        f.flush()
                        full_text += chunk
                else:
                    res = self.provider.generate(system=system_prompt, prompt=prompt)
                    full_text = res.text
                    f.write(full_text)
        except Exception as e:
            self.log.error(f"精修调用失败: {e}")
            raise e
        return full_text

    def _run_consistency_check(self, content: str, step_name: str) -> str:
        # 占位符
        return "一致性检查通过 (Mock功能)。"

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
         draft_single_scene(
             scene_data=scene_node.meta,
             cfg=self.ctx["cfg"],
             prompts=self.ctx["prompts"],
             provider=self.ctx["provider"],
             outline_path=outline_path,
             bible_path=bible_path,
             store=self.ctx["store"],
             rel_path=rel_path,
             log=self.ctx["log"],
             jsonl=self.ctx["jsonl"],
             run_id=self.ctx["run_id"]
         )
         scene_node.content_path = self.ctx["store"]._abs(rel_path)
         scene_node.status = "done"

    def _generate_ab_test(self, scene_node: SceneNode, outline_path: str, bible_path: str):
        self.log.info(f"正在进行 A/B 测试 (生成 {self.num_candidates} 个版本): 场景 {scene_node.id}")
        candidates = []
        futures = {}
        with ThreadPoolExecutor(max_workers=self.num_candidates) as executor:
            for i in range(self.num_candidates):
                cid = f"v{i+1}"
                # Output path is now .json
                rel_path = f"05_drafting/scenes/scene_{scene_node.id:03d}_{cid}.json"
                future = executor.submit(
                     draft_single_scene,
                     scene_data=scene_node.meta,
                     cfg=self.ctx["cfg"],
                     prompts=self.ctx["prompts"],
                     provider=self.ctx["provider"],
                     outline_path=outline_path,
                     bible_path=bible_path,
                     store=self.ctx["store"],
                     rel_path=rel_path,
                     log=None,
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
            winner_id = self._manual_evaluate_ui(scene_node, candidates) # Updated Method

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

    def _manual_evaluate_ui(self, scene_node: SceneNode, candidates: List[SceneCandidate]) -> str:
        options = [f"[{c.id}] 长度: {c.meta.get('char_len', 0)} 字" for c in candidates]
        idx = self.interface.ask_choice(f"场景 {scene_node.id} - 用于 A/B 测试的人工评审:", options)
        return candidates[idx].id
