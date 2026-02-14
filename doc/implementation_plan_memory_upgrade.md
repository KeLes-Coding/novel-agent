# 记忆机制升级实施计划 (Memory Upgrade Implementation Plan)

## 目标 (Goal)
针对长篇生成中的“遗忘伏笔”问题，实现分层记忆机制（卷摘要 + 近期章节摘要）。
Contextwindow 将包含：`[Archived Summaries]` (所有过往卷摘要) + `[Recent Summaries]` (最近 5 章摘要)。

## User Review Required
> [!IMPORTANT]
> 此变更会修改 `ProjectState` 数据结构 (`state.json`)。
> 旧的 `state.json` 将向前兼容（新增字段默认为空），但为了安全起见，建议在运行前备份 `runs/` 目录。

## Proposed Changes

### Core Logic

#### [MODIFY] [state.py](file:///i:/WorkSpace/novel-agent/src/core/state.py)
- `ProjectState` 类新增字段：
    - `archived_summaries`: `List[str]` (存储已归档的“卷摘要”)
    - `last_archived_scene_id`: `int` (记录最后一个被归档的场景 ID)

#### [MODIFY] [wiki_updater.py](file:///i:/WorkSpace/novel-agent/src/agents/wiki_updater.py)
- 新增方法 `consolidate_summaries(summaries: List[str]) -> str`:
    - 调用 LLM 将多个场景摘要合并为一个“卷摘要”或“阶段摘要”。

#### [MODIFY] [manager.py](file:///i:/WorkSpace/novel-agent/src/core/manager.py)
- 新增私有方法 `_consolidate_memory(self)`:
    - 检查 `completed_scenes` 数量。
    - 如果 `(latest_completed_id - last_archived_scene_id) >= 10` (保留 5 章 buffer，归档 5 章)：
        - 提取待归档的 5 章摘要。
        - 调用 `wiki_updater.consolidate_summaries`。
        - 将结果存入 `state.archived_summaries`。
        - 更新 `state.last_archived_scene_id`。
        - 保存状态。
- 在 `_process_scene_recursive` 完成场景生成和摘要后，调用 `_consolidate_memory`。

#### [MODIFY] [context.py](file:///i:/WorkSpace/novel-agent/src/core/context.py)
- 修改 `ContextBuilder.build` 方法中 `prev_context` 的生成逻辑：
    - **Previous**: 仅读取 `scene_id - 1` 的摘要。
    - **New**: 
        - 读取 `state.archived_summaries` (拼接为“往事回顾”)。
        - 读取 `state.scenes` 中从 `last_archived_scene_id + 1` 到 `scene_id - 1` 的摘要 (拼接为“近期剧情”)。

## Verification Plan

### Automated Tests
#### [NEW] [test_05_memory.py](file:///i:/WorkSpace/novel-agent/tests/test_05_memory.py)
- **Unit Test**: `test_memory_consolidation`
    - 模拟 `ProjectState` 包含 15 个已完成场景。
    - 模拟 `WikiUpdater` 返回固定的合并摘要。
    - 调用 `manager._consolidate_memory()`。
    - **Assert**: `state.archived_summaries` 长度增加，`state.last_archived_scene_id` 更新。
- **Unit Test**: `test_context_assembly`
    - 使用更新后的 State。
    - 调用 `ContextBuilder.build(scene_id=16)`。
    - **Assert**: Payload 中的 `prev_context` 包含 Mock 的归档摘要和最近 5 章的摘要。

### Manual Verification
1.  运行一个小型测试流程，或者手动修改 `state.json` 伪造 10 个场景的数据。
2.  运行 `main.py` (Drafting 阶段)。
3.  观察 Log 输出，确认触发了 `Consolidating memory...`。
4.  检查新的 `state.json` 是否包含 `archived_summaries`。
5.  检查生成的 Prompt Log (或 Context)，确认包含了历史摘要。
