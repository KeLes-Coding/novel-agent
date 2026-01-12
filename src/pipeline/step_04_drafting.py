import json
import datetime
from typing import Dict, Any, List

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
    # 注意：这里要求输出 Markdown，方便拼接
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
    """
    尽可能从模型输出中解析 JSON。
    - 若输出含杂质文本：尝试截取首个 { 到最后一个 }。
    """
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        l = text.find("{")
        r = text.rfind("}")
        if l != -1 and r != -1 and r > l:
            return json.loads(text[l : r + 1])
        raise


def run(step_ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Inputs (step_ctx):
      - cfg, prompts, provider, store
      - outline_path, bible_path
      - run_id, jsonl (optional), ctx(optional), log(optional)
    Outputs:
      - scene_plan_path, scenes_dir, draft_path, scene_paths(list)
    """
    cfg = step_ctx["cfg"]
    prompts = step_ctx["prompts"]
    provider = step_ctx["provider"]
    store = step_ctx["store"]

    outline_path = step_ctx["outline_path"]
    bible_path = step_ctx["bible_path"]

    run_id = step_ctx.get("run_id", "-")
    jsonl = step_ctx.get("jsonl", None)
    ctx = step_ctx.get("ctx", None)
    log = step_ctx.get("log", None)

    # cfg 参数（带默认值，避免你没写配置时报错）
    drafting_cfg = (cfg.get("pipeline", {}) or {}).get("drafting", {}) or {}
    scene_words = _safe_int(drafting_cfg.get("scene_words", 900), 900)
    max_scenes = _safe_int(drafting_cfg.get("max_scenes", 12), 12)

    with open(outline_path, "r", encoding="utf-8") as f:
        outline_md = f.read()
    with open(bible_path, "r", encoding="utf-8") as f:
        bible_yaml = f.read()

    system = prompts.get("global_system", "").strip()

    # ---------- 1) 生成场景计划 ----------
    plan_prompt = build_scene_plan_prompt(outline_md, bible_yaml, max_scenes)

    if log:
        log.info(f"Generating scene plan max_scenes={max_scenes}")

    t_plan = StepTimer()
    plan_text = provider.generate(system=system, prompt=plan_prompt, meta={"cfg": cfg})
    plan_ms = t_plan.ms()

    # 事件：MODEL_CALL（scene_plan）
    preview_chars = 240
    if ctx is not None:
        preview_chars = getattr(ctx, "prompt_preview_chars", 240)

    log_event(
        jsonl,
        {
            "ts": datetime.datetime.now().isoformat(),
            "run_id": run_id,
            "step": "drafting",
            "event": "MODEL_CALL",
            "subtype": "scene_plan",
            "duration_ms": plan_ms,
            "prompt_preview": plan_prompt[:preview_chars],
        },
    )

    plan_obj = _parse_scene_plan(plan_text)

    scenes: List[Dict[str, Any]] = plan_obj.get("scenes", [])
    if not isinstance(scenes, list) or len(scenes) == 0:
        raise ValueError("scene_plan JSON missing non-empty `scenes` list")

    # 截断到 max_scenes
    scenes = scenes[:max_scenes]
    plan_obj["scenes"] = scenes

    scene_plan_path = store.save_json("04_drafting/scene_plan.json", plan_obj)

    # ---------- 2) 逐场景写作 ----------
    scene_paths: List[str] = []
    md_chunks: List[str] = []

    for i, scene in enumerate(scenes, start=1):
        sid = scene.get("id", i)
        # 文件名按序号固定，避免 id 不连续
        filename = f"04_drafting/scenes/scene_{i:03d}.md"

        scene_prompt = build_scene_draft_prompt(
            scene, outline_md, bible_yaml, scene_words
        )

        if log:
            log.info(f"Drafting scene {i}/{len(scenes)} -> {filename}")

        t_scene = StepTimer()
        scene_text = provider.generate(
            system=system, prompt=scene_prompt, meta={"cfg": cfg, "scene": scene}
        )
        scene_ms = t_scene.ms()

        path = store.save_text(filename, scene_text)
        scene_paths.append(path)
        md_chunks.append(scene_text.strip() + "\n")

        # 事件：MODEL_CALL（scene_draft）
        log_event(
            jsonl,
            {
                "ts": datetime.datetime.now().isoformat(),
                "run_id": run_id,
                "step": "drafting",
                "event": "MODEL_CALL",
                "subtype": "scene_draft",
                "scene_index": i,
                "duration_ms": scene_ms,
                "prompt_preview": scene_prompt[:preview_chars],
                "artifact_path": path,
            },
        )

    # ---------- 3) 拼接成整稿 ----------
    draft_text = "\n\n---\n\n".join(md_chunks).strip() + "\n"
    draft_path = store.save_text("04_drafting/draft_v1.md", draft_text)

    return {
        "scene_plan_path": scene_plan_path,
        "scene_paths": scene_paths,
        "draft_path": draft_path,
    }
