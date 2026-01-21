# src/core/workflow.py
import os
import time
import re
from typing import List, Dict, Any, Callable, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from core.state import SceneNode, SceneCandidate, ArtifactCandidate
from pipeline.step_04_drafting import draft_single_scene
from utils.notifier import Notifier


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

        self.notifier = Notifier(self.cfg, run_id=self.run_id)

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
        通用的 HITL (Human-In-The-Loop) 步骤执行器
        """
        # 1. 检查是否已经有候选项
        current_candidates = getattr(self.state, candidates_field, [])

        if not current_candidates:
            self.log.info(f"[{step_name}] 正在生成候选项...")
            try:
                new_candidates = generate_fn()
                setattr(self.state, candidates_field, new_candidates)
                self.state.save()
            except Exception as e:
                self.log.error(f"生成失败: {e}")
                raise e

        selected_candidate = None

        while True:
            candidates = getattr(self.state, candidates_field)

            # 非交互模式
            if not self.interactive:
                self.log.info(f"[{step_name}] 非交互模式，自动选择第一个候选项。")
                selected_candidate = candidates[0]
                break

            # 2. 通知
            self.notifier.notify(
                title=f"需要介入: {step_name}",
                message=f"已生成 {len(candidates)} 个版本，请审核并选择。",
                payload={"step": step_name},
            )

            self.state.system_status = "paused_for_input"
            self.state.save()

            # 3. 交互菜单
            print(f"\n>>> [人机协作 HITL] 当前步骤: {step_name} <<<")
            for idx, c in enumerate(candidates):
                preview = c.content[:100].replace("\n", " ") + "..."
                tag = f"[{c.id}]"
                print(f"  {idx+1}. {tag:<15} {preview}")

            print("\n指令列表:")
            print("  <数字>       : 选择此候选项 (例如输入 '1')")
            print("  r           : 重写 (丢弃当前所有，重新生成)")
            print("  e <数字>     : 精修 (针对选定版本进行【保留原意】的修改)")
            print("  u <路径>     : 上传本地文件")

            choice = input("请输入指令 > ").strip()

            # A. Reroll
            if choice.lower() == "r":
                self.log.info("用户请求重写，正在重新生成所有候选项...")
                setattr(self.state, candidates_field, [])
                self.state.save()
                return self.run_step_with_hitl(
                    step_name, generate_fn, candidates_field, selected_path_field
                )

            # B. Upload
            elif choice.lower().startswith("u "):
                path = choice[2:].strip()
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        content = f.read()
                    user_cand = ArtifactCandidate(
                        id="user_upload", content=content, selected=True
                    )
                    candidates.append(user_cand)
                    setattr(self.state, candidates_field, candidates)
                    self.state.save()
                    print(f"✅ 文件已上传，作为第 {len(candidates)} 个候选项添加。")
                else:
                    print(f"❌ 文件未找到: {path}")

            # C. Edit/Refine (核心修改逻辑)
            elif choice.lower().startswith("e "):
                try:
                    parts = choice.split()
                    if len(parts) < 2:
                        print("❌ 用法错误，请输入: e <数字>")
                        continue
                    target_idx = int(parts[1]) - 1

                    if 0 <= target_idx < len(candidates):
                        target_cand = candidates[target_idx]

                        # 进入多轮精修会话
                        refined_cand = self._interactive_refine_session(
                            target_cand, step_name
                        )

                        if refined_cand:
                            # 将精修后的结果作为一个新的选项加入列表
                            # 这样用户可以对比原版和精修版
                            candidates.append(refined_cand)
                            setattr(self.state, candidates_field, candidates)
                            self.state.save()
                            print(
                                f"✅ 精修完成！结果已保存为新的候选项: {len(candidates)}"
                            )
                            print(
                                "（如果不满意，你可以继续对原版进行 'e' 操作，或者选择旧版本）"
                            )
                        else:
                            print("🚫 精修已取消。")
                    else:
                        print("❌ 无效的编号。")
                except Exception as e:
                    print(f"❌ 处理精修指令时出错: {e}")

            # D. Select
            elif choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(candidates):
                    selected_candidate = candidates[idx]
                    break
                else:
                    print("❌ 无效的编号。")
            else:
                print("❌ 无效指令。")

        selected_candidate.selected = True
        self.state.system_status = "running"
        self.state.save()
        return selected_candidate

    def _interactive_refine_session(
        self, base_cand: ArtifactCandidate, step_name: str
    ) -> Optional[ArtifactCandidate]:
        """
        交互式精修会话（支持结构化分块编辑）
        """
        current_content = base_cand.content

        # 准备目录
        refine_dir = f"{step_name}/refinements"
        try:
            os.makedirs(self.store._abs(refine_dir), exist_ok=True)
        except Exception:
            pass

        print(f"\n" + "=" * 50)
        print(f"🔧 进入结构化精修模式 (版本: {base_cand.id})")

        # 自动解析章节/分块
        sections = self._parse_sections(current_content)
        has_structure = len(sections) > 1

        while True:
            # 动态显示状态
            print("\n" + "-" * 30)
            print(f"📄 当前全文长度: {len(current_content)} 字")
            if has_structure:
                print(
                    f"📑 识别到 {len(sections)} 个小节 (如: {sections[0][0]}, {sections[1][0]}...)"
                )

            print("\n可用指令:")
            print("  ls            : 列出所有小节标题")
            print("  mod <N>       : 修改第 N 个小节 (推荐)")
            print("  mod all       : 修改全文 (慎用)")
            print("  check         : 运行一致性检查 (评估当前版本)")
            print("  show <N|all>  : 查看内容")
            print("  save          : 保存并退出")
            print("  cancel        : 放弃并退出")
            print("-" * 30)

            cmd = input("指令 > ").strip()

            if cmd in ["q", "quit", "cancel", "exit"]:
                return None

            if cmd in ["ok", "save", "done"]:
                new_id = f"{base_cand.id}_refined_{int(time.time())}"
                return ArtifactCandidate(id=new_id, content=current_content)

            # 列出小节
            if cmd == "ls" and has_structure:
                print("\n--- 目录结构 ---")
                for i, (title, _) in enumerate(sections):
                    print(f"  {i+1}. {title}")
                continue

            # 查看内容
            if cmd.startswith("show"):
                parts = cmd.split()
                target = parts[1] if len(parts) > 1 else "all"
                if target.isdigit() and has_structure:
                    idx = int(target) - 1
                    if 0 <= idx < len(sections):
                        print(
                            f"\n--- 小节: {sections[idx][0]} ---\n{sections[idx][1]}\n--- 结束 ---"
                        )
                    else:
                        print("❌ 索引越界")
                else:
                    print(
                        f"\n--- 全文预览 (前500字) ---\n{current_content[:500]}...\n--- 结束 ---"
                    )
                continue

            # 修改逻辑
            if cmd.startswith("mod "):
                target = cmd.split(" ", 1)[1].strip()

                # 确定要修改的文本范围
                target_text = ""
                section_idx = -1

                if target == "all":
                    target_text = current_content
                    print("⚠️ 正在针对全文进行修改，这可能会导致长文本质量下降。")
                elif target.isdigit() and has_structure:
                    section_idx = int(target) - 1
                    if 0 <= section_idx < len(sections):
                        title, body = sections[section_idx]
                        target_text = body
                        print(f"🎯 选中主要目标: 【{title}】")
                    else:
                        print("❌ 索引越界")
                        continue
                else:
                    print("❌ 无效的目标。请使用 'mod 1' 或 'mod all'")
                    continue

                # 获取修改意见
                feedback = input("请输入修改意见 > ").strip()
                if not feedback:
                    continue

                # 执行 LLM 修改
                timestamp = int(time.time())
                file_name = f"{base_cand.id}_mod_{target}_{timestamp}.md"
                rel_path = f"{refine_dir}/{file_name}"

                print(f"⏳ AI 正在修改... (流式写入: {rel_path})")

                try:
                    revised_part = self._call_llm_refine(
                        target_text, feedback, rel_path
                    )

                    # 应用修改
                    if target == "all":
                        current_content = revised_part
                        # 重新解析结构
                        sections = self._parse_sections(current_content)
                        has_structure = len(sections) > 1
                    elif section_idx >= 0:
                        # 替换特定小节
                        current_content = self._replace_section(
                            current_content, sections, section_idx, revised_part
                        )
                        # 更新缓存的 sections 结构
                        sections = self._parse_sections(current_content)

                    print("\n✅ 修改已应用。")

                    # 提示一致性风险
                    if target != "all":
                        print(
                            "⚠️ 提示: 你修改了局部内容，建议运行 'check' 检查是否与上下文冲突。"
                        )

                except Exception as e:
                    print(f"❌ 修改失败: {e}")

            # 一致性检查
            if cmd == "check":
                print("🕵️ 正在运行一致性/风险评估...")
                report = self._run_consistency_check(current_content, step_name)
                print("\n--- 评估报告 ---")
                print(report)
                print("----------------")

    def _parse_sections(self, content: str) -> List[Tuple[str, str]]:
        """
        简单解析 Markdown 结构
        返回列表: [(标题, 内容含标题), ...]
        """
        # 匹配 ## 或 ### 开头的标题
        # 使用正则 split，保留分隔符
        pattern = r"(^|\n)(#{2,3}\s+.*)"
        parts = re.split(pattern, content)

        sections = []
        if len(parts) < 2:
            return []

        # parts[0] 是导语，通常为空或文档头
        # parts[1] 是分隔符(\n), parts[2] 是标题, parts[3] 是正文...

        # 简单的合并逻辑：找到标题，与其后的内容合并
        current_title = "导语/前言"
        current_body = parts[0]

        # 如果第一段就有内容，先存导语
        if current_body.strip():
            sections.append(("导语", current_body))

        i = 1
        while i < len(parts) - 1:
            sep = parts[i]  # 换行符
            title_line = parts[i + 1].strip()  # 标题行
            body_text = parts[i + 2] if i + 2 < len(parts) else ""

            full_section = f"{sep}{title_line}{body_text}"
            clean_title = title_line.lstrip("#").strip()

            sections.append((clean_title, full_section))
            i += 3

        return sections

    def _replace_section(
        self,
        full_content: str,
        sections: List[Tuple[str, str]],
        idx: int,
        new_text: str,
    ) -> str:
        """
        将全文中的第 idx 个 section 替换为 new_text
        """
        # 重组全文：prefix + new_text + suffix
        # 这需要精准的定位。由于 sections 是按顺序解析的，我们可以重新拼接

        # 方案：直接利用 sections 列表重组
        # 更新 sections 列表中的内容
        sections[idx] = (sections[idx][0], new_text)  # 更新元组

        # 重新拼接所有内容
        # 注意：sections[i][1] 包含了前置换行符，所以直接 join 即可
        # 但导语部分可能没有前置换行，需注意

        # 为了稳健，我们简单暴力拼接
        new_full = ""
        for title, body in sections:
            new_full += body

        return new_full

    def _call_llm_refine(self, content: str, feedback: str, rel_path: str) -> str:
        # (保持之前的实现不变)
        refine_cfg = self.prompts.get("refinement", {})
        system_prompt = refine_cfg.get(
            "system",
            "你是一位编辑。请严格基于提供的【原始内容】进行修改，严禁重写故事走向。只根据用户的【修改意见】进行调整。",
        )
        user_template = refine_cfg.get(
            "user_template",
            "【修改意见】\n{feedback}\n\n【原始内容】\n{content}\n\n请输出修改后的完整内容：",
        )

        prompt = user_template.format(feedback=feedback, content=content)
        abs_path = self.store._abs(rel_path)
        full_text = ""

        try:
            with open(abs_path, "w", encoding="utf-8") as f:
                if hasattr(self.provider, "stream_generate"):
                    print("Writing stream: ", end="", flush=True)
                    for chunk in self.provider.stream_generate(
                        system=system_prompt, prompt=prompt
                    ):
                        f.write(chunk)
                        f.flush()
                        full_text += chunk
                        print(".", end="", flush=True)
                    print(" Done.")
                else:
                    res = self.provider.generate(system=system_prompt, prompt=prompt)
                    full_text = res.text
                    f.write(full_text)
        except Exception as e:
            self.log.error(f"Refinement stream failed: {e}")
            raise e

        return full_text

    def _call_llm_refine(self, content: str, feedback: str, rel_path: str) -> str:
        """调用 Provider 执行修改，并将结果实时流式写入指定的本地文件"""

        # 1. 读取 Prompts 配置
        refine_cfg = self.prompts.get("refinement", {})
        system_prompt = refine_cfg.get(
            "system",
            "你是一位编辑。请严格基于提供的【原始内容】进行修改，严禁重写故事走向。只根据用户的【修改意见】进行调整。",
        )
        user_template = refine_cfg.get(
            "user_template",
            "【修改意见】\n{feedback}\n\n【原始内容】\n{content}\n\n请输出修改后的完整内容：",
        )

        # 2. 组装 Prompt
        prompt = user_template.format(feedback=feedback, content=content)

        # 3. 准备写入
        abs_path = self.store._abs(rel_path)
        full_text = ""

        # 4. 执行流式生成与写入
        try:
            with open(abs_path, "w", encoding="utf-8") as f:
                # 优先使用流式接口
                if hasattr(self.provider, "stream_generate"):
                    # 可以在控制台显示一个小进度指示器
                    print("Writing stream: ", end="", flush=True)
                    for chunk in self.provider.stream_generate(
                        system=system_prompt, prompt=prompt
                    ):
                        f.write(chunk)
                        f.flush()  # 确保实时落盘
                        full_text += chunk
                        # 简单的视觉反馈
                        # print(".", end="", flush=True)
                    print(" Done.")
                else:
                    # 降级处理
                    res = self.provider.generate(system=system_prompt, prompt=prompt)
                    full_text = res.text
                    f.write(full_text)

        except Exception as e:
            self.log.error(f"Refinement stream failed: {e}")
            raise e

        return full_text

    # ... (process_scene, _generate_single, _generate_ab_test 等方法保持不变，需保留) ...
    # 为了完整性，这里保留 process_scene 等核心方法的引用
    def process_scene(self, scene_node: SceneNode, outline_path: str, bible_path: str):
        if not self.branching_enabled or self.num_candidates <= 1:
            self._generate_single(scene_node, outline_path, bible_path)
        else:
            self._generate_ab_test(scene_node, outline_path, bible_path)

    def _generate_single(
        self, scene_node: SceneNode, outline_path: str, bible_path: str
    ):
        self.log.info(f"[Workflow] 正在生成单线草稿: Scene {scene_node.id}")
        rel_path = f"04_drafting/scenes/scene_{scene_node.id:03d}.md"
        content = draft_single_scene(
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
            run_id=self.ctx["run_id"],
        )
        scene_node.content_path = self.ctx["store"]._abs(rel_path)
        scene_node.status = "done"

    def _generate_ab_test(
        self, scene_node: SceneNode, outline_path: str, bible_path: str
    ):
        # ... (保留原有的 A/B 测试逻辑，建议将内部日志也稍微汉化一下) ...
        self.log.info(
            f"[Workflow] 正在进行 A/B 测试 (生成 {self.num_candidates} 个版本): Scene {scene_node.id}"
        )

        candidates = []
        futures = {}

        with ThreadPoolExecutor(max_workers=self.num_candidates) as executor:
            for i in range(self.num_candidates):
                candidate_id = f"v{i+1}"
                rel_path = (
                    f"04_drafting/scenes/scene_{scene_node.id:03d}_{candidate_id}.md"
                )

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
                    run_id=self.ctx["run_id"],
                )
                futures[future] = (candidate_id, rel_path)

            for f in as_completed(futures):
                cid, rpath = futures[f]
                try:
                    text = f.result()
                    candidates.append(
                        SceneCandidate(
                            id=cid,
                            content_path=self.ctx["store"]._abs(rpath),
                            meta={"char_len": len(text)},
                        )
                    )
                    self.log.info(f"  - 版本 {cid} 生成完毕 ({len(text)} 字)")
                except Exception as e:
                    self.log.error(f"  - 版本 {cid} 失败: {e}")

        scene_node.candidates = candidates

        if not candidates:
            raise RuntimeError(f"场景 {scene_node.id} 的所有候选版本均生成失败")

        # 2. 评估与选择
        if self.selection_mode == "auto":
            winner_id = self._auto_evaluate(scene_node, candidates, bible_path)
        else:
            winner_id = self._manual_evaluate(scene_node, candidates)

        # 3. 固化结果
        selected = next((c for c in candidates if c.id == winner_id), candidates[0])
        selected.selected = True
        scene_node.selected_candidate_id = winner_id
        scene_node.content_path = selected.content_path

        standard_path = f"04_drafting/scenes/scene_{scene_node.id:03d}.md"
        with open(selected.content_path, "r", encoding="utf-8") as src:
            self.ctx["store"].save_text(standard_path, src.read())
        scene_node.content_path = self.ctx["store"]._abs(standard_path)
        scene_node.status = "done"

        self.log.info(f"[Workflow] 最终选定版本: {winner_id}")

    def _auto_evaluate(
        self, scene_node: SceneNode, candidates: List[SceneCandidate], bible_path: str
    ) -> str:
        self.log.info("[Workflow] 正在进行自动评估...")
        # ... (保留原逻辑，仅修改少量日志) ...
        # (代码略，保持原样即可，核心逻辑不需要动)
        return candidates[0].id

    def _manual_evaluate(
        self, scene_node: SceneNode, candidates: List[SceneCandidate]
    ) -> str:
        print(f"\n>>> 场景 {scene_node.id} A/B 测试人工审核 <<<")
        for c in candidates:
            print(f"[{c.id}] 路径: {c.content_path} (长度: {c.meta.get('char_len')})")

        choice = input("请输入选定的版本 ID (如 v1): ").strip()
        if any(c.id == choice for c in candidates):
            return choice
        print("输入无效，默认选择 v1")
        return "v1"
