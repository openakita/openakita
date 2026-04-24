# 钩子插件示例 / Hook Plugin Example

通过生命周期钩子观察和响应系统事件：消息收发、会话管理、工具调用等。

Observe and respond to system events via lifecycle hooks: message I/O, session management, tool calls, and more.

**权限级别 / Permission Level:** Basic 或 Advanced（取决于使用的钩子 / depends on hooks used）

---

## 目录结构 / Directory Structure

```
message-logger/
  plugin.json
  plugin.py
  README.md
```

## plugin.json

```json
{
  "id": "message-logger",
  "name": "Message Logger",
  "version": "1.0.0",
  "description": "记录所有消息的收发日志 / Log all incoming and outgoing messages",
  "type": "python",
  "entry": "plugin.py",
  "permissions": ["hooks.basic", "hooks.message", "data.own"],
  "provides": {
    "hooks": ["on_init", "on_message_received", "on_message_sending"]
  },
  "category": "utility",
  "tags": ["logging", "debug", "messages"]
}
```

## plugin.py

```python
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from openakita_plugin_sdk import PluginBase, PluginAPI


class Plugin(PluginBase):
    def __init__(self):
        self.api: PluginAPI | None = None
        self.log_file: Path | None = None
        self.message_count = 0

    def on_load(self, api: PluginAPI) -> None:
        self.api = api
        self.log_file = api.get_data_dir() / "messages.jsonl"

        # 基础钩子：hooks.basic 权限
        # Basic hooks: hooks.basic permission
        api.register_hook("on_init", self._on_init)

        # 消息钩子：hooks.message 权限
        # Message hooks: hooks.message permission
        api.register_hook("on_message_received", self._on_message_received)
        api.register_hook("on_message_sending", self._on_message_sending)

        api.log("Message logger plugin loaded")

    async def _on_init(self, **kwargs) -> None:
        self.api.log(f"Message logger ready, writing to {self.log_file}")

    async def _on_message_received(self, **kwargs) -> None:
        """记录收到的消息 / Log incoming messages."""
        self.message_count += 1
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "direction": "incoming",
            "channel": kwargs.get("channel", ""),
            "chat_id": kwargs.get("chat_id", ""),
            "user_id": kwargs.get("user_id", ""),
            "text": kwargs.get("text", "")[:200],  # 截断长消息 / Truncate long messages
        }
        self._write_log(entry)

    async def _on_message_sending(self, **kwargs) -> None:
        """记录发出的消息 / Log outgoing messages."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "direction": "outgoing",
            "channel": kwargs.get("channel", ""),
            "chat_id": kwargs.get("chat_id", ""),
            "text": kwargs.get("text", "")[:200],
        }
        self._write_log(entry)

    def _write_log(self, entry: dict) -> None:
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as e:
            self.api.log_error(f"Failed to write log: {e}", e)

    def on_unload(self) -> None:
        if self.api:
            self.api.log(f"Logged {self.message_count} messages total")
```

## 使用装饰器的替代写法 / Decorator Alternative

```python
from openakita_plugin_sdk import PluginBase, PluginAPI
from openakita_plugin_sdk.decorators import hook, auto_register

@hook("on_init")
async def setup(**kwargs):
    print("Plugin initialized!")

@hook("on_message_received")
async def log_incoming(**kwargs):
    text = kwargs.get("text", "")
    channel = kwargs.get("channel", "")
    print(f"[{channel}] Incoming: {text[:50]}")

@hook("on_message_sending")
async def log_outgoing(**kwargs):
    text = kwargs.get("text", "")
    print(f"Outgoing: {text[:50]}")


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        auto_register(api)

    def on_unload(self) -> None:
        pass
```

## 测试 / Test

```python
from openakita_plugin_sdk.testing import MockPluginAPI
from plugin import Plugin

def test_plugin_loads():
    api = MockPluginAPI()
    plugin = Plugin()
    plugin.on_load(api)

    assert "on_init" in api.registered_hooks
    assert "on_message_received" in api.registered_hooks
    assert "on_message_sending" in api.registered_hooks
    assert not any(level == "error" for level, _ in api.logs)

def test_unload():
    api = MockPluginAPI()
    plugin = Plugin()
    plugin.on_load(api)
    plugin.on_unload()
```

---

## 钩子权限速查 / Hook Permission Quick Reference

| 钩子 / Hook | 权限 / Permission | 级别 / Tier |
|-------------|------------------|------------|
| `on_init` | `hooks.basic` | Basic |
| `on_shutdown` | `hooks.basic` | Basic |
| `on_schedule` | `hooks.basic` | Basic |
| `on_message_received` | `hooks.message` | Advanced |
| `on_message_sending` | `hooks.message` | Advanced |
| `on_session_start` | `hooks.message` | Advanced |
| `on_session_end` | `hooks.message` | Advanced |
| `on_retrieve` | `hooks.retrieve` | Advanced |
| `on_tool_result` | `hooks.retrieve` | Advanced |
| `on_prompt_build` | `hooks.retrieve` | Advanced |

---

## 关键要点 / Key Points

- 回调可以是 `async def` 或普通 `def` / Callbacks can be async or sync
- 所有回调通过 `**kwargs` 接收参数 / All callbacks receive `**kwargs`
- 每个回调有独立的超时保护（默认 5 秒）/ Each callback has independent timeout (default 5s)
- 异常被隔离 — 一个钩子崩溃不影响其他钩子 / Exceptions are isolated — one crash doesn't affect others
- 累计 5 次错误的插件会被自动禁用 / Plugins with 5+ errors are auto-disabled
- 使用 `api.get_data_dir()` 存储持久化日志 / Use `api.get_data_dir()` for persistent logs

