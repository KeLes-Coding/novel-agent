from typing import Dict, Any


def build_outline_prompt(idea_text: str, target_words: int) -> str:
    return (
        "请基于下面的选题内容，输出一份适配男频修仙+单女主的中短篇大纲（Markdown）。\n"
        "要求：\n"
        "1) 5幕结构（开端/升级/转折/高潮/收束）\n"
        "2) 每幕3-5个关键事件（用列表）\n"
        "3) 明确主线目标、反派阻力、资源体系（功法/境界/代价）\n"
        "4) 单女主：给出她的动机、与主角关系推进节点（不得多女主暧昧）\n"
        "5) 伏笔与回收清单（至少6条）\n"
        f"字数目标：≈{target_words}\n\n"
        "【选题内容】\n"
        f"{idea_text}\n"
    )


def run(step_ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    step_ctx:
      - cfg, prompts, provider, store, log, jsonl, run_id
      - artifacts from previous steps can be read from store paths
    """
    cfg = step_ctx["cfg"]
    provider = step_ctx["provider"]
    store = step_ctx["store"]
    prompts = step_ctx["prompts"]

    # 读取 ideation 输出（你现在是 ideas.txt）
    idea_path = step_ctx["idea_path"]
    with open(idea_path, "r", encoding="utf-8") as f:
        idea_text = f.read()

    target_words = cfg["content"]["length"]["target_words"]
    system = prompts.get("global_system", "").strip()

    outline_prompt = build_outline_prompt(idea_text, target_words)
    outline_md = provider.generate(
        system=system, prompt=outline_prompt, meta={"cfg": cfg}
    )

    out_path = store.save_text("02_outline/outline.md", outline_md)

    return {"outline_path": out_path}
