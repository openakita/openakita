# LLM 提供商插件示例 / LLM Provider Plugin Example

注册一个新的 LLM 提供商，让 OpenAkita 支持新的大模型 API（如 Ollama、自建模型服务等）。

Registers a new LLM provider, enabling OpenAkita to support new model APIs (e.g., Ollama, self-hosted models).

**权限级别 / Permission Level:** Advanced（需要用户确认 / requires user approval）

---

## 目录结构 / Directory Structure

```
ollama-provider/
  plugin.json
  plugin.py
  config_schema.json
  README.md
```

## plugin.json

```json
{
  "id": "ollama-provider",
  "name": "Ollama LLM Provider",
  "version": "1.0.0",
  "description": "接入本地 Ollama 大模型服务 / Connect to local Ollama LLM service",
  "type": "python",
  "entry": "plugin.py",
  "permissions": ["llm.register", "config.read"],
  "provides": {
    "llm_provider": {
      "api_type": "ollama_native",
      "registry_slug": "ollama"
    }
  },
  "category": "ai",
  "tags": ["ollama", "llm", "local-ai"]
}
```

## plugin.py

LLM 插件使用**双重注册**机制：

LLM plugins use a **dual registration** mechanism:

1. **协议类 / Protocol class** (`LLMProvider`) — 处理实际的 API 调用 / Handles actual API calls
2. **厂商目录 / Vendor registry** (`ProviderRegistry`) — 提供模型发现 / Provides model discovery

```python
from __future__ import annotations

import json
from typing import Any

from openakita_plugin_sdk import PluginBase, PluginAPI
from openakita_plugin_sdk.llm import LLMProvider, ProviderRegistry, ProviderRegistryInfo


class OllamaProvider(LLMProvider):
    """Ollama API 协议实现 / Ollama API protocol implementation."""

    def __init__(self, config: Any) -> None:
        self.base_url = getattr(config, "base_url", "http://localhost:11434")

    async def chat(self, messages: list[dict], **kwargs) -> dict:
        import httpx

        model = kwargs.get("model", "llama3")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/api/chat",
                json={"model": model, "messages": messages, "stream": False},
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "content": data.get("message", {}).get("content", ""),
                "model": model,
                "usage": {},
            }

    async def chat_stream(self, messages: list[dict], **kwargs):
        import httpx

        model = kwargs.get("model", "llama3")
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/api/chat",
                json={"model": model, "messages": messages, "stream": True},
                timeout=120,
            ) as resp:
                async for line in resp.aiter_lines():
                    if line:
                        chunk = json.loads(line)
                        content = chunk.get("message", {}).get("content", "")
                        if content:
                            yield {"content": content}


class OllamaRegistry(ProviderRegistry):
    """Ollama 模型目录 — 从本地服务获取可用模型列表。
    Ollama model registry — fetches available models from local service.
    """

    def list_models(self) -> list[dict]:
        try:
            import httpx

            resp = httpx.get(f"{self.info.default_base_url}/api/tags", timeout=5)
            resp.raise_for_status()
            models = resp.json().get("models", [])
            return [
                {"id": m["name"], "name": m["name"], "size": m.get("size", 0)}
                for m in models
            ]
        except Exception:
            return []


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        config = api.get_config()
        base_url = config.get("ollama_url", "http://localhost:11434")

        api.register_llm_provider("ollama_native", OllamaProvider)

        registry = OllamaRegistry(ProviderRegistryInfo(
            slug="ollama",
            name="Ollama (Local)",
            api_type="ollama_native",
            default_base_url=base_url,
            api_key_env="",
        ))
        api.register_llm_registry("ollama", registry)
        api.log(f"Ollama LLM provider registered: {base_url}")

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

    assert "ollama_native" in api.registered_llm_providers
    assert "ollama" in api.registered_llm_registries

def test_unload():
    plugin = Plugin()
    api = MockPluginAPI()
    plugin.on_load(api)
    plugin.on_unload()
```

---

## 关键要点 / Key Points

- 继承 `LLMProvider` 实现 `chat()` 和 `chat_stream()` / Subclass `LLMProvider` for `chat()` and `chat_stream()`
- 继承 `ProviderRegistry` 实现 `list_models()` / Subclass `ProviderRegistry` for `list_models()`
- 注册时传入**类**（不是实例）给 `register_llm_provider()` / Pass a **class** (not instance) to `register_llm_provider()`
- `llm.register` 是 Advanced 级权限 / `llm.register` is Advanced-level permission
- 卸载时宿主自动清理注册 / Host automatically cleans up registrations on unload
