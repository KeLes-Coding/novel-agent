# src/pipeline/step_03_bible.py
import os
import json
import re
from typing import Dict, Any, List
from jinja2 import Template
from concurrent.futures import ThreadPoolExecutor, as_completed
from utils.json_utils import extract_json


from pipeline.base_step import PipelineStep

class BibleStep(PipelineStep):
    def run(self) -> Dict[str, Any]:
        """
        Step 03: 设定集生成 (三层架构)
        Layer 1: Extraction (JSON) -> 提取需设定的名单
        Layer 2: Expansion (Parallel) -> 并行生成详细档案 (JSON + MD)
        Layer 3: Analysis (Critique) -> 一致性检查
        """
        outline_path = self.context.get("outline_path")
        if not outline_path or not os.path.exists(outline_path):
            raise FileNotFoundError(f"Outline path not found: {outline_path}")

        with open(outline_path, "r", encoding="utf-8") as f:
            outline_context = f.read()

        outline_summary = outline_context[:3000]

        render_ctx = {
            "outline_context": outline_context,
            "outline_summary": outline_summary,
        }
        render_ctx.update(self.cfg.get("content", {}))
        render_ctx.update(self.cfg.get("story_constraints", {}))

        base_dir = "03_bible"
        temp_dir = f"{base_dir}/temp"
        os.makedirs(self.store._abs(temp_dir), exist_ok=True)

        sys_tpl = self.prompts.get("global_system", "")
        system_prompt = Template(sys_tpl).render(**render_ctx)

        # ==========================================
        # Layer 1: Extraction (流式写入 JSON)
        # ==========================================
        if self.log:
            self.log.info("Layer 1: Extracting entities from outline...")

        extract_tpl = self.prompts.get("bible", {}).get("extraction", "")
        prompt_p1 = Template(extract_tpl).render(**render_ctx)

        json_path = f"{base_dir}/01_entity_list.json"
        
        entities_data = {}
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
                
                entities_data = extract_json(full_json_str)
                if not isinstance(entities_data, dict):
                    raise ValueError("Output is not a dict")
                
                with open(self.store._abs(json_path), "w", encoding="utf-8") as f:
                    f.write(full_json_str)
                break
                
            except Exception as e:
                if self.log:
                    self.log.warning(f"Layer 1 JSON Parse Attempt {attempt+1}/{max_retries} Failed: {e}")
                if attempt == max_retries - 1:
                    raise RuntimeError("Failed to parse Entity List JSON after retries.") from e

        tasks = []
        for category, names in entities_data.items():
            if isinstance(names, list):
                for name in names:
                    tasks.append({"category": category, "name": name})

        if self.log:
            self.log.info(f"Layer 1 complete. Found {len(tasks)} entities to profile.")

        # ==========================================
        # Layer 2: Parallel Expansion (JSON + Markdown)
        # ==========================================
        if self.log:
            self.log.info("Layer 2: Creating profiles in parallel (JSON output)...")

        expand_tpl = self.prompts.get("bible", {}).get("expansion", "")
        expansion_results = [None] * len(tasks)

        def _expand_profile_task(index: int, task: dict) -> Dict[str, Any]:
            p_ctx = {
                "category": task["category"],
                "name": task["name"],
                "outline_summary": outline_summary,
            }
            prompt_p2 = Template(expand_tpl).render(**p_ctx)

            profile_data = {}
            last_error = None
            
            for attempt in range(3):
                try:
                    llm_output = ""
                    if hasattr(self.provider, "stream_generate"):
                        chunk_buffer = []
                        for chunk in self.provider.stream_generate(system=system_prompt, prompt=prompt_p2):
                            chunk_buffer.append(chunk)
                        llm_output = "".join(chunk_buffer)
                    else:
                        llm_output = self.provider.generate(system=system_prompt, prompt=prompt_p2).text
                    
                    profile_data = extract_json(llm_output)
                    if not isinstance(profile_data, dict):
                         raise ValueError("Output is not a dict")
                    break
                except Exception as e:
                    last_error = e
            
            if not profile_data:
                profile_data = {
                    "name": task["name"],
                    "category": task["category"],
                    "base_info": "Unknown",
                    "traits": "Parse Failed",
                    "backstory": f"Failed to parse JSON. Error: {last_error}",
                    "role": "Unknown",
                    "highlight": "Unknown"
                }

            name = profile_data.get("name", task["name"])
            cat = profile_data.get("category", task["category"])
            
            md_text = f"## {cat}档案：{name}\n\n"
            md_text += f"**基础信息**：{profile_data.get('base_info', '')}\n\n"
            md_text += f"**核心特质**：{profile_data.get('traits', '')}\n\n"
            md_text += f"**背景故事**：{profile_data.get('backstory', '')}\n\n"
            md_text += f"**关联角色**：{profile_data.get('role', '')}\n\n"
            md_text += f"**高光时刻**：{profile_data.get('highlight', '')}\n\n"

            safe_name = re.sub(r'[\\/*?:"<>|]', "", name)
            temp_file_path = f"{temp_dir}/{cat}_{safe_name}.md"
            with open(self.store._abs(temp_file_path), "w", encoding="utf-8") as f:
                f.write(md_text)

            return {
                "data": profile_data,
                "text": md_text
            }

        with ThreadPoolExecutor(max_workers=min(len(tasks), 8)) as executor:
            future_map = {
                executor.submit(_expand_profile_task, i, t): i for i, t in enumerate(tasks)
            }

            for future in as_completed(future_map):
                idx = future_map[future]
                try:
                    res = future.result()
                    expansion_results[idx] = res
                    if self.log:
                        self.log.info(f"  - Profile for '{tasks[idx]['name']}' created.")
                except Exception as e:
                    error_msg = f"## 档案生成失败: {tasks[idx]['name']}\nError: {e}"
                    expansion_results[idx] = {"data": {}, "text": error_msg}
                    if self.log:
                        self.log.error(f"  - Profile '{tasks[idx]['name']}' failed: {e}")

        # ==========================================
        # Layer 3: Analysis (流式写入报告)
        # ==========================================
        if self.log:
            self.log.info("Layer 3: Checking consistency...")

        valid_results = [r for r in expansion_results if r and r.get("data")]
        
        all_profiles_data = [r["data"] for r in valid_results]
        bible_json_path = f"{base_dir}/bible.json"
        self.store.save_json(bible_json_path, all_profiles_data)

        full_bible_text = "\n\n---\n\n".join([r["text"] for r in expansion_results if r])

        analysis_tpl = self.prompts.get("bible", {}).get("analysis", "")
        prompt_p3 = f"{analysis_tpl}\n\n【完整设定集】\n{full_bible_text}\n\n【大纲摘要】\n{outline_summary}"

        analysis_path = f"{base_dir}/02_analysis.md"
        analysis_content = ""

        with open(self.store._abs(analysis_path), "w", encoding="utf-8") as f:
            f.write("# 世界观一致性评估报告\n\n")
            if hasattr(self.provider, "stream_generate"):
                try:
                    for chunk in self.provider.stream_generate(system=system_prompt, prompt=prompt_p3):
                        f.write(chunk)
                        f.flush()
                        analysis_content += chunk
                except Exception as e:
                     f.write(f"\n[Analysis Generation Error: {e}]")
            else:
                analysis_content = self.provider.generate(system=system_prompt, prompt=prompt_p3).text
                f.write(analysis_content)

        # ==========================================
        # 4. 最终合并
        # ==========================================
        final_full_text = f"# 全书设定集 (Bible)\n\n{full_bible_text}\n\n====================\n\n{analysis_content}"
        final_path = f"{base_dir}/bible.md"
        self.store.save_text(final_path, final_full_text)

        candidate_content = f"# 全书设定集\n\n{full_bible_text}"

        return {
            "bible_path": self.store._abs(final_path),
            "bible_text": final_full_text,
            "candidates_list": [candidate_content],
            "bible_json_path": self.store._abs(bible_json_path)
        }

def run(step_ctx: Dict[str, Any]) -> Dict[str, Any]:
    return BibleStep(step_ctx).run()
