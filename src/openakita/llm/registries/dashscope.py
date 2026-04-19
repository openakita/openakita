"""
Alibaba Cloud DashScope (Bailian) provider registry

Uses a hybrid approach: API-fetched model list + preset capability table for supplemental info

Notes:
- China region: https://dashscope.aliyuncs.com/compatible-mode/v1
- International region: https://dashscope-intl.aliyuncs.com/compatible-mode/v1
"""

from ..capabilities import infer_capabilities
from .base import ModelInfo, ProviderInfo, ProviderRegistry, get_registry_client

# Preset model list (shared by China/International)
_PRESET_MODELS = [
    "qwen3-max",
    "qwen3-max-preview",
    "qwen3-plus",
    "qwen3-coder-plus",
    "qwen-max",
    "qwen-max-latest",
    "qwen-plus",
    "qwen-plus-latest",
    "qwen-turbo",
    "qwen-turbo-latest",
    "qwen-vl-max",
    "qwen-vl-max-latest",
    "qwen-vl-plus",
    "qwen-vl-plus-latest",
    "qwq-plus",
    "qwq-32b",
]


class _DashScopeBase(ProviderRegistry):
    """DashScope base class (shared logic for China/International)."""

    async def list_models(self, api_key: str) -> list[ModelInfo]:
        """
        Fetch DashScope model list.

        Hybrid approach:
        1. Call API to get the latest available models
        2. Look up each model's capabilities from the preset table
        3. Fall back to intelligent inference if the model is not in the preset table
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
                        capabilities=infer_capabilities(mid, provider_slug="dashscope"),
                    )
                )

            return sorted(models, key=lambda x: x.id)

        except Exception:
            return self._get_preset_models()

    def get_model_capabilities(self, model_id: str) -> dict:
        """Get model capabilities."""
        return infer_capabilities(model_id, provider_slug="dashscope")

    @staticmethod
    def _get_preset_models() -> list[ModelInfo]:
        """Return preset model list."""
        return [
            ModelInfo(
                id=model_id,
                name=model_id,
                capabilities=infer_capabilities(model_id, provider_slug="dashscope"),
            )
            for model_id in _PRESET_MODELS
        ]


class DashScopeRegistry(_DashScopeBase):
    """Alibaba Cloud DashScope registry (China region)."""

    info = ProviderInfo(
        name="Alibaba Cloud DashScope (China)",
        slug="dashscope",
        api_type="openai",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key_env_suggestion="DASHSCOPE_API_KEY",
        supports_model_list=True,
        supports_capability_api=False,
    )


class DashScopeInternationalRegistry(_DashScopeBase):
    """Alibaba Cloud DashScope registry (International)."""

    info = ProviderInfo(
        name="Alibaba DashScope (International)",
        slug="dashscope-intl",
        api_type="openai",
        default_base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        api_key_env_suggestion="DASHSCOPE_API_KEY",
        supports_model_list=True,
        supports_capability_api=False,
    )
