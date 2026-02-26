# src/pipeline/step_05_drafting.py
import os
import json
import time
from typing import Dict, Any, Optional
from jinja2 import Template


from pipeline.base_step import PipelineStep

class DraftingStep(PipelineStep):
    def draft_single_scene(
        self,
        scene_data: Dict[str, Any],
        outline_path: str,
        bible_path: str,
        rel_path: str,
        jsonl: Any = None,
        run_id: str = "",
    ) -> str:
        """
        Step 05: 单场景正文生成 (原子函数)
    
        该函数由 WorkflowEngine 调用，支持：
        1. Jinja2 Prompt 渲染 (注入 ContextBuilder 构建的 dynamic_context)
        2. 流式生成
        3. JSON 格式化输出 (保存 .json 和 .md 副本)
        4. 返回完整文本供后续处理
        """
        # 1. 准备上下文
        render_ctx = scene_data.get("dynamic_context", {})
    
        if not render_ctx:
            if self.log:
                try:
                    self.log.warning("Missing dynamic_context in scene_data, using fallback.")
                except:
                    pass
            render_ctx = {
                "bible": "（未加载设定集）",
                "outline": "（未加载大纲）",
                "prev_context": "（无前情提要）",
                "scene_id": scene_data.get("id", "?"),
                "scene_title": scene_data.get("title", "Unknown"),
                "scene_meta": scene_data,
            }
    
        # 2. 渲染 Prompt
        writer_tpl = self.prompts.get("drafting", {}).get("writer", "")
        if not writer_tpl:
            writer_tpl = "请根据以下细纲写出正文：\n{{ scene_meta.summary }}"
    
        try:
            user_prompt = Template(writer_tpl).render(**render_ctx)
        except Exception as e:
            if self.log:
                self.log.error(f"Template rendering failed: {e}")
            user_prompt = f"Prompt Render Error: {e}\n\nContext: {scene_data}"
    
        sys_tpl = self.prompts.get("global_system", "")
        system_prompt = Template(sys_tpl).render(**render_ctx)
    
        # 3. 准备输出路径
        if not rel_path.endswith(".json"):
             rel_path += ".json"
        
        abs_path = self.store._abs(rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    
        if self.log:
            self.log.info(f"Drafting scene to {rel_path} ...")
    
        # 4. 准备输出 (Sidecar MD stream + Console)
        md_path = abs_path.replace(".json", ".md")
        
        # 5. 执行生成 (流式收集)
        full_text = ""
        start_time = time.time()
        
        try:
            if hasattr(self.provider, "stream_generate"):
                with open(md_path, "w", encoding="utf-8") as md_file:
                    header = f"# {render_ctx.get('scene_title', '无标题')}\n\n"
                    md_file.write(header)
                    md_file.flush()
                    
                    if self.log:
                        self.log.info(f"Start streaming to {md_path}...")
                    
                    buffer = []
                    for chunk in self.provider.stream_generate(
                        system=system_prompt,
                        prompt=user_prompt,
                        meta={"scene_id": render_ctx.get("scene_id")},
                    ):
                        md_file.write(chunk)
                        md_file.flush()
                        buffer.append(chunk)
                    
                    print("\n")
                    full_text = "".join(buffer)
            else:
                full_text = self.provider.generate(system=system_prompt, prompt=user_prompt).text
                with open(md_path, "w", encoding="utf-8") as md_file:
                    header = f"# {render_ctx.get('scene_title', '无标题')}\n\n"
                    md_file.write(header + full_text)
    
        except Exception as e:
            if self.log:
                self.log.error(f"Generation failed for {rel_path}: {e}")
            raise e
    
        # 6. 保存 JSON
        draft_data = {
            "scene_id": render_ctx.get("scene_id"),
            "timestamp": start_time,
            "run_id": run_id,
            "title": render_ctx.get("scene_title"),
            "content": full_text,
            "meta": scene_data,
        }
        
        with open(abs_path, "w", encoding="utf-8") as f:
            json.dump(draft_data, f, indent=2, ensure_ascii=False)
    
        return full_text

    def run(self) -> Dict[str, Any]:
        """
        Since drafting is managed scene-by-scene via workflow.py (A/B testing, HITL),
        the root run() mapping is not primarily used for bulk execution here.
        But we implement it to satisfy the PipelineStep interface.
        """
        raise NotImplementedError("Drafting is orchestrated by WorkflowEngine per scene. Please use draft_single_scene().")
