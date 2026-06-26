"""echo-llm: LLM provider that echoes the prompt back as a response.

Registers both a LLMProvider (for chat/chat_stream) and a ProviderRegistry
(for model listing), exercising the full LLM plugin registration pipeline.

All openakita.llm imports are deferred to on_load() to avoid circular imports
when the plugin module is loaded before the full system is initialized.
"""

from __future__ import annotations

import logging

from openakita.plugins.api import PluginAPI, PluginBase

logger = logging.getLogger(__name__)


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        import time
        from collections.abc import AsyncIterator

        from openakita.llm.providers.base import LLMProvider
        from openakita.llm.registries.base import ModelInfo, ProviderInfo, ProviderRegistry
        from openakita.llm.types import LLMRequest, LLMResponse, StopReason, TextBlock, Usage

        class EchoProvider(LLMProvider):
            """Echoes the last user message back as the assistant response."""

            __plugin_id__ = "echo-llm"

            async def chat(self, request: LLMRequest) -> LLMResponse:
                user_text = ""
                for msg in reversed(request.messages):
                    if msg.role == "user":
                        for block in msg.content:
                            if hasattr(block, "text"):
                                user_text = block.text
                                break
                        if user_text:
                            break

                prefix = "[Echo LLM] "
                reply = f"{prefix}{user_text}" if user_text else f"{prefix}(no user message)"
                input_tokens = len(user_text) // 4 if user_text else 0
                output_tokens = len(reply) // 4

                return LLMResponse(
                    id=f"echo-{int(time.time())}",
                    content=[TextBlock(text=reply)],
                    stop_reason=StopReason.END_TURN,
                    usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens),
                    model=self.model or "echo-default",
                )

            async def chat_stream(self, request: LLMRequest) -> AsyncIterator[dict]:
                response = await self.chat(request)
                text = response.content[0].text if response.content else ""
                for ch in text:
                    yield {"type": "text_delta", "text": ch}
                yield {
                    "type": "message_stop",
                    "usage": {
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens,
                    },
                }

        class EchoRegistry(ProviderRegistry):
            """Registry that returns a fixed list of echo models."""

            def __init__(self) -> None:
                self.info = ProviderInfo(
                    name="Echo",
                    slug="echo",
                    api_type="echo",
                    default_base_url="local://echo",
                    api_key_env_suggestion="",
                    supports_model_list=True,
                    supports_capability_api=False,
                    requires_api_key=False,
                    is_local=True,
                )

            async def list_models(self, api_key: str) -> list[ModelInfo]:
                return [
                    ModelInfo(id="echo-default", name="Echo Default", capabilities={"text": True}),
                    ModelInfo(id="echo-verbose", name="Echo Verbose", capabilities={"text": True}),
                ]

        api.register_llm_provider("echo", EchoProvider)
        api.register_llm_registry("echo", EchoRegistry())
        api.log("Registered echo LLM provider and registry")

    def on_unload(self) -> None:
        pass
