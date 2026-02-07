# 测试与 CI 指南（当前仓库实情版）

本文档以仓库当前实现为准，目标是让你能：
- 本地一键复现 CI
- 了解哪些测试是纯单测/模拟，哪些需要外部凭据
- 明确工程的测试分层与覆盖范围

---

## CI 流程（GitHub Actions）

CI 配置文件：`.github/workflows/ci.yml`，当前分 3 个 job。

### 1) `lint`（Ubuntu / Python 3.11）

安装：

- `ruff`、`mypy`
- `pip install -e .`

执行：

```bash
ruff check src/
mypy src/ --ignore-missing-imports
```

说明：
- `mypy` 当前作为“尽力而为”检查（见 `pyproject.toml`：`ignore_errors = true`），用于尽早暴露明显问题；不以“全量类型正确”为门槛。

### 2) `test`（多 OS / 多版本）

矩阵：
- OS：Ubuntu / Windows / macOS
- Python：3.11 / 3.12

安装：

```bash
pip install -e ".[dev]"
```

执行：

```bash
pytest tests/ -v --cov=src/openakita --cov-report=xml
```

覆盖率：
- 产物：`coverage.xml`
- 上传：仅 Ubuntu + Python 3.11 的矩阵分支会上传到 Codecov

### 3) `build`（Ubuntu / Python 3.11）

依赖：`lint`、`test`

执行：

```bash
python -m build
```

并上传 `dist/` 作为构建产物。

---

## 本地复现 CI（推荐命令）

### 安装开发依赖

```bash
python -m pip install -U pip
pip install -e ".[dev]"
```

### Lint / Type Check

```bash
ruff check src/
mypy src/ --ignore-missing-imports
```

### 运行测试（与 CI 一致）

```bash
pytest tests/ -v
```

带覆盖率：

```bash
pytest tests/ -v --cov=src/openakita --cov-report=xml
```

---

## 测试分层与目录结构（真实结构）

当前 pytest 用例主要分布在：

```text
tests/
├── llm/
│   ├── unit/          # 纯单测：能力推断、配置解析、消息格式、类型结构等
│   ├── integration/   # Provider/Registry/Routing 的集成层测试（目前多使用 mock，不依赖真实 API）
│   ├── fault/         # 故障/容错：超时、failover、降级策略
│   ├── regression/    # 回归/兼容：Brain、媒体处理、memory 注入等关键路径
│   └── e2e/           # 端到端：对话、工具、多模态、调度等（当前以可复现/离线为优先）
├── test_memory_system.py
├── test_new_features.py
├── test_orchestration.py
├── test_scheduler_detailed.py
└── test_telegram_simple.py    # 需要真实凭据：默认跳过
```

---

## 需要外部凭据/环境的测试

目标原则：
- 默认 CI 只跑 **离线可复现** 的测试（mock、纯逻辑、无网络依赖）
- 真实外部依赖（IM、真实 LLM key）尽量 **默认 skip**，避免把 CI 稳定性绑在外部服务上

### Telegram 集成测试（默认跳过）

文件：`tests/test_telegram_simple.py`

行为：如果没有 `TELEGRAM_BOT_TOKEN`，模块会在导入时直接 skip。

需要的环境变量：
- `TELEGRAM_BOT_TOKEN`（必需）
- `TELEGRAM_CHAT_ID`（部分用例需要）

本地启用示例：

```bash
export TELEGRAM_BOT_TOKEN="..."
export TELEGRAM_CHAT_ID="..."
pytest tests/test_telegram_simple.py -v
```

### `--api-keys` 选项（当前是预留能力）

`tests/llm/conftest.py` 里预留了 `--api-keys` 参数与 `api_keys` marker（不加参数时自动 skip 被标记用例）。

现状：仓库中该 marker 的覆盖还不完整（很多用例仍使用 mock、或用其它方式进行 skip）。

建议标准化口径（后续可落地到文档+代码）：
- 默认 CI：不启用 `--api-keys`
- 手动/夜间任务：显式 `pytest --api-keys ...` 跑真实 key 的用例

---

## 功能/烟囱测试脚本（不在 CI，适合本地冒烟）

脚本：`scripts/run_tests.py`

用途：做工具可用性与关键行为的“冒烟验证”（Shell/File/QA/Prompt&Memory）。

运行：

```bash
python scripts/run_tests.py
```

说明：其中 QA 会初始化 `Agent()`，在某些环境下可能触发真实 LLM 路径；因此它更适合本地/部署后验证，而非默认 CI。

---

## 排障建议（测试失败时）

常用命令：

```bash
# 单文件
pytest tests/test_memory_system.py -v

# 单用例
pytest tests/test_memory_system.py::TestMemoryManager::test_34_get_injection_context_includes_memory_md -v

# 打印 stdout / 日志
pytest tests/test_memory_system.py -v -s
```

# Testing Guide

OpenAkita includes a comprehensive testing framework with 300+ test cases.

## Test Categories

| Category | Count | Description |
|----------|-------|-------------|
| QA/Basic | 30 | Math, programming knowledge |
| QA/Reasoning | 35 | Logic, code comprehension |
| QA/Multi-turn | 35 | Context memory, instruction following |
| Tools/Shell | 40 | Command execution |
| Tools/File | 30 | File operations |
| Tools/API | 30 | HTTP requests |
| Search/Web | 40 | Web search |
| Search/Code | 30 | Code search |
| Search/Docs | 30 | Documentation search |
| **Total** | **300** | |

## Running Tests

### All Tests

```bash
# Run all tests
pytest tests/ -v

# With coverage report
pytest tests/ --cov=src/openakita --cov-report=html
```

### Specific Categories

```bash
# Only QA tests
pytest tests/test_qa.py -v

# Only tool tests
pytest tests/test_tools.py -v

# Only search tests
pytest tests/test_search.py -v
```

### Self-Check Mode

```bash
# Quick check (core functionality)
openakita selfcheck --quick

# Full check (all 300 tests)
openakita selfcheck --full

# With auto-fix on failure
openakita selfcheck --fix
```

## Test Structure

### Test File Organization

```
tests/
├── test_qa.py            # Q&A tests
├── test_tools.py         # Tool tests
├── test_search.py        # Search tests
├── test_integration.py   # Integration tests
└── fixtures/
    ├── sample_files/     # Test files
    └── mock_responses/   # Mock API responses
```

### Test Case Format

```python
# tests/test_qa.py
import pytest
from openakita.testing.runner import TestRunner

class TestBasicQA:
    """Basic question-answering tests."""
    
    @pytest.mark.asyncio
    async def test_math_addition(self, agent):
        """Agent can perform basic math."""
        response = await agent.process("What is 2 + 2?")
        assert "4" in response
    
    @pytest.mark.asyncio
    async def test_programming_knowledge(self, agent):
        """Agent knows programming concepts."""
        response = await agent.process("What is a Python decorator?")
        assert "function" in response.lower()
```

## Built-in Test Runner

### TestRunner Class

```python
from openakita.testing.runner import TestRunner, TestCase

runner = TestRunner()

# Add test cases
runner.add_case(TestCase(
    id="qa_math_001",
    category="qa/basic",
    input="What is 15 * 7?",
    expected_contains=["105"],
    timeout=30
))

# Run tests
results = await runner.run_all()
print(f"Passed: {results.passed}/{results.total}")
```

### Test Case Definition

```python
@dataclass
class TestCase:
    id: str                    # Unique identifier
    category: str              # Test category
    input: str                 # User input
    expected_contains: list    # Expected substrings in output
    expected_not_contains: list = None  # Should not appear
    timeout: int = 60          # Timeout in seconds
    requires_tools: list = None  # Required tools
    setup: Callable = None     # Setup function
    teardown: Callable = None  # Teardown function
```

## Judge System

The judge evaluates test results:

```python
from openakita.testing.judge import Judge

judge = Judge()

verdict = judge.evaluate(
    expected="The answer is 42",
    actual="Based on my calculation, the answer is 42.",
    criteria=["exact_match", "contains", "semantic"]
)

print(verdict.passed)  # True
print(verdict.score)   # 0.95
print(verdict.reason)  # "Contains expected value"
```

### Evaluation Criteria

| Criteria | Description |
|----------|-------------|
| `exact_match` | Output exactly matches expected |
| `contains` | Output contains expected substring |
| `not_contains` | Output does not contain string |
| `semantic` | Semantically similar (uses LLM) |
| `regex` | Matches regular expression |
| `json_valid` | Output is valid JSON |
| `code_runs` | Code in output executes successfully |

## Auto-Fix System

When tests fail, OpenAkita can attempt automatic fixes:

```python
from openakita.testing.fixer import Fixer

fixer = Fixer()

# Analyze failure
analysis = await fixer.analyze(
    test_case=failed_test,
    actual_output=output,
    error_message=error
)

# Attempt fix
if analysis.fixable:
    fix = await fixer.generate_fix(analysis)
    await fixer.apply_fix(fix)
    
    # Re-run test
    result = await runner.run_case(failed_test)
```

### Fix Categories

| Category | Auto-Fix Support |
|----------|------------------|
| Missing import | ✅ Yes |
| Syntax error | ✅ Yes |
| Type mismatch | ✅ Yes |
| Logic error | ⚠️ Sometimes |
| Design flaw | ❌ No |

## Writing Tests

### Best Practices

1. **One assertion per test** when possible
2. **Use descriptive names** that explain what's tested
3. **Include edge cases** (empty input, large data, etc.)
4. **Mock external services** to ensure reproducibility
5. **Set appropriate timeouts** for async operations

### Example: Tool Test

```python
@pytest.mark.asyncio
async def test_file_write_read(self, agent, tmp_path):
    """Agent can write and read files."""
    test_file = tmp_path / "test.txt"
    content = "Hello, World!"
    
    # Write file
    response = await agent.process(
        f"Write '{content}' to {test_file}"
    )
    assert test_file.exists()
    
    # Read file
    response = await agent.process(
        f"Read the contents of {test_file}"
    )
    assert content in response
```

### Example: Multi-turn Test

```python
@pytest.mark.asyncio
async def test_context_memory(self, agent):
    """Agent remembers context across turns."""
    # First turn
    await agent.process("My name is Alice")
    
    # Second turn - should remember
    response = await agent.process("What is my name?")
    assert "Alice" in response
```

## CI/CD Integration

### GitHub Actions

```yaml
# .github/workflows/test.yml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: pip install -e ".[dev]"
      
      - name: Run tests
        run: pytest tests/ -v --cov=src/openakita
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      
      - name: Upload coverage
        uses: codecov/codecov-action@v3
```

## Debugging Tests

### Enable Debug Logging

```bash
LOG_LEVEL=DEBUG pytest tests/test_qa.py -v -s
```

### Run Single Test

```bash
pytest tests/test_qa.py::TestBasicQA::test_math_addition -v
```

### Interactive Debugging

```python
@pytest.mark.asyncio
async def test_with_debug(self, agent):
    import pdb; pdb.set_trace()
    response = await agent.process("Test input")
```

## Performance Testing

```python
import time

@pytest.mark.asyncio
async def test_response_time(self, agent):
    """Response should be under 5 seconds."""
    start = time.time()
    await agent.process("Simple question")
    elapsed = time.time() - start
    assert elapsed < 5.0
```

## Test Data

### Sample Files

Test files are in `tests/fixtures/sample_files/`:

```
sample_files/
├── text/
│   ├── simple.txt
│   └── unicode.txt
├── code/
│   ├── python_sample.py
│   └── javascript_sample.js
└── data/
    ├── sample.json
    └── sample.csv
```

### Mock Responses

Mock API responses in `tests/fixtures/mock_responses/`:

```python
# Loaded automatically in tests
MOCK_CLAUDE_RESPONSE = {
    "content": [{"type": "text", "text": "Mock response"}],
    "stop_reason": "end_turn"
}
```
