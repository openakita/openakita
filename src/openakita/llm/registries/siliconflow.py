"""
SiliconFlow Provider Registry

Notes:
- China region: https://api.siliconflow.cn/v1
- International region: https://api.siliconflow.com/v1
"""

from ..capabilities import infer_capabilities
from .base import ModelInfo, ProviderInfo, ProviderRegistry, get_registry_client

# Preset model list (shared between China and International regions)
_PRESET_MODELS = [
    "deepseek-ai/DeepSeek-V3",
    "deepseek-ai/DeepSeek-R1",
    "Qwen/Qwen2.5-72B-Instruct",
    "Qwen/Qwen2.5-32B-Instruct",
    "Qwen/QwQ-32B",
    "meta-llama/Llama-3.3-70B-Instruct",
]


class _SiliconFlowBase(ProviderRegistry):
    """SiliconFlow base class (shared logic for China/International regions)."""

    def _provider_slug(self) -> str:
        return "siliconflow"

    async def list_models(self, api_key: str) -> list[ModelInfo]:
        """Fetch the SiliconFlow model list."""
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
                        capabilities=infer_capabilities(
                            model_id,
                            provider_slug=self._provider_slug(),
                        ),
                    )
                )
            return sorted(models, key=lambda x: x.id)

        except Exception:
            return self._get_preset_models()

    @staticmethod
    def _is_chat_model(model_id: str) -> bool:
        """Determine whether the model is a chat model."""
        exclude_keywords = ["embed", "rerank", "whisper", "tts", "speech"]
        return not any(kw in model_id.lower() for kw in exclude_keywords)

    def _get_preset_models(self) -> list[ModelInfo]:
        """Return the preset model list."""
        return [
            ModelInfo(
                id=model_id,
                name=model_id,
                capabilities=infer_capabilities(model_id, provider_slug=self._provider_slug()),
            )
            for model_id in _PRESET_MODELS
        ]


class SiliconFlowRegistry(_SiliconFlowBase):
    """SiliconFlow registry (China region)."""

    info = ProviderInfo(
        name="SiliconFlow (China)",
        slug="siliconflow",
        api_type="openai",
        default_base_url="https://api.siliconflow.cn/v1",
        api_key_env_suggestion="SILICONFLOW_API_KEY",
        supports_model_list=True,
        supports_capability_api=False,
    )


class SiliconFlowInternationalRegistry(_SiliconFlowBase):
    """SiliconFlow registry (International region)."""

    info = ProviderInfo(
        name="SiliconFlow (International)",
        slug="siliconflow-intl",
        api_type="openai",
        default_base_url="https://api.siliconflow.com/v1",
        api_key_env_suggestion="SILICONFLOW_API_KEY",
        supports_model_list=True,
        supports_capability_api=False,
    )
