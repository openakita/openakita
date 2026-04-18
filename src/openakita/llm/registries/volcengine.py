"""
Volcengine (Volcengine / Ark) provider registry

Ark is ByteDance's LLM service platform, offering an OpenAI-compatible API.
It supports Doubao-series models, DeepSeek, and other models.

API docs: https://www.volcengine.com/docs/82379/1330626
Base URL: https://ark.cn-beijing.volces.com/api/v3
"""

from ..capabilities import infer_capabilities
from .base import ModelInfo, ProviderInfo, ProviderRegistry, get_registry_client


class VolcEngineRegistry(ProviderRegistry):
    """Volcengine (Ark) registry"""

    info = ProviderInfo(
        name="Volcengine",
        slug="volcengine",
        api_type="openai",
        default_base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key_env_suggestion="ARK_API_KEY",
        supports_model_list=True,
        supports_capability_api=False,
    )

    async def list_models(self, api_key: str) -> list[ModelInfo]:
        """
        Fetch the Volcengine model list.

        Ark is compatible with the OpenAI /models endpoint.
        If the API call fails, returns a preset list of common models.
        """
        client = get_registry_client()
        try:
            resp = await client.get(
                f"{self.info.default_base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()

            models: list[ModelInfo] = []
            seen: set[str] = set()
            for m in data.get("data", []) or []:
                if not isinstance(m, dict):
                    continue
                mid = (m.get("id") or "").strip()
                if not mid or mid in seen:
                    continue
                seen.add(mid)
                models.append(
                    ModelInfo(
                        id=mid,
                        name=mid,
                        capabilities=infer_capabilities(mid, provider_slug="volcengine"),
                    )
                )
            return sorted(models, key=lambda x: x.id)

        except Exception:
            return self._get_preset_models()

    def get_model_capabilities(self, model_id: str) -> dict:
        """Get model capabilities"""
        return infer_capabilities(model_id, provider_slug="volcengine")

    def _get_preset_models(self) -> list[ModelInfo]:
        """Return a preset list of common Ark models."""
        preset = [
            # Doubao series
            "doubao-seed-1-6",
            "doubao-seed-code",
            "doubao-1-5-pro-256k",
            "doubao-1-5-pro-32k",
            "doubao-1-5-lite-32k",
            "doubao-1-5-vision-pro-32k",
            "doubao-pro-256k",
            "doubao-pro-32k",
            "doubao-pro-4k",
            "doubao-lite-128k",
            "doubao-lite-32k",
            "doubao-lite-4k",
            "doubao-vision-pro-32k",
            "doubao-vision-lite-32k",
            # DeepSeek series (hosted on Ark)
            "deepseek-r1",
            "deepseek-v3",
            "deepseek-r1-distill-qwen-32b",
        ]

        return [
            ModelInfo(
                id=model_id,
                name=model_id,
                capabilities=infer_capabilities(model_id, provider_slug="volcengine"),
            )
            for model_id in preset
        ]
