from typing import Dict, Any


def build_bible_prompt(outline_md: str) -> str:
    return (
        "你是中文男频修仙中短篇的“设定与角色圣经”生成器。\n"
        "必须遵守：单女主（不许多女主暧昧/后宫倾向）。\n"
        "请基于下方【大纲】输出一个 YAML 格式的角色圣经（只输出 YAML，不要夹带解释性文字）。\n\n"
        "YAML 结构必须包含以下字段（字段名固定）：\n"
        "meta:\n"
        "  genre: 男频\n"
        "  tags: [修仙, 单女主]\n"
        "world:\n"
        "  setting_one_liner: 世界一句话\n"
        "  power_system:\n"
        "    realms: [境界列表]\n"
        "    rules: [修行规则/代价/限制]\n"
        "    resources: [灵石/丹药/法宝等资源体系]\n"
        "  factions:\n"
        "    - name: 势力名\n"
        "      goal: 目标\n"
        "      method: 手段\n"
        "      relation_to_mc: 与主角关系\n"
        "characters:\n"
        "  mc:\n"
        "    name: 主角名\n"
        "    age: 年龄\n"
        "    appearance: 外貌关键词\n"
        "    personality: [性格关键词]\n"
        "    core_wound: 核心创伤/执念\n"
        "    desire: 表层欲望\n"
        "    need: 深层需求\n"
        "    bottom_line: 底线\n"
        "    strengths: [优势]\n"
        "    flaws: [缺点]\n"
        "    signature_skill: 标志性能力/功法\n"
        "    growth_arc: 成长弧线（起点->转折->终点）\n"
        "  fl:\n"
        "    name: 女主名\n"
        "    role: 身份定位\n"
        "    motivation: 动机\n"
        "    personality: [性格关键词]\n"
        "    competence: 关键能力\n"
        "    vulnerability: 软肋\n"
        "    relationship_arc:\n"
        "      - beat: 节点名\n"
        "        what_happens: 发生什么\n"
        "        emotional_change: 情感变化\n"
        "    non_negotiable: 单女主约束声明（明确：只此一人）\n"
        "  antagonist:\n"
        "    name: 主要反派名\n"
        "    goal: 目标\n"
        "    fear: 恐惧/失控点\n"
        "    mask: 表面人设\n"
        "    true_face: 真面目\n"
        "    methods: [手段]\n"
        "plot_support:\n"
        "  key_promises: [读者承诺点/爽点承诺]\n"
        "  foreshadows:\n"
        "    - seed: 伏笔\n"
        "      payoff: 回收\n"
        "  consistency_rules: [写作时必须保持一致的规则]\n\n"
        "要求：\n"
        "1) 角色名不要太现代口语，符合修仙世界审美。\n"
        "2) 女主必须有独立动机与能力，不得工具人化。\n"
        "3) 境界体系给出 6-10 级即可，且与剧情推进匹配。\n"
        "4) 输出必须是合法 YAML。\n\n"
        "【大纲】\n"
        f"{outline_md}\n"
    )


def run(step_ctx: Dict[str, Any]) -> Dict[str, Any]:
    cfg = step_ctx["cfg"]
    prompts = step_ctx["prompts"]
    provider = step_ctx["provider"]
    store = step_ctx["store"]

    outline_path = step_ctx["outline_path"]
    with open(outline_path, "r", encoding="utf-8") as f:
        outline_md = f.read()

    system = prompts.get("global_system", "").strip()
    bible_prompt = build_bible_prompt(outline_md)

    bible_yaml = provider.generate(
        system=system, prompt=bible_prompt, meta={"cfg": cfg}
    )
    out_path = store.save_text("03_bible/characters.yaml", bible_yaml)

    return {
        "bible_path": out_path,
        "bible_prompt": bible_prompt,
        "bible_text": bible_yaml,
    }
