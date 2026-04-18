# 测试指南 / Testing Guide

SDK 提供测试工具，让你无需运行完整的 OpenAkita 实例即可验证插件的正确性。

The SDK provides testing utilities to verify your plugin without a running OpenAkita instance.

```python
from openakita_plugin_sdk.testing import MockPluginAPI, assert_plugin_loads
```

---

## MockPluginAPI

`MockPluginAPI` 是 `PluginAPI` 的内存模拟实现，记录所有注册调用，供断言使用。

`MockPluginAPI` is an in-memory mock of `PluginAPI` that records all registration calls for assertions.

### 基本用法 / Basic Usage

```python
from openakita_plugin_sdk.testing import MockPluginAPI
from plugin import Plugin

def test_my_plugin():
    api = MockPluginAPI()
    plugin = Plugin()
    plugin.on_load(api)

    # 验证工具注册 / Verify tools registered
    assert "my_tool" in api.registered_tools

    # 验证钩子注册 / Verify hooks registered
    assert "on_init" in api.registered_hooks

    # 验证日志输出 / Verify log output
    assert any("loaded" in msg for _, msg in api.logs)
```

### 可检查属性 / Inspectable Attributes

| 属性 / Attribute | 类型 / Type | 说明 / Description |
|-----------------|------------|-------------------|
| `registered_tools` | `list[str]` | 已注册的工具名列表 / Registered tool names |
| `registered_hooks` | `dict[str, list[Callable]]` | 钩子名 → 回调函数列表 / Hook name → callback list |
| `registered_channels` | `list[str]` | 已注册的通道类型 / Registered channel types |
| `registered_memory_backends` | `dict[str, Any]` | 插件 ID → 后端实例 / Plugin ID → backend instance |
| `registered_search_backends` | `dict[str, Any]` | 后端名 → 实例 / Backend name → instance |
| `registered_llm_providers` | `dict[str, type]` | API 类型 → 提供商类 / API type → provider class |
| `registered_llm_registries` | `dict[str, Any]` | slug → 注册表实例 / slug → registry instance |
| `registered_retrieval_sources` | `list` | 检索源实例列表 / Retrieval source instances |
| `registered_routes` | `list` | FastAPI Router 实例列表 / Router instances |
| `config` | `dict` | 插件配置（get/set）/ Plugin config (get/set) |
| `logs` | `list[tuple[str, str]]` | `(level, message)` 日志记录 / Log entries |
| `sent_messages` | `list[dict]` | `send_message()` 调用记录 / Message send records |

### 配置测试 / Testing Config

```python
def test_config():
    api = MockPluginAPI()
    api.config = {"api_key": "test-key", "max_results": 5}

    plugin = Plugin()
    plugin.on_load(api)

    # 验证插件正确读取了配置 / Verify plugin reads config correctly
    assert api.config.get("api_key") == "test-key"
```

### 消息发送测试 / Testing Message Sending

```python
def test_send_message():
    api = MockPluginAPI()
    plugin = Plugin()
    plugin.on_load(api)

    # 触发某个会发送消息的操作
    # Trigger some action that sends a message
    api.send_message("telegram", "123", "Hello!")

    assert len(api.sent_messages) == 1
    assert api.sent_messages[0]["text"] == "Hello!"
    assert api.sent_messages[0]["channel"] == "telegram"
```

---

## assert_plugin_loads

快速冒烟测试：实例化插件、调用 `on_load()`、验证没有错误日志。

Quick smoke test: instantiate the plugin, call `on_load()`, assert no error logs.

```python
from openakita_plugin_sdk.testing import assert_plugin_loads
from plugin import Plugin

def test_plugin_loads():
    plugin = Plugin()
    api = assert_plugin_loads(plugin)
    # api 是 MockPluginAPI 实例，可以继续检查
    # api is a MockPluginAPI instance, continue checking
    assert "my_tool" in api.registered_tools
```

> **注意 / Note:** `assert_plugin_loads()` 接收插件**实例**（不是类）。
>
> `assert_plugin_loads()` takes a plugin **instance** (not a class).

---

## 异步处理函数测试 / Testing Async Handlers

使用 `pytest-asyncio` 测试异步工具处理函数：

Use `pytest-asyncio` to test async tool handlers:

```python
import pytest

@pytest.mark.asyncio
async def test_tool_handler():
    api = MockPluginAPI()
    plugin = Plugin()
    plugin.on_load(api)

    # 直接调用处理函数 / Call handler directly
    result = await plugin.handle_tool("search_notes", {"query": "test"})
    assert "test" in result
```

---

## 卸载测试 / Testing Unload

确保 `on_unload()` 不会抛出异常：

Ensure `on_unload()` doesn't raise:

```python
def test_unload():
    api = MockPluginAPI()
    plugin = Plugin()
    plugin.on_load(api)
    plugin.on_unload()  # 不应抛出 / should not raise
```

---

## 装饰器测试 / Testing with Decorators

使用装饰器的插件需要在测试间清理全局注册表：

Plugins using decorators need to clear global registries between tests:

```python
from openakita_plugin_sdk.decorators import clear_registries

def setup_function():
    clear_registries()

def test_my_decorator_plugin():
    clear_registries()
    # 重新导入或重新定义带装饰器的函数
    # Re-import or re-define decorated functions
    from plugin import Plugin
    plugin = Plugin()
    api = assert_plugin_loads(plugin)
    assert "my_tool" in api.registered_tools
```

---

## 集成测试 / Integration Tests

需要真实外部服务（数据库、API）的测试，使用环境变量和 skip 标记：

For tests requiring real external services, use environment variables and skip markers:

```python
import os
import pytest

@pytest.mark.skipif(
    not os.getenv("QDRANT_URL"),
    reason="QDRANT_URL not set"
)
@pytest.mark.asyncio
async def test_qdrant_backend():
    api = MockPluginAPI()
    api.config = {"qdrant_url": os.environ["QDRANT_URL"]}
    plugin = QdrantPlugin()
    plugin.on_load(api)

    backend = list(api.registered_memory_backends.values())[0]
    mid = await backend.store({"content": "hello", "type": "test"})
    results = await backend.search("hello", limit=5)
    assert len(results) > 0
```

---

## 测试清单 / Test Checklist

每个插件至少应通过以下测试：

Every plugin should at least pass these tests:

- [ ] `on_load()` 完成，无错误日志 / `on_load()` completes without error logs
- [ ] 预期的工具/钩子/通道已注册 / Expected tools/hooks/channels are registered
- [ ] `on_unload()` 不抛出异常 / `on_unload()` doesn't raise
- [ ] 工具处理函数返回正确结果 / Tool handlers return correct results
- [ ] 配置读写正常 / Config read/write works

---

## UI 插件测试 / Testing UI Plugins

全栈 UI 插件的后端逻辑可以用相同的 `MockPluginAPI` 测试。前端 Bridge 通信需要手动或端到端测试。

Backend logic of full-stack UI plugins can use the same `MockPluginAPI`. Frontend Bridge communication requires manual or end-to-end testing.

### 后端路由测试 / Backend Route Testing

```python
from fastapi.testclient import TestClient
from fastapi import FastAPI

def test_plugin_routes():
    app = FastAPI()
    api = MockPluginAPI()
    api._host = {"api_app": app}

    plugin = MyUIPlugin()
    plugin.on_load(api)

    for router in api.registered_routes:
        app.include_router(router, prefix="/api/plugins/my-plugin")

    client = TestClient(app)
    resp = client.get("/api/plugins/my-plugin/tasks")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
```

### 前端测试建议 / Frontend Testing Tips

- 在浏览器中直接打开 `ui/dist/index.html` 测试渲染（SDK 自动 fallback 到直连模式）
- 使用浏览器开发者工具的 Console 调用 `window.__bridge.pluginApi("GET", "/tasks")` 验证 API 通信
- `_inIframe` 为 `false` 时，SDK 自动跳过 Bridge 使用直连 fetch，适合独立测试

---

## 相关文档 / Related

- [getting-started.md](getting-started.md) — 第一个测试示例 / First test example
- [api-reference.md](api-reference.md) — `MockPluginAPI` 模拟的所有方法 / All methods mocked by MockPluginAPI
- [plugin-ui.md](plugin-ui.md) — UI 插件开发指南 / UI plugin development guide
