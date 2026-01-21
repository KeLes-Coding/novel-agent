# src/pipeline/step_04_drafting.py
import os
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
    Step 04b: 单场景正文生成 (原子函数)

    该函数由 WorkflowEngine 调用，支持：
    1. Jinja2 Prompt 渲染 (注入 ContextBuilder 构建的 dynamic_context)
    2. 流式生成与实时文件写入
    3. 返回完整文本供后续处理
    """

    # 1. 准备上下文
    # Manager 已经在 run_drafting_loop 中调用 ContextBuilder 并注入到了 scene_data["dynamic_context"]
    # 这里的 scene_data 就是 SceneNode.meta
    render_ctx = scene_data.get("dynamic_context", {})

    # 如果缺少动态上下文（比如单独调试时），尝试简单的回退
    if not render_ctx:
        if log:
            log.warning("Missing dynamic_context in scene_data, using fallback.")
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
    # rel_path 由 WorkflowEngine 传入，可能是 "04_drafting/scenes/scene_001_v1.md"
    abs_path = store._abs(rel_path)
    # 确保存储目录存在
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)

    if log:
        log.info(f"Drafting scene to {rel_path} ...")

    # 4. 执行生成 (流式)
    full_text = ""
    start_time = time.time()

    try:
        with open(abs_path, "w", encoding="utf-8") as f:
            # 写入文件头 (可选，方便阅读)
            header = f"# {render_ctx.get('scene_title', '无标题')}\n\n"
            f.write(header)

            if hasattr(provider, "stream_generate"):
                for chunk in provider.stream_generate(
                    system=system_prompt,
                    prompt=user_prompt,
                    meta={"scene_id": render_ctx.get("scene_id")},
                ):
                    f.write(chunk)
                    f.flush()
                    full_text += chunk
            else:
                text = provider.generate(system=system_prompt, prompt=user_prompt).text
                f.write(text)
                full_text = text

    except Exception as e:
        if log:
            log.error(f"Generation failed for {rel_path}: {e}")
        raise e

    # 5. 返回结果
    # 注意：WorkflowEngine 需要纯正文文本来计算长度等，这里我们返回完整内容
    return full_text
