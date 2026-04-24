# 测试指南

本文档以仓库当前实现为准，覆盖测试分层、运行方式、CI 集成和开发流程。

---

## 测试分层架构

```
L5 质量评估  │ 统计通过率, 手动/每周     │ tests/quality/
L4 E2E 测试  │ 录制回放/真实 LLM, <15min │ tests/e2e/
L3 集成测试  │ Mock LLM, <3min          │ tests/integration/
L2 组件测试  │ Mock LLM, <2min          │ tests/component/
L1 单元测试  │ 纯逻辑, <30s             │ tests/unit/
```

| 层 | 触发条件 | 耗时 | LLM 费用 |
|----|---------|------|---------|
| L1 单元测试 | 每次 commit/PR | <30s | 0 |
| L2 组件测试 | 每次 commit/PR | <2min | 0 |
| L3 集成测试 | 每次 commit/PR | <3min | 0 |
| L4 E2E (回放) | 每次 PR / 每日 | <5min | 0 (回放模式) |
| L4 E2E (录制) | 手动 / 每周 | <15min | ~$0.5 |
| L5 质量评估 | 手动 / 每周 | <30min | ~$2 |

---

## 目录结构

```
tests/
├── conftest.py                    # 全局 fixtures (mock_llm, test_session, etc.)
├── fixtures/
│   ├── mock_llm.py                # MockLLMClient + LLMRecorder + ReplayClient
│   ├── factories.py               # 测试数据工厂
│   └── recordings/                # LLM 响应录制文件
│       └── .gitkeep
├── unit/                          # L1: 纯逻辑, <30s
│   ├── test_budget.py             # Token 预算估算
│   ├── test_session.py            # Session 状态机
│   ├── test_capabilities.py       # 模型能力匹配
│   ├── test_judge.py              # Judge 判定规则
│   ├── test_catalog.py            # 工具目录
│   └── test_memory_types.py       # Memory 数据模型
├── component/                     # L2: Mock LLM, <2min
│   ├── test_context_manager.py    # 上下文压缩
│   ├── test_reasoning_engine.py   # ReAct / AgentState
│   ├── test_tool_executor.py      # 工具执行 / 结果截断
│   ├── test_prompt_compiler.py    # Prompt 编译 / 系统提示词构建
│   ├── test_llm_client.py         # LLM 类型 / MockLLMClient
│   └── test_memory_manager.py     # 记忆存取
├── integration/                   # L3: Mock LLM, <3min
│   ├── test_api_chat.py           # /api/chat SSE
│   ├── test_api_config.py         # /api/config
│   ├── test_gateway.py            # 消息类型 / Gateway
│   ├── test_session_lifecycle.py  # SessionManager 生命周期
│   ├── test_im_adapters.py        # IM 适配器协议合规 (6 个适配器)
│   └── test_setup_wizard.py       # 安装向导 (目录/配置生成)
├── e2e/                           # L4: 录制回放/真实 LLM, <15min
│   ├── test_multiturn.py          # 多轮对话上下文
│   ├── test_memory_e2e.py         # 记忆跨会话持久化
│   ├── test_tool_orchestration.py # 多步工具编排
│   ├── test_interrupt_e2e.py      # 中断/取消/跳过全链路
│   ├── test_cli.py                # CLI 命令
│   └── test_setup_e2e.py          # 安装配置全流程
├── quality/                       # L5: 统计通过率, 手动/每周
│   ├── test_tool_selection.py     # 工具选择准确率
│   └── test_response_quality.py   # 回答质量评分 (LLM-as-Judge)
└── legacy/                        # 迁移自原 tests/ 根目录
    ├── test_cancel.py
    ├── test_memory_system.py
    ├── test_memory_interaction.py
    ├── test_module_deps.py
    ├── test_new_features.py
    ├── test_orchestration.py
    ├── test_refactoring.py
    ├── test_scheduler_detailed.py
    └── test_telegram_simple.py
```

---

## 核心基础设施

### MockLLMClient (tests/fixtures/mock_llm.py)

可编程的 LLM 模拟，支持：
- `preset_response(content, tool_calls)` — 预设单次返回
- `preset_sequence(responses)` — 预设多轮 ReAct 序列
- `call_log` / `total_calls` — 调用记录
- `MockBrain` — 包装 MockLLMClient 匹配 Brain 接口

### LLMRecorder / ReplayLLMClient

- `LLMRecorder(real_client, dir)` — 录制真实 LLM 交互到 JSON
- `ReplayLLMClient(dir)` — 从 JSON 回放，基于 messages hash 匹配

### Factories (tests/fixtures/factories.py)

- `create_test_session(chat_id, channel, messages)`
- `create_channel_message(channel, text, images, voices)`
- `create_mock_agent()` — 轻量 Mock Agent
- `create_mock_gateway()` — Mock MessageGateway
- `create_tool_definition(name, category, schema)`
- `create_endpoint_config(name, provider, model)`
- `build_conversation(turns)` — 从 `(role, content)` 元组构建对话

### conftest.py 提供的 Fixtures

| Fixture | 描述 |
|---------|------|
| `mock_llm_client` | 带默认回复的 MockLLMClient |
| `mock_brain` | 基于 mock_llm_client 的 MockBrain |
| `test_session` | 空的 Session |
| `tmp_workspace` | 临时工作空间 (data/memory/logs/identity) |
| `test_settings` | 指向临时目录的 Settings |
| `mock_response_factory` | 创建 MockResponse 的工厂函数 |

---

## 本地运行

### 安装开发依赖

```bash
pip install -e ".[dev]"
```

### 运行全部测试

```bash
pytest tests/ -v
```

### 分层运行

```bash
# L1 单元测试 (最快)
pytest tests/unit/ -v

# L2 组件测试
pytest tests/component/ -v

# L3 集成测试
pytest tests/integration/ -v

# L4 E2E 测试
pytest tests/e2e/ -v

# L5 质量评估
pytest tests/quality/ -v

# Legacy 测试
pytest tests/legacy/ -v -k "not TestVectorStore"
```

### 带覆盖率

```bash
pytest tests/ -v --cov=src/openakita --cov-report=html
```

---

## CI 流程 (GitHub Actions)

配置文件：`.github/workflows/ci.yml`

### 分层 CI Jobs

| Job | 触发 | 依赖 |
|-----|------|------|
| `unit_tests` | 每次 Push/PR | - |
| `component_tests` | 每次 Push/PR | unit_tests |
| `integration_tests` | 每次 Push/PR | component_tests |
| `e2e_tests` | main 分支 / 手动 | integration_tests |
| `quality_tests` | main 分支 / 手动 | e2e_tests |
| `python_test` | 每次 Push/PR | - (跨平台兼容性) |

### E2E 录制/回放

```bash
# 回放模式 (CI 默认)
LLM_TEST_MODE=replay pytest tests/e2e/ -v

# 录制模式 (本地手动)
LLM_TEST_MODE=record pytest tests/e2e/ -v
```

---

## 需要外部凭据的测试

默认 CI 只跑离线可复现的测试。

### Telegram 集成测试 (默认跳过)

```bash
export TELEGRAM_BOT_TOKEN="..."
export TELEGRAM_CHAT_ID="..."
pytest tests/legacy/test_telegram_simple.py -v
```

---

## 新功能开发流程

### 测试检查清单 (PR 模板)

每个新功能 PR 必须回答：

- [ ] 新增了哪些测试文件/测试用例?
- [ ] 纯逻辑部分是否有 L1 单元测试? (函数输入输出、边界值)
- [ ] 组件交互是否有 L2 测试? (Mock LLM、验证调用链)
- [ ] 如果涉及 API/通道，是否有 L3 集成测试?
- [ ] 是否录制了 LLM 响应用于 E2E 回放?
- [ ] 修复 bug 时是否先写了复现该 bug 的测试?

### 变更类型 → 最低测试要求

| 变更类型 | 最低测试要求 |
|---------|-----------|
| 新增纯逻辑函数 | L1 单元测试，覆盖正常路径 + 至少 1 个边界 |
| 新增组件/模块 | L2 组件测试，Mock 依赖，验证关键交互 |
| 新增 API 端点 | L3 集成测试 (httpx AsyncClient)，验证请求/响应格式 |
| 新增 IM 适配器 | L3 集成测试，Mock webhook/消息，验证消息转换 |
| 新增 LLM 交互行为 | L4 E2E，录制一次真实交互，加入回放测试 |
| 修复 bug | 先写复现测试 (红)，再修复代码使其变绿 |

---

## 排障建议

```bash
# 单文件
pytest tests/unit/test_budget.py -v

# 单用例
pytest tests/unit/test_budget.py::TestEstimateTokens::test_pure_english -v

# 打印 stdout / 日志
pytest tests/component/test_context_manager.py -v -s

# 调试模式
LOG_LEVEL=DEBUG pytest tests/integration/test_api_chat.py -v -s
```

