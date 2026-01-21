# src/pipeline/step_04_scene_plan.py
import os
import json
import re
from typing import Dict, Any, List
from jinja2 import Template
from concurrent.futures import ThreadPoolExecutor, as_completed


def run(step_ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Step 04a: 分场规划 (三层架构)
    Layer 1: Extraction (JSON) -> 拆解场次
    Layer 2: Expansion (Parallel) -> 扩写细纲
    Layer 3: Analysis (Critique) -> 连贯性检查
    """
    cfg = step_ctx["cfg"]
    provider = step_ctx["provider"]
    store = step_ctx["store"]
    prompts = step_ctx["prompts"]
    log = step_ctx.get("log")

    # 1. 准备输入上下文
    outline_path = step_ctx.get("outline_path")
    bible_path = step_ctx.get("bible_path")

    with open(outline_path, "r", encoding="utf-8") as f:
        outline_context = f.read()

    # 读取 Bible 摘要 (截取前3000字避免 overflow，或者让 ContextBuilder 专门生成一个摘要)
    bible_summary = ""
    if bible_path and os.path.exists(bible_path):
        with open(bible_path, "r", encoding="utf-8") as f:
            bible_summary = f.read()[:3000]

    # 估算场次数量 (例如：总字数 / 3000字每章)
    target_words = (
        cfg.get("content", {}).get("length", {}).get("target_total_words", 200000)
    )
    est_scenes = max(10, int(target_words / 3000))

    render_ctx = {
        "outline_context": outline_context,
        "num_scenes": est_scenes,
        "bible_summary": bible_summary,
    }

    # 路径准备
    base_dir = "04_scene_plan"
    temp_dir = f"{base_dir}/temp"
    os.makedirs(store._abs(temp_dir), exist_ok=True)

    sys_tpl = prompts.get("global_system", "")
    system_prompt = Template(sys_tpl).render(**render_ctx)

    # ==========================================
    # Layer 1: Extraction (JSON List)
    # ==========================================
    if log:
        log.info(f"Layer 1: Breaking down outline into ~{est_scenes} scenes...")

    extract_tpl = prompts.get("scene_plan", {}).get("extraction", "")
    prompt_p1 = Template(extract_tpl).render(**render_ctx)

    json_path = f"{base_dir}/01_scene_list.json"
    full_json_str = ""

    with open(store._abs(json_path), "w", encoding="utf-8") as f:
        if hasattr(provider, "stream_generate"):
            for chunk in provider.stream_generate(
                system=system_prompt, prompt=prompt_p1
            ):
                f.write(chunk)
                f.flush()
                full_json_str += chunk
        else:
            full_json_str = provider.generate(
                system=system_prompt, prompt=prompt_p1
            ).text
            f.write(full_json_str)

    # 解析 JSON
    raw_json = re.sub(r"^```json", "", full_json_str.strip(), flags=re.MULTILINE)
    raw_json = re.sub(r"^```", "", raw_json, flags=re.MULTILINE)

    try:
        scene_list = json.loads(raw_json)
        if not isinstance(scene_list, list):
            raise ValueError("Output is not a list")
    except Exception as e:
        if log:
            log.error(f"Layer 1 JSON Parse Failed: {e}")
        raise RuntimeError("Failed to parse Scene List JSON.")

    if log:
        log.info(f"Layer 1 complete. Defined {len(scene_list)} scenes.")

    # ==========================================
    # Layer 2: Parallel Expansion (详细细纲)
    # ==========================================
    if log:
        log.info("Layer 2: Expanding scene details in parallel...")

    expand_tpl = prompts.get("scene_plan", {}).get("expansion", "")
    scenes_content = [None] * len(scene_list)

    def _expand_scene_task(index: int, meta: dict) -> str:
        """扩写单场细纲"""
        p_ctx = {
            "id": meta.get("id", index + 1),
            "title": meta.get("title", f"第{index+1}章"),
            "summary": meta.get("summary", ""),
            "characters": meta.get("characters", []),
            "bible_summary": bible_summary,
        }
        prompt_p2 = Template(expand_tpl).render(**p_ctx)

        temp_file_path = f"{temp_dir}/scene_{index+1}.md"
        abs_temp_path = store._abs(temp_file_path)

        # 构造结构化头部，方便 Manager 解析回 SceneNode
        # 格式：# ID. Title
        # 元数据块
        header = f"# {p_ctx['id']}. {p_ctx['title']}\n"
        header += f"> 梗概：{p_ctx['summary']}\n"
        header += f"> 人物：{', '.join(p_ctx['characters'])}\n\n"

        full_text_buffer = ""

        with open(abs_temp_path, "w", encoding="utf-8") as f:
            f.write(header)

            if hasattr(provider, "stream_generate"):
                for chunk in provider.stream_generate(
                    system=system_prompt, prompt=prompt_p2
                ):
                    f.write(chunk)
                    f.flush()
                    full_text_buffer += chunk
            else:
                text = provider.generate(system=system_prompt, prompt=prompt_p2).text
                f.write(text)
                full_text_buffer = text

        return header + full_text_buffer

    # 并行执行
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
                if log:
                    log.info(f"  - Scene {idx+1} expansion finished.")
            except Exception as e:
                error_msg = f"# {idx+1}. Error\nGeneration failed: {e}"
                scenes_content[idx] = error_msg
                if log:
                    log.error(f"  - Scene {idx+1} failed: {e}")

    # ==========================================
    # Layer 3: Analysis (连贯性检查)
    # ==========================================
    if log:
        log.info("Layer 3: Checking consistency...")

    full_plan_text = "\n\n---\n\n".join(filter(None, scenes_content))

    analysis_tpl = prompts.get("scene_plan", {}).get("analysis", "")

    # 简单的 Prompt 渲染
    prompt_p3 = f"{analysis_tpl}\n\n【完整分场细纲】\n{full_plan_text}"

    analysis_path = f"{base_dir}/02_analysis.md"
    analysis_content = ""

    with open(store._abs(analysis_path), "w", encoding="utf-8") as f:
        f.write("# 分场连贯性评估\n\n")
        if hasattr(provider, "stream_generate"):
            for chunk in provider.stream_generate(
                system=system_prompt, prompt=prompt_p3
            ):
                f.write(chunk)
                f.flush()
                analysis_content += chunk
        else:
            analysis_content = provider.generate(
                system=system_prompt, prompt=prompt_p3
            ).text
            f.write(analysis_content)

    # ==========================================
    # 4. 最终合并
    # ==========================================
    final_full_text = f"# 全书分场表 (Scene Plan)\n\n{full_plan_text}\n\n====================\n\n{analysis_content}"
    final_path = f"{base_dir}/scene_plan.md"
    store.save_text(final_path, final_full_text)

    # 返回给 Manager
    # 注意：Manager 需要根据 candidates_list 里的文本解析出 SceneNode
    candidate_content = f"# 全书分场表\n\n{full_plan_text}"

    return {
        "scene_plan_path": store._abs(final_path),
        "scene_plan_text": final_full_text,
        "candidates_list": [candidate_content],
        "raw_scene_list": scene_list,  # 传递原始 JSON 供 Manager 兜底使用
    }
