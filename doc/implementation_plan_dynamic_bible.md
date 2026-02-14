# 实施方案：动态设定集更新 (Dynamic World Building)

## 目标 (Goal)
实现“边写边更新”的机制。
在每章（或各阶段）完成后，AI 自动分析剧情增量，提取新的实体（人物、地点、物品）或状态变更，并更新到 `bible_selected.md` 中。

## User Review Required
> [!NOTE]
> 考虑到 Markdown 解析的复杂性与风险，本方案采用 **Append-Only (追加式)** 策略。
> 新的设定将追加到 `bible_selected.md` 的末尾，标记为 `## [New] Dynamic Updates (Chapter X)`。
> 后续可以通过手动或定期的 "Refactor" 任务进行整理。

## Proposed Changes

### Logic

#### [MODIFY] [wiki_updater.py](file:///i:/WorkSpace/novel-agent/src/agents/wiki_updater.py)
- **Optimization Strategy**: 为了解决用户担忧的 Token 消耗和延迟问题，我们将“摘要生成”与“实体提取”合并为一次 LLM 调用 (**Piggyback Extraction**)。
- 重构 `summarize` 方法为 `analyze_scene(text) -> Dict`:
    - Prompt: "请总结本章剧情（100字），**并**提取文中新出现的关键实体（新人物、新地点、新道具）或重大状态变更（存活->死亡）。"
    - Output Format: JSON (preferred) or Structured Text.
      ```json
      {
        "summary": "...",
        "new_facts": ["林风获得了一把断剑", "村长被杀害"]
      }
      ```
- 新增 `patch_bible(old_bible_text, updates) -> str`:
    - 将 `updates` 格式化后追加到 `old_bible_text` 末尾。

#### [MODIFY] [manager.py](file:///i:/WorkSpace/novel-agent/src/core/manager.py)
- 更新 `_process_scene_recursive`:
    - 调用 `wiki_updater.analyze_scene` 替代原有的 `summarize`。
    - `scene_node.summary = result["summary"]`
    - `if result["new_facts"]: wiki_updater.patch_bible(...)`
    - 这样**不会增加额外的 LLM 请求次数**，仅仅是 Output Token 略微增加。

## Verification Plan

### Automated Tests
#### [NEW] [tests/test_06_bible_update.py](file:///i:/WorkSpace/novel-agent/tests/test_06_bible_update.py)
- **Unit Test**: `test_analyze_scene`
    - Mock Provider returning JSON with summary and facts.
    - Verify correct parsing.
- **Unit Test**: `test_integration_flow`
    - Mock Manager processing a scene.
    - Verify `node.summary` is set.
    - Verify `bible_selected.md` is appended with new facts.
