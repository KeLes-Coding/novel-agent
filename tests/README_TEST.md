# 测试框架说明文档

本文档详细介绍了 Novel-Agent 项目的自动化测试框架。该框架基于 Pytest，支持从实际运行数据中提取固定的测试用例（Fixtures），以确保测试的稳定性和真实性。

## 1. 目录结构

所有测试相关文件均位于 `tests/` 目录下：

- **`tests/data/`**: 存放测试数据（如 `fixtures.json`）。
- **`tests/extract_fixtures.py`**: 用于从运行记录（runs）中提取数据并生成 `fixtures.json` 的脚本。
- **`tests/test_*.py`**: 具体的测试文件，按 Pipeline 阶段划分。
- **`tests/conftest.py`**: Pytest 通用配置，负责加载 Fixtures。

## 2. 数据准备

在运行测试前，首先需要准备测试数据。我们已经编写了脚本，从一个成功的历史运行记录（`runs/2026-01-15/16-55-35_b6901ee8`）中提取必要的数据。

如果需要刷新测试数据，请在根目录运行以下命令：

```powershell
python311 tests/extract_fixtures.py
```

该命令会读取并解析指定的 run，并在 `tests/data/fixtures.json` 生成以下内容：
- **Ideation**: 创意生成阶段的输出。
- **Outline**: 大纲生成阶段的输出。
- **Bible**: 世界观与角色设定。
- **Scene Plan**: 分场大纲。
- **Scenes**: 20 个已生成的正文场景内容。

## 3. 运行测试

使用 `python311 -m pytest` 命令运行测试。

### 运行所有测试
```powershell
python311 -m pytest tests
```

### 运行特定阶段的测试
我们使用 Pytest Markers 对测试进行了分类，您可以只运行特定模块：

```powershell
# 只运行创意生成阶段的测试
python311 -m pytest -m ideation

# 只运行大纲阶段的测试
python311 -m pytest -m outline

# 只运行正文写作阶段的测试 (包含20个场景的独立测试)
python311 -m pytest -m drafting
```

### 并行运行测试
对于耗时较长的测试（如大量场景内容的校验），可以使用 `pytest-xdist` 进行并行加速：

```powershell
python311 -m pytest -n auto
```

## 4. 增加新测试

1. 在 `tests/` 目录下新建 `test_*.py` 文件。
2. 在 `conftest.py` 中查看可用的 Fixtures（如 `ideation_data`, `outline_data` 等）。
3. 使用标准 Pytest 语法编写测试函数。
4. 如果需要新的分类，请在 `pytest.ini` 中注册新的 marker，并在测试函数上使用 `@pytest.mark.your_marker`。

---
**注意**：所有测试脚本和数据生成脚本都应确保在项目根目录下执行（即 `i:\WorkSpace\novel-agent`），以保证路径引用正确。
