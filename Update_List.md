# Novel-Agent v2.0 重构与演进设计文档

## 1. 核心架构变更 (Architecture Shift)

### 当前问题

* **状态丢失**：运行状态仅靠文件名存在，无法细粒度追踪（如“第3章第2段”）。
* **紧耦合**：`orchestrator.py` 把所有步骤写死在一个流程里。
* **无法回滚**：一旦出错，往往需要删除文件重跑。

### v2.0 目标架构

采用 **Manager-Worker 模型** + **状态机 (State Machine)**。

* **ProjectManager**: 核心控制器，负责加载项目状态、调度任务。
* **StateStore**: 唯一的真理来源（Source of Truth），记录大纲结构、当前进度、分支选择。
* **Agents (Workers)**: 纯粹的功能函数，无状态，只负责 `Input -> LLM -> Output`。

---

## 2. 详细重构路线图 (TODO List)

### Phase 1: 地基重构 (Infrastructure & State)

**目标**：解耦 Orchestrator，引入状态管理，实现“原子化”生成。

#### 1.1 建立状态管理 (`src/core/state.py`)

不再依赖文件系统扫描来判断进度。

* [ ] **定义数据结构**：
```python
class SceneNode:
    id: str
    title: str
    status: "pending" | "drafting" | "reviewing" | "done"
    versions: List[str]  # 对应 A/B 测试的不同版本文件路径
    selected_version: str # 最终选定的版本

class ProjectState:
    run_id: str
    step: str  # 当前大阶段
    bible_path: str
    outline_graph: Dict[str, SceneNode] # 图结构大纲
    history_summary: List[str] # 每章的摘要列表 (滑动窗口用)

```


* [ ] **实现持久化**：支持 `save()` 和 `load()` 到 `project_state.json`。

#### 1.2 重写 Orchestrator (`src/core/manager.py`)

废弃当前的 `src/orchestrator.py`，改为交互式控制器。

* [ ] 实现 `init_project(config)`。
* [ ] 实现 `load_project(run_id)`。
* [ ] 实现 `execute_step(step_name)`：不再是线性的，而是根据 State 决定跑什么。

#### 1.3 拆解 Drafting 循环 (`src/pipeline/drafting.py`)

当前的 `step_04_drafting.py` 内部有一个巨大的 `for` 循环。

* [ ] **重构为单次调用**：`generate_scene(scene_id, context)`。
* [ ] 移除内部循环，改为由 `ProjectManager` 外部调度。这样才能在两章之间插入“动态 Bible 更新”或“人工干预”。

---

### Phase 2: 动态上下文与记忆 (The Brain)

**目标**：解决“上下文失忆”，实现 Bible 动态更新。

#### 2.1 上下文构造器 (`src/core/context.py`)

* [ ] **实现滑动窗口**：
```python
def build_prompt_context(scene_id, state):
    # 1. 获取 Bible (静态 + 动态部分)
    # 2. 获取前 5 章的 Summary
    # 3. 获取上一章最后 800 字 (Raw Text)
    # 4. 组装成 Prompt

```



#### 2.2 动态 Bible 更新器 (`src/agents/wiki_updater.py`)

* [ ] **新增 Agent**：在每章生成后触发。
* [ ] **Prompt 设计**：
> "阅读以下正文，提取新出现的：1. 人物关系变化 2. 新获得的物品 3. 新的世界规则。并输出为 JSON diff。"


* [ ] **合并逻辑**：将提取出的 JSON diff 合并回 `characters.yaml`。

---

### Phase 3: 逻辑增强 (Logic & Branching)

**目标**：实现结构化大纲与 A/B 测试。

#### 3.1 结构化大纲解析 (`src/utils/graph_parser.py`)

* [ ] **Markdown 转 Graph**：将 `step_02_outline.py` 产生的 Markdown 解析为 `SceneNode` 对象。
* [ ] **逻辑断层检测**：
> 遍历图节点，检查 Pre-condition 和 Post-condition（如：上一章没拿到剑，这一章却在用剑）。



#### 3.2 A/B 测试工作流 (`src/core/workflow.py`)

* [ ] **并行生成接口**：修改 Provider，支持 `n=3` 或并发调用 3 次。
* [ ] **交互式选择 CLI**：
```text
[System] Scene 5 生成了 3 个分支：
A. 主角正面硬刚 (Token消耗: 500)
B. 主角侧面迂回 (Token消耗: 480)
C. 主角通过嘴遁 (Token消耗: 520)
[User Input] 选择 > B

```


* [ ] **版本记录**：将落选的版本存入 `archive/` 文件夹备查，选中的存入 `drafts/`。

---

### Phase 4: 质量控制 (Quality & Style)

**目标**：引入 Embedding 进行文风质检。

#### 4.1 扩展 Provider (`src/providers/base.py`)

* [ ] **新增 `get_embedding(text)` 方法**：支持 OpenAI/Gemini/HuggingFace 的 Embedding API。

#### 4.2 文风检测器 (`src/pipeline/step_05_qc.py`)

升级现有的 QC 模块。

* [ ] **基准录入**：读取 Config 中的 `style_reference_file`（如《凡人修仙传》某章节），计算基准向量。
* [ ] **距离计算**：`cosine_similarity(current_chapter_vec, reference_vec)`。
* [ ] **报警机制**：如果相似度 < 0.75，标记为 `STYLE_WARNING`，触发 Editor Agent 重写。

---

## 3. 建议的文件结构重构 (File Structure)

为了实现上述模块化，建议将当前扁平的结构调整为：

```text
novel-agent/
├── config/              # 配置文件
├── data/                # (新) 存放 Embedding 缓存、Prompt 模板
├── runs/                # 运行时产物
│   └── run_xyz/
│       ├── state.json   # (新) 核心状态文件
│       ├── artifacts/   # 各种生成物
│       └── .temp/       # A/B 测试的临时文件
├── src/
│   ├── core/            # (新) 核心逻辑
│   │   ├── manager.py   # 项目管理与调度
│   │   ├── state.py     # 状态类定义
│   │   └── context.py   # 上下文组装
│   ├── agents/          # (新) 各种具体能力的 Agent
│   │   ├── writer.py    # 原 step_04
│   │   ├── editor.py    # 润色与重写
│   │   ├── wiki.py      # 动态 Bible 更新
│   │   └── critic.py    # 风格检查与逻辑检查
│   ├── providers/       # LLM 接口 (保持不变)
│   └── utils/           # 工具类
└── main.py              # (新) 统一入口 CLI

```