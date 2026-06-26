"""Ollama plugin demo: register stub provider + vendor registry."""

from __future__ import annotations

import json
import logging

from openakita.plugins.api import PluginAPI, PluginBase

logger = logging.getLogger(__name__)


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        # Import here so plugin.json load does not pull `openakita.llm` at import time.
        from collections.abc import AsyncIterator

        import httpx

        from openakita.llm.providers.base import LLMProvider
        from openakita.llm.registries.base import ModelInfo, ProviderInfo, ProviderRegistry
        from openakita.llm.types import LLMRequest, LLMResponse, StopReason, TextBlock, Usage

        class OllamaProvider(LLMProvider):
            """Stub Ollama-backed provider (structure demo)."""

            __plugin_id__ = "ollama-provider"

            async def chat(self, request: LLMRequest) -> LLMResponse:
                _ = request
                return LLMResponse(
                    id="ollama-stub",
                    content=[
                        TextBlock(text="[ollama-provider stub] No real Ollama call was made.")
                    ],
                    stop_reason=StopReason.END_TURN,
                    usage=Usage(input_tokens=0, output_tokens=0),
                    model=self.model,
                )

            async def chat_stream(self, request: LLMRequest) -> AsyncIterator[dict]:
                _ = request
                yield {"type": "text_delta", "text": "[ollama-provider stub stream] "}

        class OllamaRegistry(ProviderRegistry):
            """Queries Ollama ``/api/tags`` for local model names."""

            def __init__(self, base_url: str = "http://localhost:11434") -> None:
                self._base_url = base_url.rstrip("/")
                self.info = ProviderInfo(
                    name="Ollama",
                    slug="ollama",
                    api_type="ollama_native",
                    default_base_url=self._base_url,
                    api_key_env_suggestion="",
                    supports_model_list=True,
                    supports_capability_api=False,
                    requires_api_key=False,
                    is_local=True,
                )

            async def list_models(self, api_key: str) -> list[ModelInfo]:
                _ = api_key
                url = f"{self._base_url}/api/tags"
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        response = await client.get(url)
                        response.raise_for_status()
                        data = response.json()
                except (httpx.HTTPError, json.JSONDecodeError, ValueError) as e:
                    logger.warning("OllamaRegistry list_models failed: %s", e)
                    return []

                models_raw = data.get("models") if isinstance(data, dict) else None
                if not isinstance(models_raw, list):
                    return []

                out: list[ModelInfo] = []
                for item in models_raw:
                    if not isinstance(item, dict):
                        continue
                    name = item.get("name")
                    if not name:
                        continue
                    out.append(ModelInfo(id=str(name), name=str(name)))
                return out

        cfg = api.get_config()
        base = (cfg.get("ollama_url") or "http://localhost:11434").rstrip("/")

        api.register_llm_provider("ollama_native", OllamaProvider)
        api.register_llm_registry("ollama", OllamaRegistry(base_url=base))
        api.log("Registered ollama_native provider and ollama registry", "info")

    def on_unload(self) -> None:
        pass
