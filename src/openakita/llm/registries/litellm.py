"""
LiteLLM provider registry.

LiteLLM is an AI gateway SDK that routes to 100+ LLM providers
(OpenAI, Anthropic, Google, Azure, Bedrock, Ollama, etc.) through
a unified interface. No proxy server needed.

Model strings use the ``provider/model`` format, e.g.
``anthropic/claude-sonnet-4-20250514``, ``azure/gpt-4o``,
``bedrock/anthropic.claude-3-haiku``, ``openai/gpt-4o``.

See https://docs.litellm.ai/docs/providers for the full list.
"""

from ..capabilities import infer_capabilities
from .base import ModelInfo, ProviderInfo, ProviderRegistry


class LiteLLMRegistry(ProviderRegistry):
    """LiteLLM registry - lists models from litellm.model_cost."""

    info = ProviderInfo(
        name="LiteLLM",
        slug="litellm",
        api_type="openai",
        default_base_url="https://api.openai.com/v1",
        api_key_env_suggestion="LITELLM_API_KEY",
        supports_model_list=True,
        supports_capability_api=False,
        requires_api_key=False,
        note="provider.litellm.note",
    )

    async def list_models(self, api_key: str) -> list[ModelInfo]:
        try:
            import litellm

            models: list[ModelInfo] = []
            seen: set[str] = set()
            for model_id in sorted(litellm.model_cost.keys()):
                if not model_id or model_id in seen:
                    continue
                if "/" not in model_id:
                    continue
                seen.add(model_id)
                models.append(
                    ModelInfo(
                        id=model_id,
                        name=model_id,
                        capabilities=infer_capabilities(model_id, provider_slug="litellm"),
                    )
                )
            return models
        except ImportError:
            return self._get_preset_models()
        except Exception:
            return self._get_preset_models()

    def _get_preset_models(self) -> list[ModelInfo]:
        preset = [
            "openai/gpt-4o",
            "openai/gpt-4o-mini",
            "anthropic/claude-sonnet-4-20250514",
            "anthropic/claude-haiku-4-5-20251001",
            "google/gemini-2.5-flash",
            "google/gemini-2.5-pro",
            "azure/gpt-4o",
            "bedrock/anthropic.claude-3-haiku-20240307-v1:0",
        ]
        return [
            ModelInfo(
                id=model_id,
                name=model_id,
                capabilities=infer_capabilities(model_id, provider_slug="litellm"),
            )
            for model_id in preset
        ]
