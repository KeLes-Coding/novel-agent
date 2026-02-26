# src/pipeline/step_02_outline.py
from typing import Dict, Any, List
import os
import json
from jinja2 import Template
from concurrent.futures import ThreadPoolExecutor, as_completed
from utils.json_utils import extract_json

from pipeline.base_step import PipelineStep

class OutlineStep(PipelineStep):
    def run(self) -> Dict[str, Any]:
        """
        Step 02: 大纲生成 (三层架构)
        Layer 1: Structure (JSON) -> 规划分卷
        Layer 2: Expansion (Parallel/JSON) -> 扩写分章 (JSON结构化)
        Layer 3: Analysis (Critique) -> 风险评估
        """
        idea_path = self.context.get("idea_path")
        if not idea_path or not os.path.exists(idea_path):
            raise FileNotFoundError(f"Idea path not found: {idea_path}")

        with open(idea_path, "r", encoding="utf-8") as f:
            idea_context = f.read()

        # --- 1.1 计算预估分卷与章节数 ---
        default_total_words = 300000
        default_avg_words = 1500
        
        target_words = self.cfg.get("content", {}).get("length", {}).get("target_total_words", default_total_words)
        avg_chapter_words = self.cfg.get("content", {}).get("length", {}).get("avg_chapter_words", default_avg_words)
        
        total_chapters_est = max(1, int(target_words / avg_chapter_words))
        
        est_volumes = max(2, min(10, int(total_chapters_est / 15)))
        chapters_per_vol = max(1, int(total_chapters_est / est_volumes))

        if self.log:
            self.log.info(f"Outline Planning: {target_words} words, {avg_chapter_words} words/chap")
            self.log.info(f"Est. Chapters: {total_chapters_est} | Est. Volumes: {est_volumes} | Chaps/Vol: {chapters_per_vol}")

        render_ctx = {
            "idea_context": idea_context,
            "target_total_words": target_words,
            "est_volumes": est_volumes,
            "chapters_per_vol": chapters_per_vol,
        }
        render_ctx.update(self.cfg.get("content", {}))
        render_ctx.update(self.cfg.get("story_constraints", {}))

        base_dir = "02_outline"
        temp_dir = f"{base_dir}/temp"
        os.makedirs(self.store._abs(temp_dir), exist_ok=True)

        sys_tpl = self.prompts.get("global_system", "")
        system_prompt = Template(sys_tpl).render(**render_ctx)

        # ==========================================
        # Layer 1: Structure (流式写入 JSON)
        # ==========================================
        if self.log:
            self.log.info("Layer 1: Designing macro structure (Volumes)...")

        struct_tpl = self.prompts.get("outline", {}).get("structure", "")
        if not struct_tpl:
            struct_tpl = "请规划本书的分卷结构，输出JSON列表。"

        prompt_p1 = Template(struct_tpl).render(**render_ctx)

        json_path = f"{base_dir}/01_structure.json"
        
        volumes_list = []
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
                
                volumes_list = extract_json(full_json_str)
                if not isinstance(volumes_list, list):
                    raise ValueError("Output is not a list")
                
                with open(self.store._abs(json_path), "w", encoding="utf-8") as f:
                    f.write(full_json_str)
                break
                
            except Exception as e:
                if self.log:
                    self.log.warning(f"Layer 1 JSON Parse Attempt {attempt+1}/{max_retries} Failed: {e}")
                if attempt == max_retries - 1:
                    raise RuntimeError("Failed to parse Outline Structure JSON after retries.") from e

        if self.log:
            self.log.info(f"Layer 1 complete. Planned {len(volumes_list)} volumes.")

        # ==========================================
        # Layer 2: Parallel Expansion (返回 JSON)
        # ==========================================
        if self.log:
            self.log.info("Layer 2: Expanding chapters in parallel (JSON output)...")

        expand_tpl = self.prompts.get("outline", {}).get("expansion", "")
        expansion_results = [None] * len(volumes_list)

        def _expand_volume_task(index: int, vol_meta: dict) -> Dict[str, Any]:
            start_id = index * chapters_per_vol + 1
            p_ctx = {
                "volume_id": vol_meta.get("volume_id", index + 1),
                "title": vol_meta.get("title", "未知卷名"),
                "summary": vol_meta.get("summary", ""),
                "idea_context": idea_context,
                "chapters_per_vol": chapters_per_vol,
                "start_chapter_id": start_id,
            }
            prompt_p2 = Template(expand_tpl).render(**p_ctx)

            chapters_data = []
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

                    parsed = extract_json(llm_output)
                    
                    if isinstance(parsed, list):
                        chapters_data = parsed
                    elif isinstance(parsed, dict) and "chapters" in parsed:
                        chapters_data = parsed["chapters"]
                    else:
                        raise ValueError("Parsed JSON is not a list or dict with 'chapters' key")
                    break
                except Exception as e:
                    last_error = e
            
            if not chapters_data:
                 chapters_data = [{
                    "chapter_id": start_id, 
                    "title": "Parse Error", 
                    "summary": f"Failed to parse JSON after 3 attempts. Last Error: {last_error}"
                }]

            vol_full_data = vol_meta.copy()
            vol_full_data["chapters"] = chapters_data
            
            return {
                "data": vol_full_data,
                "text": ""
            }

        with ThreadPoolExecutor(max_workers=min(len(volumes_list), 5)) as executor:
            future_map = {
                executor.submit(_expand_volume_task, i, vol): i
                for i, vol in enumerate(volumes_list)
            }

            for future in as_completed(future_map):
                idx = future_map[future]
                try:
                    res = future.result()
                    expansion_results[idx] = res
                    if self.log:
                        self.log.info(f"  - Volume {idx+1} expansion finished.")
                except Exception as e:
                    error_md = f"## 第 {idx+1} 卷: 生成失败\nError: {e}"
                    expansion_results[idx] = {"data": volumes_list[idx], "text": error_md}
                    if self.log:
                        self.log.error(f"  - Volume {idx+1} failed: {e}")

        # ==========================================
        # Post-Process: Global Renumbering & Markdown Gen
        # ==========================================
        global_chapter_id = 1
        for idx, res in enumerate(expansion_results):
            if not res or "data" not in res:
                continue
                
            vol_data = res["data"]
            chapters = vol_data.get("chapters", [])
            
            for chap in chapters:
                chap["chapter_id"] = global_chapter_id
                global_chapter_id += 1
                
            vol_title = vol_data.get("title", f"Volume {idx+1}")
            vol_summary = vol_data.get("summary", "")
            vol_id = vol_data.get("volume_id", idx+1)
            
            vol_md = f"## 第{vol_id}卷：{vol_title}\n\n**本卷摘要**：{vol_summary}\n\n"
            for chap in chapters:
                c_title = chap.get("title", "无题")
                c_sum = chap.get("summary", "")
                vol_md += f"### 第{chap['chapter_id']}章 {c_title}\n{c_sum}\n\n"
                
            res["text"] = vol_md
            
            temp_file_path = f"{temp_dir}/volume_{idx+1}.md"
            with open(self.store._abs(temp_file_path), "w", encoding="utf-8") as f:
                f.write(vol_md)

        # ==========================================
        # Layer 3: Analysis (流式写入报告)
        # ==========================================
        if self.log:
            self.log.info("Layer 3: Analyzing Outline (Risk & Suggestions)...")

        valid_texts = [r["text"] for r in expansion_results if r and r.get("text")]
        full_outline_text = "\n\n---\n\n".join(valid_texts)

        analysis_tpl = self.prompts.get("outline", {}).get("analysis", "")

        p_ctx_l3 = {"full_outline": full_outline_text}
        prompt_p3 = Template(analysis_tpl).render(**p_ctx_l3)

        analysis_path = f"{base_dir}/02_analysis.md"
        analysis_content = ""

        with open(self.store._abs(analysis_path), "w", encoding="utf-8") as f:
            f.write("# 大纲深度评估报告\n\n")
            if hasattr(self.provider, "stream_generate"):
                try:
                    for chunk in self.provider.stream_generate(system=system_prompt, prompt=prompt_p3):
                        f.write(chunk)
                        f.flush()
                        analysis_content += chunk
                except Exception as e:
                    self.log.warning(f"Analysis generation failed: {e}")
                    f.write(f"\n[Generation Error: {e}]")
            else:
                analysis_content = self.provider.generate(system=system_prompt, prompt=prompt_p3).text
                f.write(analysis_content)

        if self.log:
            self.log.info("Layer 3 Analysis complete.")

        # ==========================================
        # 4. 最终合并与保存
        # ==========================================
        final_full_text = f"# 全书大纲\n\n{full_outline_text}\n\n====================\n\n{analysis_content}"
        final_path = f"{base_dir}/outline.md"
        self.store.save_text(final_path, final_full_text)

        full_outline_data = [r["data"] for r in expansion_results if r]
        json_output_path = f"{base_dir}/outline.json"
        self.store.save_json(json_output_path, full_outline_data)

        return {
            "outline_path": self.store._abs(final_path),
            "outline_text": final_full_text,
            "outline_json_path": self.store._abs(json_output_path),
            "candidates_list": [f"# 全书大纲\n\n{full_outline_text}"],
        }

def run(step_ctx: Dict[str, Any]) -> Dict[str, Any]:
    return OutlineStep(step_ctx).run()
