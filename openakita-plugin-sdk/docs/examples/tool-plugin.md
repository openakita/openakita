# 工具插件示例 / Tool Plugin Example

最简单的插件类型 — 注册一个或多个 LLM 可调用的工具。

The simplest plugin type — registers one or more tools that the LLM can call.

**权限级别 / Permission Level:** Basic（无需用户确认 / no user approval needed）

---

## 目录结构 / Directory Structure

```
hello-tool/
  plugin.json
  plugin.py
  README.md
```

## plugin.json

```json
{
  "id": "hello-tool",
  "name": "Hello Tool",
  "version": "1.0.0",
  "description": "一个简单的问候工具 / A simple greeting tool",
  "author": "OpenAkita Team",
  "license": "MIT",
  "type": "python",
  "entry": "plugin.py",
  "permissions": ["tools.register"],
  "provides": { "tools": ["hello_world"] },
  "category": "tool",
  "tags": ["demo", "greeting"]
}
```

## plugin.py（手动注册方式 / Manual Registration）

```python
from openakita_plugin_sdk import PluginBase, PluginAPI
from openakita_plugin_sdk.tools import tool_definition

TOOLS = [
    tool_definition(
        name="hello_world",
        description="向某人问好 / Say hello to someone",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "要问候的人名 / Name to greet"},
            },
            "required": ["name"],
        },
    ),
]


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        self.api = api

        async def handler(tool_name: str, arguments: dict) -> str:
            if tool_name == "hello_world":
                name = arguments.get("name", "World")
                return f"Hello, {name}!"
            return f"Unknown tool: {tool_name}"

        api.register_tools(TOOLS, handler)
        api.log("Hello tool plugin loaded")

    def on_unload(self) -> None:
        pass
```

## plugin.py（装饰器方式 / Decorator Style）

```python
from openakita_plugin_sdk import PluginBase, PluginAPI
from openakita_plugin_sdk.decorators import tool, auto_register

@tool(name="hello_world", description="向某人问好 / Say hello to someone")
async def hello_world(tool_name: str, arguments: dict) -> str:
    name = arguments.get("name", "World")
    return f"Hello, {name}!"


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        auto_register(api)
        api.log("Hello tool plugin loaded")

    def on_unload(self) -> None:
        pass
```

## 测试 / Test

```python
from openakita_plugin_sdk.testing import assert_plugin_loads
from plugin import Plugin

def test_plugin_loads():
    plugin = Plugin()
    api = assert_plugin_loads(plugin)
    assert "hello_world" in api.registered_tools
```

---

## 关键要点 / Key Points

- 工具定义使用 OpenAI 函数调用格式 / Tool definitions use OpenAI function calling format
- `handler` 接收所有工具调用，通过 `tool_name` 分发 / `handler` receives all calls, dispatch by `tool_name`
- 只需 `tools.register` 权限，安装即可用 / Only needs `tools.register`, works immediately
- 使用 `tool_definition()` 辅助函数简化定义 / Use `tool_definition()` helper to simplify definitions

