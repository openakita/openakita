"""
Anthropic 服务商注册表
"""

from ..capabilities import infer_capabilities
from .base import ModelInfo, ProviderInfo, ProviderRegistry, create_registry_client


class AnthropicRegistry(ProviderRegistry):
    """Anthropic 注册表"""

    info = ProviderInfo(
        name="Anthropic (Official / Compatible)",
        slug="anthropic",
        api_type="anthropic",
        default_base_url="https://api.anthropic.com",
        api_key_env_suggestion="ANTHROPIC_API_KEY",
        supports_model_list=True,
        supports_capability_api=False,  # API 只返回基本信息
    )

    async def list_models(self, api_key: str) -> list[ModelInfo]:
        """获取 Anthropic 模型列表"""
        try:
            async with create_registry_client(self.info.default_base_url) as client:
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
        """返回预置模型列表"""
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
