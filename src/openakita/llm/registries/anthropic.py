"""
Anthropic provider registry
"""

from ..capabilities import infer_capabilities
from .base import ModelInfo, ProviderInfo, ProviderRegistry, get_registry_client


class AnthropicRegistry(ProviderRegistry):
    """Anthropic registry"""

    info = ProviderInfo(
        name="Anthropic (Official / Compatible)",
        slug="anthropic",
        api_type="anthropic",
        default_base_url="https://api.anthropic.com",
        api_key_env_suggestion="ANTHROPIC_API_KEY",
        supports_model_list=True,
        supports_capability_api=False,  # API only returns basic info
    )

    async def list_models(self, api_key: str) -> list[ModelInfo]:
        """Fetch the list of Anthropic models"""
        client = get_registry_client()
        try:
            resp = await client.get(
                f"{self.info.default_base_url}/v1/models",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
            )
            resp.raise_for_status()
            data = resp.json()

            models = []
            for m in data.get("data", []):
                model_id = m.get("id", "")
                models.append(
                    ModelInfo(
                        id=model_id,
                        name=m.get("display_name", model_id),
                        capabilities=infer_capabilities(model_id, provider_slug="anthropic"),
                    )
                )
            return models

        except Exception:
            return self._get_preset_models()

    def _get_preset_models(self) -> list[ModelInfo]:
        """Return the preset model list"""
        preset = [
            "claude-opus-4-20250514",
            "claude-sonnet-4-20250514",
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
            "claude-3-opus-20240229",
            "claude-3-sonnet-20240229",
            "claude-3-haiku-20240307",
        ]

        return [
            ModelInfo(
                id=model_id,
                name=model_id,
                capabilities=infer_capabilities(model_id, provider_slug="anthropic"),
            )
            for model_id in preset
        ]
