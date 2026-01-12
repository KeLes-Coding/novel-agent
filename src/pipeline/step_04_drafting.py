import json
import os
import datetime
from typing import Dict, Any, List, Optional

from utils.logger import log_event, StepTimer


def _safe_int(v, default: int) -> int:
    try:
        return int(v)
    except Exception:
        return default


def build_scene_plan_prompt(outline_md: str, bible_yaml: str, max_scenes: int) -> str:
    return (
        "你是中文男频修仙中短篇的“场景拆分器”。必须遵守：单女主。\n"
        "请根据【大纲】与【角色圣经】，拆分为可写作的场景列表。\n"
        "只输出严格 JSON（不要代码块，不要解释）。\n\n"
        "JSON 结构：\n"
        "{\n"
        '  "scenes": [\n'
        "    {\n"
        '      "id": 1,\n'
        '      "title": "场景标题",\n'
        '      "goal": "本场目标",\n'
        '      "conflict": "冲突点",\n'
        '      "turn": "转折/信息增量",\n'
        '      "cliffhanger": "结尾钩子",\n'
        '      "characters": ["出场人物名1", "名2"],\n'
        '      "location": "地点",\n'
        '      "time": "时间/阶段"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        f"要求：\n"
        f"1) 场景数量 <= {max_scenes}\n"
        "2) 节奏快：每个场景必须有冲突推进与信息增量\n"
        "3) 单女主：女主的关系推进节点要出现在多个关键场景，但不得喧宾夺主\n"
        "4) 尽量让场景标题具有网文吸引力\n\n"
        "【大纲】\n"
        f"{outline_md}\n\n"
        "【角色圣经】\n"
        f"{bible_yaml}\n"
    )


def build_scene_draft_prompt(
    scene: Dict[str, Any], outline_md: str, bible_yaml: str, scene_words: int
) -> str:
    return (
        "你是中文男频修仙中短篇写作助手。必须原创，必须单女主。\n"
        "请根据【场景卡】写一个完整场景，输出 Markdown。\n\n"
        f"硬性要求：\n"
        f"1) 字数约 {scene_words} 字（允许±25%）\n"
        "2) 强画面感 + 对白推进冲突\n"
        "3) 必须落实本场 goal/conflict/turn/cliffhanger\n"
        "4) 不得出现多女主暧昧倾向\n"
        "5) 保持人物性格一致（参考角色圣经）\n\n"
        "输出格式：\n"
        f"# Scene {scene.get('id')}: {scene.get('title','')}\n"
        "- 目标：...\n"
        "- 冲突：...\n"
        "- 转折：...\n"
        "- 钩子：...\n"
        "\n"
        "正文正文正文...\n\n"
        "【场景卡】\n"
        f"{json.dumps(scene, ensure_ascii=False)}\n\n"
        "【大纲】\n"
        f"{outline_md}\n\n"
        "【角色圣经】\n"
        f"{bible_yaml}\n"
    )


def _parse_scene_plan(text: str) -> Dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        l = text.find("{")
        r = text.rfind("}")
        if l != -1 and r != -1 and r > l:
            return json.loads(text[l : r + 1])
        raise


def generate_scene_plan(step_ctx: Dict[str, Any]) -> Dict[str, Any]:
    """生成分场大纲（包含 JSON Mode 支持与详细日志）"""
    cfg = step_ctx["cfg"]
    prompts = step_ctx["prompts"]
    provider = step_ctx["provider"]
    store = step_ctx["store"]

    # 获取上下文
    outline_path = step_ctx["outline_path"]
    bible_path = step_ctx["bible_path"]
    run_id = step_ctx.get("run_id", "-")
    jsonl = step_ctx.get("jsonl")
    log = step_ctx.get("log")

    with open(outline_path, "r", encoding="utf-8") as f:
        outline_md = f.read()
    with open(bible_path, "r", encoding="utf-8") as f:
        bible_yaml = f.read()

    system = prompts.get("global_system", "").strip()
    drafting_cfg = (cfg.get("pipeline", {}) or {}).get("drafting", {}) or {}
    max_scenes = int(drafting_cfg.get("max_scenes", 12))

    plan_prompt = build_scene_plan_prompt(outline_md, bible_yaml, max_scenes)

    if log:
        log.info(f"Generating scene plan max_scenes={max_scenes}")

    t_plan = StepTimer()

    # --- 核心逻辑升级：支持 generate_json ---
    if hasattr(provider, "generate_json"):
        plan_text, plan_obj = provider.generate_json(
            system=system, prompt=plan_prompt, meta={"cfg": cfg}
        )
    else:
        plan_text = provider.generate(
            system=system, prompt=plan_prompt, meta={"cfg": cfg}
        ).text
        plan_obj = _parse_scene_plan(plan_text)

    plan_ms = t_plan.ms()

    # 保存原始输出（Debug用）
    store.save_text("04_drafting/scene_plan_raw.txt", plan_text)

    # 记录详细日志
    log_event(
        jsonl,
        {
            "ts": datetime.datetime.now().isoformat(),
            "run_id": run_id,
            "step": "drafting",
            "event": "MODEL_CALL",
            "subtype": "scene_plan",
            "duration_ms": plan_ms,
            "prompt_preview": plan_prompt[:240],
        },
    )

    scenes = plan_obj.get("scenes", [])
    if not isinstance(scenes, list):
        scenes = []

    # 截断与回填
    scenes = scenes[:max_scenes]
    plan_obj["scenes"] = scenes

    path = store.save_json("04_drafting/scene_plan.json", plan_obj)
    return {"scene_plan_path": path, "scenes": scenes}


def draft_single_scene(
    scene_data: Dict[str, Any],
    cfg: Dict[str, Any],
    prompts: Dict[str, Any],
    provider: Any,
    outline_path: str,
    bible_path: str,
    store: Any,  # 新增：为了流式写文件
    rel_path: str,  # 新增：目标文件路径
    log: Any = None,  # 新增
    jsonl: Any = None,  # 新增
    run_id: str = "-",  # 新增
) -> str:
    """原子函数：写单个场景（支持流式写入与断点续传）"""

    # 1. 检查是否已完成（断点续传逻辑）
    # 注意：Manager 层可能已经做过检查，但原子函数内部再做一次更安全，
    # 或者Manager调用前做检查，这里只负责写。为了保持函数纯粹，建议主要逻辑在Manager，
    # 但由于你希望复用流式写入逻辑，我们将写入操作放在这里。

    with open(outline_path, "r", encoding="utf-8") as f:
        outline_md = f.read()
    with open(bible_path, "r", encoding="utf-8") as f:
        bible_yaml = f.read()

    system = prompts.get("global_system", "").strip()
    drafting_cfg = (cfg.get("pipeline", {}) or {}).get("drafting", {}) or {}
    scene_words = int(drafting_cfg.get("scene_words", 900))

    scene_prompt = build_scene_draft_prompt(
        scene_data, outline_md, bible_yaml, scene_words
    )

    t_scene = StepTimer()

    # 2. 准备原子写入
    # 使用 .part 文件，避免写了一半程序崩溃导致文件损坏
    part_rel_path = rel_path + ".part"
    part_abs_path, f_handle = store.open_text(part_rel_path, mode="w")

    collected = []
    try:
        # --- 核心逻辑升级：支持流式生成 ---
        if hasattr(provider, "stream_generate"):
            # 流式调用
            for chunk in provider.stream_generate(
                system=system,
                prompt=scene_prompt,
                meta={"cfg": cfg, "scene": scene_data},
            ):
                f_handle.write(chunk)
                f_handle.flush()  # 实时刷盘
                collected.append(chunk)
        else:
            # 非流式回退
            txt = provider.generate(
                system=system,
                prompt=scene_prompt,
                meta={"cfg": cfg, "scene": scene_data},
            ).text
            f_handle.write(txt)
            collected.append(txt)
    except Exception as e:
        if log:
            log.error(f"Error drafting scene {scene_data.get('id')}: {e}")
        f_handle.close()
        raise e
    finally:
        f_handle.close()

    scene_ms = t_scene.ms()
    full_text = "".join(collected)

    # 3. 原子替换 (rename .part -> .md)
    final_abs_path = store._abs(rel_path)
    if os.path.exists(final_abs_path):
        os.remove(final_abs_path)  # Windows下replace不能覆盖已有文件，需先删
    os.replace(part_abs_path, final_abs_path)

    # 4. 记录日志
    log_event(
        jsonl,
        {
            "ts": datetime.datetime.now().isoformat(),
            "run_id": run_id,
            "step": "drafting",
            "event": "MODEL_CALL",
            "subtype": "scene_draft",
            "scene_index": scene_data.get("id"),
            "duration_ms": scene_ms,
            "prompt_preview": scene_prompt[:240],
            "artifact_path": final_abs_path,
        },
    )

    return full_text
