# src/pipeline/step_02_outline.py
import os
import json
import re
from typing import Dict, Any, List
from jinja2 import Template
from concurrent.futures import ThreadPoolExecutor, as_completed


def run(step_ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Step 02: 大纲生成 (三层架构)
    Layer 1: Structure (JSON) -> 规划分卷
    Layer 2: Expansion (Parallel) -> 扩写分章
    Layer 3: Analysis (Critique) -> 风险评估
    """
    cfg = step_ctx["cfg"]
    provider = step_ctx["provider"]
    store = step_ctx["store"]
    prompts = step_ctx["prompts"]
    log = step_ctx.get("log")

    # 1. 读取上一步选定的创意
    idea_path = step_ctx.get("idea_path")
    if not idea_path or not os.path.exists(idea_path):
        raise FileNotFoundError(f"Idea path not found: {idea_path}")

    with open(idea_path, "r", encoding="utf-8") as f:
        idea_context = f.read()

    render_ctx = {
        "idea_context": idea_context,
    }

    # 路径准备
    base_dir = "02_outline"
    temp_dir = f"{base_dir}/temp"
    os.makedirs(store._abs(temp_dir), exist_ok=True)

    sys_tpl = prompts.get("global_system", "")
    system_prompt = Template(sys_tpl).render(**render_ctx)

    # ==========================================
    # Layer 1: Structure (流式写入 JSON)
    # ==========================================
    if log:
        log.info("Layer 1: Designing macro structure (Volumes)...")

    struct_tpl = prompts.get("outline", {}).get("structure", "")
    if not struct_tpl:
        struct_tpl = "请规划本书的分卷结构，输出JSON列表。"  # Fallback

    prompt_p1 = Template(struct_tpl).render(**render_ctx)

    json_path = f"{base_dir}/01_structure.json"
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
        volumes_list = json.loads(raw_json)
        # 简单验证格式
        if not isinstance(volumes_list, list):
            raise ValueError("Output is not a list")
    except Exception as e:
        if log:
            log.error(f"Layer 1 JSON Parse Failed: {e}\nRaw: {raw_json}")
        raise RuntimeError("Failed to parse Outline Structure JSON.")

    if log:
        log.info(f"Layer 1 complete. Planned {len(volumes_list)} volumes.")

    # ==========================================
    # Layer 2: Parallel Expansion (流式写入临时文件)
    # ==========================================
    if log:
        log.info("Layer 2: Expanding chapters in parallel...")

    expand_tpl = prompts.get("outline", {}).get("expansion", "")
    # 预分配结果数组
    volumes_content = [None] * len(volumes_list)

    def _expand_volume_task(index: int, vol_meta: dict) -> str:
        """扩写单卷：流式写入临时文件"""
        p_ctx = {
            "volume_id": vol_meta.get("volume_id", index + 1),
            "title": vol_meta.get("title", "未知卷名"),
            "summary": vol_meta.get("summary", ""),
            "idea_context": idea_context,  # 传入全书背景
        }
        prompt_p2 = Template(expand_tpl).render(**p_ctx)

        temp_file_path = f"{temp_dir}/volume_{index+1}.md"
        abs_temp_path = store._abs(temp_file_path)

        header = f"## 第{p_ctx['volume_id']}卷：{p_ctx['title']}\n\n**本卷摘要**：{p_ctx['summary']}\n\n"
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
    with ThreadPoolExecutor(max_workers=len(volumes_list)) as executor:
        future_map = {
            executor.submit(_expand_volume_task, i, vol): i
            for i, vol in enumerate(volumes_list)
        }

        for future in as_completed(future_map):
            idx = future_map[future]
            try:
                res_text = future.result()
                volumes_content[idx] = res_text
                if log:
                    log.info(f"  - Volume {idx+1} expansion finished.")
            except Exception as e:
                error_msg = f"## 第 {idx+1} 卷: 生成失败\nError: {e}"
                volumes_content[idx] = error_msg
                if log:
                    log.error(f"  - Volume {idx+1} failed: {e}")

    # ==========================================
    # Layer 3: Analysis (流式写入报告)
    # ==========================================
    if log:
        log.info("Layer 3: Analyzing Outline (Risk & Suggestions)...")

    # 拼装完整大纲
    full_outline_text = "\n\n---\n\n".join(filter(None, volumes_content))

    analysis_tpl = prompts.get("outline", {}).get("analysis", "")

    # 渲染 Prompt
    p_ctx_l3 = {"full_outline": full_outline_text}
    prompt_p3 = Template(analysis_tpl).render(**p_ctx_l3)

    analysis_path = f"{base_dir}/02_analysis.md"
    analysis_content = ""

    with open(store._abs(analysis_path), "w", encoding="utf-8") as f:
        f.write("# 大纲深度评估报告\n\n")
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

    if log:
        log.info("Layer 3 Analysis complete.")

    # ==========================================
    # 4. 最终合并
    # ==========================================
    # 合并 正文 + 分析报告
    final_full_text = f"# 全书大纲\n\n{full_outline_text}\n\n====================\n\n{analysis_content}"
    final_path = f"{base_dir}/outline.md"
    store.save_text(final_path, final_full_text)

    # 返回列表结构，虽然通常只有一个“v1”，但为了兼容 HITL 接口
    # 这里的 "v1" 包含了【正文】部分（volumes_content 拼接），用于用户精修
    # 注意：这里我们只把“正文”作为 Candidate，不包含分析报告，方便用户精修大纲本身
    candidate_content = f"# 全书大纲\n\n{full_outline_text}"

    return {
        "outline_path": store._abs(final_path),
        "outline_text": final_full_text,
        "candidates_list": [candidate_content],  # 返回单元素列表
    }
