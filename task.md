# 任务清单

- [x] 数据抽取脚本开发 (`tests/extract_fixtures.py`)
- [x] 成功运行数据抽取，生成 `tests/data/fixtures.json`
- [x] 编写测试用例
    - [x] 创意生成测试 (`tests/test_01_ideation.py`)
    - [x] 大纲结构测试 (`tests/test_02_outline.py`)
    - [x] 世界观/设定集测试 (`tests/test_03_bible.py`)
    - [x] 正文草稿测试 (`tests/test_04_drafting.py`)
- [x] 测试配置 (`pytest.ini`, `tests/conftest.py`)
- [x] 移动测试代码至 `tests/` 目录
- [x] 编写测试说明文档 (`tests/README_TEST.md`)
- [x] 验证测试通过并生成报告 (`tests/test_report.txt`)

## Phase 2: Memory Enhancement (Completed)
- [x] 记忆机制升级设计 (`doc/implementation_plan_memory_upgrade.md`)
- [x] 核心数据结构更新 (`src/core/state.py`)
- [x] 摘要聚合逻辑 (`src/agents/wiki_updater.py`)
- [x] 记忆管理与驱动 (`src/core/manager.py`)
- [x] 上下文构建 (`src/core/context.py`)
- [x] 验证记忆升级 (`tests/test_05_memory.py`)

## Phase 2.5: Dynamic World Building (Completed)
- [x] 实施方案设计 (`doc/implementation_plan_dynamic_bible.md`)
- [x] 实体提取逻辑 (`src/agents/wiki_updater.py`)
    - [x] 实现 `extract_changes` (Optimized to `analyze_scene`)
- [x] 设定集更新逻辑 (`src/agents/wiki_updater.py`)
    - [x] 实现 `patch_bible` (Append-only strategy)
- [x] 集成流程 (`src/core/manager.py`)
    - [x] 在场景完成后触发更新
- [x] 验证更新 (`tests/test_06_bible_update.py`)

## Phase 3: Usability & Documentation (Completed)
- [x] CLI 流程优化 (`main.py`)
    - [x] 实现 `--auto` 模式下的自动循环推进
- [x] 项目文档编写
    - [x] `doc/Project_Overview.md` (架构、技术栈、记忆机制)
    - [x] `doc/API_Manual.md` (CLI 使用说明、后端接口规划)
