# Novel-Agent v2.1 重构与演进设计文档

## 1. 核心架构变更 (Architecture Shift)

### 当前问题

* **状态丢失**：运行状态仅靠文件名存在，无法细粒度追踪。
* **紧耦合**：所有步骤写死，缺乏灵活性。
* **可观测性差**：日志缺乏 LLM 调用的完整输入输出，调试困难。
* **机械感重**：生成的文字缺乏文学性，AI 味浓重。

### v2.0 目标架构

采用 **Manager-Worker 模型** + **状态机 (State Machine)** + **服务化 API**。

* **ProjectManager**: 核心控制器，负责调度。
* **Agents (Workers)**: 纯粹的功能函数，无状态。
* **Service Layer**: FastAPI 接口层，对接前端。
* **StateStore (DB)**: 从文件存储转向数据库存储（SQLite/PostgreSQL）。

---

## 2. 详细重构路线图 (TODO List)

### Phase 1: 地基重构与可观测性 (Infrastructure & Observability)

**目标**：解耦逻辑，建立全链路日志追踪，规范化文件管理。

#### 1.1 建立状态管理 (`src/core/state.py`)
* [x] **定义数据结构**：`SceneNode`, `ProjectState`。
* [x] **实现持久化**：暂用 JSON，为 Phase 5 迁入数据库做准备。

#### 1.2 重写 Orchestrator (`src/core/manager.py`)
* [x] 实现 `init_project`, `load_project`, `execute_step`。

#### 1.3 拆解 Drafting 循环 (`src/pipeline/drafting.py`)
* [x] **原子化**：实现 `generate_scene(scene_id)`，支持外部调度。

#### 1.4 高级日志系统 (`src/utils/trace_logger.py`) **(New)**
* [ ] **全链路追踪**：记录完整的 `Prompt (Input)`、`Completion (Output)`、`Token Usage`、`Latency`、`Model Config`。
* [ ] **结构化存储**：每条日志应包含 `agent_id`（身份信息）、`step_name`、`timestamp`。
* [ ] **兼容性**：提供 `migrate_logs.py` 脚本，将旧版纯文本日志转换为新的 JSONL 结构。
* [ ] **本地落盘**：确保所有 API 交互都有本地副本（`.trace` 文件），用于微调数据积累。
* [ ] **版本管理**: 保存修改记录，方便后续回滚。

#### 1.5 目录结构规范化 **(New)**
* [ ] **时间序命名**：Runs 目录修改为 `runs/YYYY-MM-DD_HH-MM-SS_{uuid}/`，便于排序和检索。

---

### Phase 2: 动态上下文与记忆 (The Brain)

**目标**：解决“上下文失忆”，实现 Bible 动态更新。

#### 2.1 上下文构造器 (`src/core/context.py`)
* [ ] **滑动窗口**：组装 Bible + Summary + 上文原文。

#### 2.2 动态 Bible 更新器 (`src/agents/wiki_updater.py`)
* [ ] **Wiki Agent**：每章结束后提取新设定，更新 `characters.yaml`。

---

### Phase 3: 逻辑增强 (Logic & Branching)

**目标**：实现结构化大纲与 A/B 测试。

#### 3.1 结构化大纲解析 (`src/utils/graph_parser.py`)
* [ ] **逻辑一致性检测**：检查大纲节点的前置条件。

#### 3.2 A/B 测试工作流 (`src/core/workflow.py`)
* [ ] **并行生成**：支持多分支剧情生成。
* [ ] **人工选择接口**：CLI 或 API 层面支持用户选择分支。

---

### Phase 4: 风格去噪与润色 (Humanization & Polishing)

**目标**：**去除 AI 味**，引入多角色协作提升文学性。

#### 4.1 Prompt 工程升级 (`config/prompts_v2.yaml`) **(New)**
* [ ] **Show, Don't Tell**：重写 Prompt，强制要求侧面描写而非直接陈述。
* [ ] **Few-Shot Learning**：在 Prompt 中动态插入高质量网文片段作为 `Example`（风格迁移）。

#### 4.2 阅读者 Agent (`src/agents/reader.py`) **(New)**
* [ ] **角色定位**：模拟挑剔的读者。
* [ ] **任务**：阅读 Draft，提出具体的批评（如：“对话太僵硬”、“打斗缺乏画面感”、“爽点铺垫不足”）。不修改正文，只输出 `Critique Report`。

#### 4.3 润色 Agent (`src/agents/polisher.py`) **(New)**
* [ ] **角色定位**：资深网文编辑。
* [ ] **任务**：输入 `Draft` + `Critique Report`，输出 `Final Polish`。
* [ ] **关注点**：去除翻译腔，强化情绪渲染，优化短句节奏。

---

### Phase 5: 服务化与持久化 (Service & Persistence)

**目标**：**前后端分离**，构建 API 接口与数据库，支持 GUI 开发。

#### 5.1 数据库设计 (`src/db/`) **(New)**
* [ ] **Schema 设计**：设计 `Projects`, `Chapters`, `Artifacts`, `Traces` 表结构。
* [ ] **ORM 集成**：引入 SQLAlchemy (Sync/Async)，替代现有的 `state.json` 文件读写。

#### 5.2 后端 API 开发 (`src/server/`) **(New)**
* [ ] **框架选型**：使用 `FastAPI`。
* [ ] **接口定义 (API Contract)**：
    * `POST /api/projects`: 创建新书。
    * `GET /api/projects/{id}/timeline`: 获取大纲流。
    * `POST /api/chapters/{id}/generate`: 触发生成（异步任务）。
    * `POST /api/chapters/{id}/polish`: 触发润色。
* [ ] **文档自动生成**：利用 Swagger/OpenAPI 生成详细接口文档，供前端 LLM 参考。

#### 5.3 异步任务队列 **(New)**
* [ ] **后台任务**：引入简单任务队列（如 Python 内置 queue 或 Celery），防止 LLM 生成阻塞 API 响应。

---

## 3. 建议的文件结构重构 (File Structure)

```text
novel-agent/
├── config/              # 配置文件
├── data/                # Embedding 缓存、Few-shot 样本库
├── docs/                # (New) API 文档、数据库设计文档
├── runs/                # 运行时产物 (按时间戳排序)
├── src/
│   ├── api/             # (New) FastAPI 路由与 Schema
│   ├── core/            # 核心逻辑 (Manager, State, Context)
│   ├── db/              # (New) 数据库模型与迁移脚本
│   ├── agents/          # 具体的 Worker Agents
│   │   ├── writer.py    # 初稿
│   │   ├── reader.py    # (New) 审稿/吐槽
│   │   ├── polisher.py  # (New) 润色/精修
│   │   ├── wiki.py      # 设定更新
│   ├── providers/       # LLM 接口
│   └── utils/           # 工具类 (Logger, Hasher)
├── main.py              # CLI 入口
└── server.py            # (New) Web Server 入口