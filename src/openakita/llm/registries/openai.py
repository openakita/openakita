"""
OpenAI Provider Registry
"""

from ..capabilities import infer_capabilities
from .base import ModelInfo, ProviderInfo, ProviderRegistry, get_registry_client


class OpenAIRegistry(ProviderRegistry):
    """OpenAI registry"""

    info = ProviderInfo(
        name="OpenAI (Official / Compatible)",
        slug="openai",
        api_type="openai",
        default_base_url="https://api.openai.com/v1",
        api_key_env_suggestion="OPENAI_API_KEY",
        supports_model_list=True,
        supports_capability_api=False,  # API only returns basic info
    )

    async def list_models(self, api_key: str) -> list[ModelInfo]:
        """Fetch the OpenAI model list."""
        client = get_registry_client()
        try:
            resp = await client.get(
                f"{self.info.default_base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()

            models = []
            for m in data.get("data", []):
                model_id = m.get("id", "")
                if not self._is_chat_model(model_id):
                    continue
                models.append(
                    ModelInfo(
                        id=model_id,
                        name=model_id,
                        capabilities=infer_capabilities(model_id, provider_slug="openai"),
                    )
                )
            return sorted(models, key=lambda x: x.id)

        except Exception:
            return self._get_preset_models()

    def _is_chat_model(self, model_id: str) -> bool:
        """Check whether the model is a chat model."""
        chat_prefixes = ["gpt-4", "gpt-3.5", "o1", "chatgpt"]
        return any(model_id.startswith(prefix) for prefix in chat_prefixes)

    def _get_preset_models(self) -> list[ModelInfo]:
        """Return the preset model list."""
        preset = [
            "gpt-4o",
            "gpt-4o-mini",
            "gpt-4-turbo",
            "gpt-4",
            "gpt-3.5-turbo",
            "o1",
            "o1-mini",
            "o1-preview",
        ]

        return [
            ModelInfo(
                id=model_id,
                name=model_id,
                capabilities=infer_capabilities(model_id, provider_slug="openai"),
            )
            for model_id in preset
        ]
