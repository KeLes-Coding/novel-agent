# src/pipeline/step_04_scene_plan.py
from typing import Dict, Any, List
import os
import json
from jinja2 import Template
from concurrent.futures import ThreadPoolExecutor, as_completed
from utils.json_utils import extract_json

from pipeline.base_step import PipelineStep

class ScenePlanStep(PipelineStep):
    def run(self) -> Dict[str, Any]:
        """
        Step 04a: 分场规划 (三层架构)
        Layer 1: Extraction (JSON) -> 拆解场次
        Layer 2: Expansion (Parallel) -> 扩写细纲
        Layer 3: Analysis (Critique) -> 连贯性检查
        """
        outline_path = self.context.get("outline_path")
        bible_path = self.context.get("bible_path")

        with open(outline_path, "r", encoding="utf-8") as f:
            outline_context = f.read()

        bible_summary = ""
        if bible_path and os.path.exists(bible_path):
            with open(bible_path, "r", encoding="utf-8") as f:
                bible_summary = f.read()[:3000]

        avg_chapter_words = self.cfg.get("content", {}).get("length", {}).get("avg_chapter_words", 3000)
        target_words = self.cfg.get("content", {}).get("length", {}).get("target_total_words", 200000)
        est_scenes = max(10, int(target_words / avg_chapter_words))

        render_ctx = {
            "outline_context": outline_context,
            "num_scenes": est_scenes,
            "bible_summary": bible_summary,
        }
        render_ctx.update(self.cfg.get("content", {}))
        render_ctx.update(self.cfg.get("story_constraints", {}))

        base_dir = "04_scene_plan"
        temp_dir = f"{base_dir}/temp"
        os.makedirs(self.store._abs(temp_dir), exist_ok=True)

        sys_tpl = self.prompts.get("global_system", "")
        system_prompt = Template(sys_tpl).render(**render_ctx)

        # ==========================================
        # Layer 1: Extraction (JSON List)
        # ==========================================
        if self.log:
            self.log.info(f"Layer 1: Breaking down outline into ~{est_scenes} scenes...")

        extract_tpl = self.prompts.get("scene_plan", {}).get("extraction", "")
        prompt_p1 = Template(extract_tpl).render(**render_ctx)

        json_path = f"{base_dir}/01_scene_list.json"
        
        scene_list = []
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                full_json_str = ""
                if hasattr(self.provider, "stream_generate"):
                    buffer = []
                    for chunk in self.provider.stream_generate(system=system_prompt, prompt=prompt_p1):
                        buffer.append(chunk)
                    full_json_str = "".join(buffer)
                else:
                    full_json_str = self.provider.generate(system=system_prompt, prompt=prompt_p1).text
                
                scene_list = extract_json(full_json_str)
                if not isinstance(scene_list, list):
                    raise ValueError("Output is not a list")
                
                with open(self.store._abs(json_path), "w", encoding="utf-8") as f:
                    f.write(full_json_str)
                break
                
            except Exception as e:
                if self.log:
                    self.log.warning(f"Layer 1 JSON Parse Attempt {attempt+1}/{max_retries} Failed: {e}")
                if attempt == max_retries - 1:
                    raise RuntimeError("Failed to parse Scene List JSON after retries.") from e

        if self.log:
            self.log.info(f"Layer 1 complete. Defined {len(scene_list)} scenes.")

        # ==========================================
        # Layer 2: Parallel Expansion (详细细纲)
        # ==========================================
        if self.log:
            self.log.info("Layer 2: Expanding scene details in parallel...")

        expand_tpl = self.prompts.get("scene_plan", {}).get("expansion", "")
        scenes_content = [None] * len(scene_list)

        def _expand_scene_task(index: int, meta: dict) -> str:
            p_ctx = {
                "id": meta.get("id", index + 1),
                "title": meta.get("title", f"第{index+1}章"),
                "summary": meta.get("summary", ""),
                "characters": meta.get("characters", []),
                "bible_summary": bible_summary,
            }
            prompt_p2 = Template(expand_tpl).render(**p_ctx)

            temp_file_path = f"{temp_dir}/scene_{index+1}.md"
            abs_temp_path = self.store._abs(temp_file_path)

            header = f"# {p_ctx['id']}. {p_ctx['title']}\n"
            header += f"> 梗概：{p_ctx['summary']}\n"
            header += f"> 人物：{', '.join(p_ctx['characters'])}\n\n"

            full_text_buffer = ""

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

        with ThreadPoolExecutor(max_workers=min(len(scene_list), 10)) as executor:
            future_map = {
                executor.submit(_expand_scene_task, i, s): i
                for i, s in enumerate(scene_list)
            }

            for future in as_completed(future_map):
                idx = future_map[future]
                try:
                    res_text = future.result()
                    scenes_content[idx] = res_text
                    if self.log:
                        self.log.info(f"  - Scene {idx+1} expansion finished.")
                except Exception as e:
                    error_msg = f"# {idx+1}. Error\nGeneration failed: {e}"
                    scenes_content[idx] = error_msg
                    if self.log:
                        self.log.error(f"  - Scene {idx+1} failed: {e}")

        # ==========================================
        # Layer 3: Analysis (连贯性检查)
        # ==========================================
        if self.log:
            self.log.info("Layer 3: Checking consistency...")

        full_plan_text = "\n\n---\n\n".join(filter(None, scenes_content))

        analysis_tpl = self.prompts.get("scene_plan", {}).get("analysis", "")
        prompt_p3 = f"{analysis_tpl}\n\n【完整分场细纲】\n{full_plan_text}"

        analysis_path = f"{base_dir}/02_analysis.md"
        analysis_content = ""

        with open(self.store._abs(analysis_path), "w", encoding="utf-8") as f:
            f.write("# 分场连贯性评估\n\n")
            if hasattr(self.provider, "stream_generate"):
                for chunk in self.provider.stream_generate(system=system_prompt, prompt=prompt_p3):
                    f.write(chunk)
                    f.flush()
                    analysis_content += chunk
            else:
                analysis_content = self.provider.generate(system=system_prompt, prompt=prompt_p3).text
                f.write(analysis_content)

        # ==========================================
        # 4. 最终合并
        # ==========================================
        final_full_text = f"# 全书分场表 (Scene Plan)\n\n{full_plan_text}\n\n====================\n\n{analysis_content}"
        final_path = f"{base_dir}/scene_plan.md"
        self.store.save_text(final_path, final_full_text)

        final_json_path = f"{base_dir}/scene_plan.json"
        self.store.save_json(final_json_path, scene_list)

        candidate_content = f"# 全书分场表\n\n{full_plan_text}"

        return {
            "scene_plan_path": self.store._abs(final_path),
            "scene_plan_text": final_full_text,
            "scene_plan_json_path": self.store._abs(final_json_path),
            "candidates_list": [candidate_content],
            "raw_scene_list": scene_list,
        }

def run(step_ctx: Dict[str, Any]) -> Dict[str, Any]:
    return ScenePlanStep(step_ctx).run()
