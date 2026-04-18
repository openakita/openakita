"""
Zhipu AI (Zhipu / GLM) provider registry (OpenAI-compatible)

Notes:
- China region:   open.bigmodel.cn  -> https://open.bigmodel.cn/api/paas/v4
- International:  z.ai / api.z.ai   -> https://api.z.ai/api/paas/v4

Both regions are fully OpenAI-compatible, supporting /v4/chat/completions,
/v4/models, etc.  Common models: GLM-5, GLM-4.7, GLM-4.6V, GLM-4.5, GLM-4, etc.

API documentation:
  China:   https://open.bigmodel.cn/dev/api
  International: https://docs.z.ai/
"""

from ..capabilities import infer_capabilities
from .base import ModelInfo, ProviderInfo, ProviderRegistry, get_registry_client


class ZhipuChinaRegistry(ProviderRegistry):
    """Zhipu AI China region registry."""

    info = ProviderInfo(
        name="Zhipu AI (Zhipu - China)",
        slug="zhipu-cn",
        api_type="openai",
        default_base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key_env_suggestion="ZHIPU_API_KEY",
        supports_model_list=True,
        supports_capability_api=False,
    )

    async def list_models(self, api_key: str) -> list[ModelInfo]:
        """
        Fetch the model list for Zhipu AI China region.

        Zhipu is compatible with the OpenAI /models endpoint.
        If the API call fails, a preset list of common models is returned.
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
                        capabilities=infer_capabilities(mid, provider_slug="zhipu"),
                    )
                )
            return sorted(models, key=lambda x: x.id)

        except Exception:
            return self._get_preset_models()

    def _get_preset_models(self) -> list[ModelInfo]:
        """Return the preset model list."""
        preset = [
            "glm-5",
            "glm-5-plus",
            "glm-4.7",
            "glm-4.6v",
            "glm-4.5v",
            "glm-4",
            "glm-4-plus",
            "glm-4-air",
            "glm-4-airx",
            "glm-4-long",
            "glm-4-flash",
            "glm-4-flashx",
            "glm-4v",
            "glm-4v-plus",
            "autoglm-phone",
        ]
        return [
            ModelInfo(
                id=model_id,
                name=model_id,
                capabilities=infer_capabilities(model_id, provider_slug="zhipu"),
            )
            for model_id in preset
        ]


class ZhipuInternationalRegistry(ProviderRegistry):
    """Zhipu AI International region (Z.AI) registry."""

    info = ProviderInfo(
        name="Zhipu AI (Z.AI·International)",
        slug="zhipu-int",
        api_type="openai",
        default_base_url="https://api.z.ai/api/paas/v4",
        api_key_env_suggestion="ZHIPU_API_KEY",
        supports_model_list=True,
        supports_capability_api=False,
    )

    async def list_models(self, api_key: str) -> list[ModelInfo]:
        """Fetch the model list for Zhipu AI International region."""
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
                        capabilities=infer_capabilities(mid, provider_slug="zhipu"),
                    )
                )
            return sorted(models, key=lambda x: x.id)

        except Exception:
            return self._get_preset_models()

    def _get_preset_models(self) -> list[ModelInfo]:
        """Return the preset model list (shared with the China region)."""
        preset = [
            "glm-5",
            "glm-5-plus",
            "glm-4.7",
            "glm-4.6v",
            "glm-4.5v",
            "glm-4",
            "glm-4-plus",
            "glm-4-air",
            "glm-4v",
            "glm-4v-plus",
            "autoglm-phone",
        ]
        return [
            ModelInfo(
                id=model_id,
                name=model_id,
                capabilities=infer_capabilities(model_id, provider_slug="zhipu"),
            )
            for model_id in preset
        ]
