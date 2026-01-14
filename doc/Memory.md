# Novel-Agent Phase 2：基于 LLM Agent 的动态记忆系统设计与实现

**摘要 (Abstract)**
本文档详细阐述了 Novel-Agent v2.1 中“动态记忆系统 (Dynamic Memory System)”的架构设计。针对长篇小说生成中普遍存在的“上下文遗忘 (Context Amnesia)”与“逻辑不一致 (Inconsistency)”问题，本方案借鉴 **Claude Code** 的“分层作用域 (Scoped Context)”与 **Southbridge** 的“自动压缩 (Auto-compaction)”思想，构建了一套包含**语义记忆 (Semantic)**、**情景记忆 (Episodic)** 与**工作记忆 (Working)** 的三层混合架构。通过上下文预算管理算法与递归摘要机制，实现了在有限 Token 窗口下的长程叙事一致性。

---

## 1. 引言 (Introduction)

在基于大语言模型 (LLM) 的长文本生成任务中，随着 Token 数量的线性增长，模型对早期信息的召回能力呈指数级衰退。传统的“滑动窗口 (Sliding Window)”机制仅能保证局部连贯性，无法维持宏观叙事逻辑（如伏笔回收、人物性格成长）。

为了解决这一问题，Novel-Agent 引入了**主动式记忆管理 (Active Memory Management)**。系统的核心目标是将非结构化的长文本流，实时转化为结构化的知识图谱与摘要链，从而使 Agent 具备“长期记忆”能力。

## 2. 理论框架 (Theoretical Framework)

本系统的设计深受 **Claude Code** 工程实践与 **Southbridge** 架构分析的启发，将其核心概念映射至文学创作领域：

### 2.1 记忆的文件化与作用域 (File-based & Scoped Memory)

* **Claude Code 原理**：通过 `.claude/rules/*.md` 定义项目级规则，并支持按目录深度或文件类型动态加载特定上下文。
* **Novel-Agent 映射**：
* **全局记忆**：`NOVEL.md`（核心设定）。
* **领域记忆**：`.novel/rules/combat.md`（战斗规则）、`romance.md`（情感规则），仅在相关情节触发时加载。



### 2.2 自动压缩与预算管理 (Compaction & Budgeting)

* **Southbridge 原理**：当对话历史超出阈值时，触发 LLM 生成 Summary 替代原始消息（Prioritized Truncation）。
* **Novel-Agent 映射**：
* **原文驱逐**：随着剧情推进，旧章节的原文被移除上下文。
* **摘要留存**：原文被压缩为 `Scene Summary`，进而递归合并为 `Chapter Summary`。



---

## 3. 系统架构 (System Architecture)

Novel-Agent 的记忆系统由三层异构存储组成：

| 记忆类型 | 对应组件 | 存储形式 | 更新频率 | 功能描述 |
| --- | --- | --- | --- | --- |
| **L1: 语义记忆**<br>

<br>(Semantic Memory) | **Bible (设定集)** | `characters.yaml`<br>

<br>`world.yaml` | 低频<br>

<br>(按章更新) | 存储世界观、人物状态、物品属性。相当于“知识库”。 |
| **L2: 情景记忆**<br>

<br>(Episodic Memory) | **Timeline (摘要链)** | `state.json`<br>

<br>`summaries.md` | 中频<br>

<br>(按场景更新) | 存储已发生的剧情流水账。用于维持因果逻辑。 |
| **L3: 工作记忆**<br>

<br>(Working Memory) | **Context Window** | Runtime Prompt | 高频<br>

<br>(实时) | 当前正在处理的上下文，包含上文接龙、当前任务目标。 |

---

## 4. 核心算法与实现 (Implementation Methodology)

### 4.1 动态上下文组装器 (Dynamic Context Assembler)

该组件负责在有限的 Context Window (Budget) 内，依据优先级策略筛选并装填信息。

**核心逻辑 (Pseudo-code):**

```python
class ContextAssembler:
    def __init__(self, max_tokens=32000):
        self.budget = max_tokens
        self.priority_queue = [
            ("P0", "System Prompt", required=True),
            ("P0", "Current Scene Goal", required=True),
            ("P1", "Immediate Context (Last 800 chars)", required=True),
            ("P2", "Active Rules (e.g., style.md)", required=False),
            ("P3", "Character Cards (Active Only)", required=False),
            ("P4", "Episodic Summaries (Recent)", required=False),
            ("P5", "Episodic Summaries (Arc/Global)", required=False)
        ]

    def build(self, current_scene_id, tags):
        current_context = []
        used_tokens = 0

        # 1. 加载规则 (Scoped Rules)
        active_rules = self.rule_loader.load(tags) # e.g. 加载 'combat.md' if tag='战斗'

        # 2. 优先级装填循环
        for priority, item, required in self.priority_queue:
            content = self.fetch_content(item)
            cost = self.estimator.count(content)

            if used_tokens + cost < self.budget:
                current_context.append(content)
                used_tokens += cost
            else:
                if required:
                    raise ContextOverflowError("Critical memory overflow")
                else:
                    # 触发降级策略 (Graceful Degradation)
                    # 例如：将 Bible 完整版替换为“仅名字列表”
                    trimmed_content = self.compress(content)
                    if used_tokens + len(trimmed_content) < self.budget:
                         current_context.append(trimmed_content)
                    
        return self.format_prompt(current_context)

```

### 4.2 记忆固化与更新 (Memory Consolidation)

在 Drafting 阶段完成后，由 `WikiUpdater` Agent 执行记忆的“写回”操作。

**核心逻辑 (Pseudo-code):**

```python
class MemoryConsolidator:
    def on_scene_complete(self, scene_text, scene_id):
        """
        触发时机：单场景写作完成
        """
        
        # Step 1: 生成原子摘要 (Atomic Summarization)
        # 将 3000 字正文压缩为 100 字摘要
        summary = self.llm.generate(
            prompt=f"Summarize the key events in:\n{scene_text}",
            max_tokens=150
        )
        StateStore.update_scene_summary(scene_id, summary)

        # Step 2: 递归压缩 (Recursive Compaction)
        # 检查是否满足合并条件（例如：每10个场景）
        if scene_id % 10 == 0:
            recent_summaries = StateStore.get_summaries(start=scene_id-9, end=scene_id)
            chapter_summary = self.llm.generate(
                prompt=f"Merge these 10 summaries into one arc summary:\n{recent_summaries}"
            )
            StateStore.save_arc_summary(chapter_summary)

        # Step 3: 语义漂移检测 (Semantic Drift Detection)
        # 识别正文中与 Bible 不一致或新增的事实
        diff = self.llm.analyze(
            prompt="Extract new facts (items, injuries, relationships) from text based on current Bible."
        )
        if diff.has_changes:
            BibleManager.patch(diff) # 更新 characters.yaml

```

---

## 5. 演进路线 (Evolution Roadmap)

基于上述架构，后续的更新计划如下：

* **Phase 2.2 (Current)**: 实现基础的 `WikiUpdater`，完成单场景摘要与全量 Bible 的简单更新。
* **Phase 2.3 (Optimization)**: 引入 **Vector Database (RAG)**。当小说超过 50 万字时，P4/P5 级别的摘要将无法完全放入 Context。此时需将旧摘要向量化，根据当前剧情的 Embedding 检索相关的历史记忆（例如：“主角遇到了曾经在第 3 章救过的路人”）。
* **Phase 3.0 (Logic Graph)**: 将 YAML 格式的 Bible 升级为 **Graph Database (Neo4j/NetworkX)**，显式建模人物关系图谱，防止逻辑冲突（如：死人复活、辈分混乱）。

## 6. 结论 (Conclusion)

本方案通过工程化的手段，将大语言模型的短期注意力转化为系统的长期记忆。它不仅解决了长篇生成的连贯性问题，更通过“文件化”与“配置化”的设计，赋予了 Novel-Agent 极高的可维护性与可观测性，为构建真正具备“作家级”掌控力的 AI Agent 奠定了坚实基础。