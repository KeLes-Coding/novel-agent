# src/pipeline/step_01_ideation.py
import os
import json
import re
from typing import Dict, Any, List
from jinja2 import Template
from concurrent.futures import ThreadPoolExecutor, as_completed


from pipeline.base_step import PipelineStep

class IdeationStep(PipelineStep):
    def run(self) -> Dict[str, Any]:
        """
        Step 01: 创意生成 (三层架构 + 全链路流式落盘)
        Layer 1: Brainstorm (JSON) -> stream to file
        Layer 2: Expansion (Parallel) -> stream to temp files
        Layer 3: Analysis (Summary) -> stream to file
        """
        content_cfg = self.cfg.get("content", {})
        num_ideas = content_cfg.get("num_ideas", 5)

        render_ctx = {
            "genre": content_cfg.get("genre", "网文"),
            "tags": content_cfg.get("tags", []),
            "num_ideas": num_ideas,
            "user_prompt": content_cfg.get("user_prompt", ""),
        }
        render_ctx.update(self.cfg.get("story_constraints", {}))

        base_dir = "01_ideation"
        temp_dir = f"{base_dir}/temp"
        os.makedirs(self.store._abs(temp_dir), exist_ok=True)

        sys_tpl = self.prompts.get("global_system", "")
        system_prompt = Template(sys_tpl).render(**render_ctx)

        # ==========================================
        # Layer 1: Brainstorming (流式写入 JSON 文件)
        # ==========================================
        if self.log:
            self.log.info(f"Layer 1: Brainstorming {num_ideas} concepts...")

        brainstorm_tpl = self.prompts.get("ideation", {}).get("brainstorm", "")
        prompt_p1 = Template(brainstorm_tpl).render(**render_ctx)

        json_path = f"{base_dir}/01_brainstorm.json"
        full_json_str = ""

        with open(self.store._abs(json_path), "w", encoding="utf-8") as f:
            if hasattr(self.provider, "stream_generate"):
                for chunk in self.provider.stream_generate(system=system_prompt, prompt=prompt_p1):
                    f.write(chunk)
                    f.flush()
                    full_json_str += chunk
            else:
                full_json_str = self.provider.generate(system=system_prompt, prompt=prompt_p1).text
                f.write(full_json_str)

        raw_json = re.sub(r"^```json", "", full_json_str.strip(), flags=re.MULTILINE)
        raw_json = re.sub(r"^```", "", raw_json, flags=re.MULTILINE)

        try:
            ideas_list = json.loads(raw_json)
        except json.JSONDecodeError as e:
            if self.log:
                self.log.error(f"Layer 1 JSON Parse Failed: {e}")
            raise RuntimeError("Failed to parse Layer 1 JSON.")

        if self.log:
            self.log.info(f"Layer 1 complete. Generated {len(ideas_list)} ideas.")

        # ==========================================
        # Layer 2: Parallel Expansion
        # ==========================================
        if self.log:
            self.log.info("Layer 2: Expanding ideas in parallel (streaming to temp files)...")

        expand_tpl = self.prompts.get("ideation", {}).get("expansion", "")
        candidates_content = [None] * len(ideas_list)

        def _expand_stream_task(index: int, idea_meta: dict) -> str:
            p_ctx = {
                "title": idea_meta.get("title", "未知"),
                "core_concept": idea_meta.get("core_concept", ""),
                **render_ctx,
            }
            prompt_p2 = Template(expand_tpl).render(**p_ctx)
            temp_file_path = f"{temp_dir}/candidate_{index+1}.md"
            abs_temp_path = self.store._abs(temp_file_path)
            full_text_buffer = ""
            header = f"# 方案 {index+1}：《{p_ctx['title']}》\n\n"

            with open(abs_temp_path, "w", encoding="utf-8") as f:
                f.write(header)
                if hasattr(self.provider, "stream_generate"):
                    for chunk in self.provider.stream_generate(system=system_prompt, prompt=prompt_p2):
                        f.write(chunk)
                        f.flush()
                        full_text_buffer += chunk
                else:
                    text = self.provider.generate(system=system_prompt, prompt=prompt_p2).text
                    f.write(text)
                    full_text_buffer = text

            return header + full_text_buffer

        with ThreadPoolExecutor(max_workers=num_ideas) as executor:
            future_map = {
                executor.submit(_expand_stream_task, i, idea): i
                for i, idea in enumerate(ideas_list)
            }

            for future in as_completed(future_map):
                idx = future_map[future]
                try:
                    res_text = future.result()
                    candidates_content[idx] = res_text
                    if self.log:
                        self.log.info(f"  - Idea {idx+1} expansion finished.")
                except Exception as e:
                    error_msg = f"# 方案 {idx+1}: 生成失败\nError: {e}"
                    candidates_content[idx] = error_msg
                    if self.log:
                        self.log.error(f"  - Idea {idx+1} failed: {e}")

        # ==========================================
        # Layer 3: Analysis & Suggestions
        # ==========================================
        if self.log:
            self.log.info("Layer 3: Analyzing and summarizing (Agent 3)...")

        all_candidates_text = "\n\n---\n\n".join(filter(None, candidates_content))

        analysis_tpl = self.prompts.get("ideation", {}).get("analysis", "")
        if not analysis_tpl:
            analysis_tpl = "请对以上方案进行总结评估、风险分析和扩展建议。"

        prompt_p3 = f"{analysis_tpl}\n\n【待评估方案列表】\n{all_candidates_text}"
        try:
            prompt_p3 = Template(analysis_tpl).render(**render_ctx) + f"\n\n【待评估方案列表】\n{all_candidates_text}"
        except:
            pass

        analysis_path = f"{base_dir}/02_analysis.md"
        analysis_content = ""

        with open(self.store._abs(analysis_path), "w", encoding="utf-8") as f:
            f.write("# 深度评估与建议报告\n\n")

            if hasattr(self.provider, "stream_generate"):
                for chunk in self.provider.stream_generate(system=system_prompt, prompt=prompt_p3):
                    f.write(chunk)
                    f.flush()
                    analysis_content += chunk
            else:
                analysis_content = self.provider.generate(system=system_prompt, prompt=prompt_p3).text
                f.write(analysis_content)

        if self.log:
            self.log.info("Layer 3 Analysis complete.")

        final_full_text = all_candidates_text + "\n\n====================\n\n" + analysis_content
        final_path = f"{base_dir}/ideas.txt"
        self.store.save_text(final_path, final_full_text)

        return {
            "idea_path": self.store._abs(final_path),
            "idea_text": final_full_text,
            "candidates_list": candidates_content,
        }

def run(step_ctx: Dict[str, Any]) -> Dict[str, Any]:
    return IdeationStep(step_ctx).run()
