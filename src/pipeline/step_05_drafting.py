# src/pipeline/step_05_drafting.py
import os
import json
import time
from typing import Dict, Any, Optional
from jinja2 import Template


def draft_single_scene(
    scene_data: Dict[str, Any],
    cfg: Dict[str, Any],
    prompts: Dict[str, Any],
    provider: Any,
    outline_path: str,
    bible_path: str,
    store: Any,
    rel_path: str,
    log: Any = None,
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

    # 如果缺少动态上下文（比如单独调试时），尝试简单的回退
    if not render_ctx:
        if log:
            try:
                log.warning("Missing dynamic_context in scene_data, using fallback.")
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
    writer_tpl = prompts.get("drafting", {}).get("writer", "")
    if not writer_tpl:
        # Fallback prompt
        writer_tpl = "请根据以下细纲写出正文：\n{{ scene_meta.summary }}"

    try:
        user_prompt = Template(writer_tpl).render(**render_ctx)
    except Exception as e:
        if log:
            log.error(f"Template rendering failed: {e}")
        user_prompt = f"Prompt Render Error: {e}\n\nContext: {scene_data}"

    sys_tpl = prompts.get("global_system", "")
    system_prompt = Template(sys_tpl).render(**render_ctx)

    # 3. 准备输出路径
    # rel_path 应以 .json 结尾
    if not rel_path.endswith(".json"):
         rel_path += ".json"
    
    abs_path = store._abs(rel_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)

    if log:
        log.info(f"Drafting scene to {rel_path} ...")

    # 4. 执行生成 (流式收集)
    full_text = ""
    start_time = time.time()

    try:
        if hasattr(provider, "stream_generate"):
            buffer = []
            for chunk in provider.stream_generate(
                system=system_prompt,
                prompt=user_prompt,
                meta={"scene_id": render_ctx.get("scene_id")},
            ):
                buffer.append(chunk)
            full_text = "".join(buffer)
        else:
            full_text = provider.generate(system=system_prompt, prompt=user_prompt).text
    except Exception as e:
        if log:
            log.error(f"Generation failed for {rel_path}: {e}")
        raise e

    # 5. 保存 JSON 和 Sidecar Markdown
    
    # 构造数据对象
    draft_data = {
        "scene_id": render_ctx.get("scene_id"),
        "title": render_ctx.get("scene_title"),
        "content": full_text,
        "meta": scene_data, # 包含 summary 等
        "timestamp": start_time,
        "run_id": run_id
    }
    
    # 保存 JSON
    with open(abs_path, "w", encoding="utf-8") as f:
        json.dump(draft_data, f, indent=2, ensure_ascii=False)
        
    # 保存 Sidecar MD (用于查看)
    md_path = abs_path.replace(".json", ".md")
    with open(md_path, "w", encoding="utf-8") as f:
        header = f"# {render_ctx.get('scene_title', '无标题')}\n\n"
        f.write(header + full_text)

    # 6. 返回纯文本 (WorkflowEngine 需要长度)
    return full_text
