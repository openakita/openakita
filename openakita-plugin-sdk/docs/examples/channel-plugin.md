# 通道插件示例 / Channel Plugin Example

注册一个新的 IM 通道适配器，让 OpenAkita 能通过新的平台收发消息。

Registers a new IM channel adapter so OpenAkita can send and receive messages through a new platform.

**权限级别 / Permission Level:** Advanced（需要用户确认 / requires user approval）

---

## 目录结构 / Directory Structure

```
echo-channel/
  plugin.json
  plugin.py
  README.md
```

## plugin.json

```json
{
  "id": "echo-channel",
  "name": "Echo Channel",
  "version": "1.0.0",
  "description": "回显所有消息的演示通道 / Demo channel that echoes all messages",
  "type": "python",
  "entry": "plugin.py",
  "permissions": ["channel.register", "hooks.basic", "hooks.message", "channel.send"],
  "provides": { "channels": ["echo"] },
  "category": "channel",
  "tags": ["demo", "channel"]
}
```

## plugin.py

```python
from openakita_plugin_sdk import PluginBase, PluginAPI
from openakita_plugin_sdk.channel import ChannelAdapter


class EchoAdapter(ChannelAdapter):
    """回显适配器 — 将所有消息打印到控制台。
    Echo adapter — prints all messages to console.
    实际开发中替换为真正的 IM SDK 调用。
    Replace with real IM SDK calls in production.
    """

    def __init__(self, channel_name: str = "echo"):
        super().__init__(channel_name=channel_name)

    async def start(self) -> None:
        print(f"[{self.channel_name}] Adapter started")

    async def stop(self) -> None:
        print(f"[{self.channel_name}] Adapter stopped")

    async def send_message(self, message) -> str:
        print(f"[{self.channel_name}] Send: {message}")
        return "echo-msg-id"

    async def send_text(self, chat_id: str, text: str, **kwargs) -> str:
        print(f"[{self.channel_name} -> {chat_id}] {text}")
        return "echo-msg-id"


def _adapter_factory(creds, *, channel_name, bot_id, agent_profile_id):
    """通道工厂函数 — 宿主调用此函数创建适配器实例。
    Channel factory — the host calls this to create adapter instances.

    参数 / Parameters:
        creds: 通道凭证配置 / Channel credentials config
        channel_name: 通道名称 / Channel name from config
        bot_id: 机器人 ID / Bot identifier
        agent_profile_id: Agent 配置 ID / Agent profile identifier
    """
    return EchoAdapter(channel_name=channel_name)


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        api.register_channel("echo", _adapter_factory)
        api.log("Echo channel registered")

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
    assert "echo" in api.registered_channels
```

---

## 关键要点 / Key Points

- 继承 `ChannelAdapter`，**必须调用 `super().__init__()`**，实现 4 个方法（`send_message`/`send_text` 返回 `str`）/ Subclass `ChannelAdapter`, **must call `super().__init__()`**, implement 4 methods (`send_message`/`send_text` return `str`)
- 工厂函数接收凭证和配置参数 / Factory receives credentials and config parameters
- 需要 `channel.register` 权限（Advanced 级）/ Requires `channel.register` (Advanced tier)
- 可选使用 `ChannelPluginMixin` 简化注册 / Optionally use `ChannelPluginMixin` for simpler registration
