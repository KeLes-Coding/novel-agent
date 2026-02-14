# 当前记忆机制与代码结构解构报告

## 1. 代码结构总览 (Code Structure Overview)

目前 Novel-Agent 的核心逻辑主要分布在 `src/core`, `src/pipeline`, `src/agents` 和 `src/orchestrator.py`/`src/manager.py` 中。

### 核心组件
*   **`src/core/manager.py`**: 项目的实际“大脑”。也就是 `ProjectManager` 类。它维护了一个有限状态机 (`FSM`)，管理从 Ideation 到 Drafting 的全流程。它比 `orchestrator.py` 更先进，支持分支剧情 (`_handle_branches`) 和人机交互 (HITL)。
*   **`src/core/context.py`**: **关键组件**。`ContextBuilder` 类负责组装 LLM 的 Prompt Context。它决定了 LLM “此刻能看到什么”。
*   **`src/core/state.py`**: 定义了 `ProjectState` 和 `SceneNode` 数据结构。`SceneNode` 是记忆挂载的实体，包含了 `summary` (摘要) 和 `meta` (元数据)。
*   **`src/agents/wiki_updater.py`**: 负责记忆的维护（生成摘要、更新设定）。**目前处于极其基础的阶段。**

### 流程控制
*   **`orchestrator.py`**: 较旧的线性执行入口。
*   **`manager.py`**: 推荐的执行入口，支持递归的分支剧情生成。

---

## 2. 记忆机制现状解构 (Memory Mechanism Analysis)

对照 `doc/Memory.md` 中的设计目标，目前的实现情况如下：

### L1: 语义记忆 (Semantic Memory) - **部分实现 (静态)**
*   **定义**: 世界观、人物卡 (Bible)。
*   **实现**: 
    *   存储在 `bible_selected.md` 或 `bible.md`。
    *   `ContextBuilder` 会每次都完整加载这个文件进入 Context (`bible` 字段)。
*   **缺陷**: **完全静态**。尽管 `WikiUpdater` 中定义了 `update_bible` 接口，但目前仅仅是返回原文本：
    ```python
    def update_bible(self, old_bible: str, new_chapter: str) -> str:
        # ...
        return old_bible  # 占位
    ```
    这意味着随着剧情发展，如果产生了新的设定（e.g. 主角获得新武器、受了伤），**Bible 不会自动更新**，后续章节可能无法感知这些变化。

### L2: 情景记忆 (Episodic Memory) - **基础实现 (滑动窗口)**
*   **定义**: 剧情的流水账摘要。
*   **实现**: 
    *   `ProjectManager` 在通过 `step_04_drafting` 生成完一章正文后，会立即调用 `WikiUpdater.summarize` 生成 100 字摘要，并存入 `SceneNode.summary`。
    *   `ContextBuilder` 在构建下一章 Context 时，会读取 **上一章** 的摘要：
        ```python
        prev_node = next((s for s in self.state.scenes if s.id == scene_id - 1), None)
        if prev_node and prev_node.summary:
            prev_context = f"【上一章摘要】：{prev_node.summary}"
        ```
*   **缺陷**: **视野极窄**。目前只读取了 `N-1` 章的摘要。如果第 10 章需要引用第 2 章的伏笔，LLM 将完全“看不见”，因为第 2 章的摘要没有被包含进 Context。缺乏文档规划中的“递归摘要”或“动态检索”机制。

### L3: 工作记忆 (Working Memory) - **已实现**
*   **定义**: 实时 Prompt 上下文。
*   **实现**: `ContextBuilder.build()` 负责组装。
    *   包含：Idea (全量), Outline (全量), Bible (全量), Prev Context (仅上一章), Current Scene Info。
*   **现状**: 对于中短篇小说尚可，但随着篇幅增长，`Outline` 和 `Bible` 可能会非常长，直接全量塞入可能会挤占 Context Window，导致遗忘。

---

## 3. 优化建议 (Optimization Recommendations)

回答您的问题：**基础功能和记忆模块非常需要继续优化。**

### 优先级 P0：激活“动态设定更新”
*   **目标**: 让 Bible 变“活”。
*   **行动**: 实现 `WikiUpdater.update_bible` 的具体逻辑。
    1.  在每章生成后，不仅生成摘要，还调用 LLM 识别“设定变更”（Entity Extraction）。
    2.  将变更 Patch 到 `bible_selected.md` 中（或者更新结构化的 YAML/JSON 数据，如果已迁移）。

### 优先级 P1：扩展情景记忆视野
*   **目标**: 解决“遗忘伏笔”问题。
*   **行动**: 改造 `ContextBuilder` 的 `prev_context` 逻辑。
    *   **低配版**: 读取最近 N 章（如最近 3-5 章）的摘要，而不仅仅是上一章。
    *   **中配版**: 按照 `Memory.md` 规划，实现 `Chapter Summary` (卷摘要) -> `Scene Summary` (章摘要) 的层级结构。Prompt 中包含“所有过往卷摘要” + “最近 5 章摘要”。

### 优先级 P2：结构化存储
*   目前主要依赖 Markdown 文本。为了更精准的更新，建议将 Characters/World 真正转化为 structured format (YAML/JSON) 进行维护，在 Prompt 组装时再渲染回 Markdown。

### 代码重构建议
*   `orchestrator.py` 与 `manager.py` 功能重叠。建议废弃 `orchestrator.py`，统一使用 `ProjectManager` 作为单一事实来源 (Single Source of Truth)。
