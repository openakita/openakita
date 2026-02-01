"""
Anthropic 服务商注册表
"""

import httpx

from .base import ProviderRegistry, ProviderInfo, ModelInfo
from ..capabilities import infer_capabilities


class AnthropicRegistry(ProviderRegistry):
    """Anthropic 注册表"""
    
    info = ProviderInfo(
        name="Anthropic",
        slug="anthropic",
        api_type="anthropic",
        default_base_url="https://api.anthropic.com",
        api_key_env_suggestion="ANTHROPIC_API_KEY",
        supports_model_list=True,
        supports_capability_api=False,  # API 只返回基本信息
    )
    
    async def list_models(self, api_key: str) -> list[ModelInfo]:
        """获取 Anthropic 模型列表"""
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.get(
                    f"{self.info.default_base_url}/v1/models",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                    }
                )
                resp.raise_for_status()
                data = resp.json()
                
                models = []
                for m in data.get("data", []):
                    model_id = m.get("id", "")
                    models.append(ModelInfo(
                        id=model_id,
                        name=m.get("display_name", model_id),
                        capabilities=infer_capabilities(model_id, provider_slug="anthropic"),
                    ))
                
                return models
                
            except httpx.HTTPError as e:
                # API 调用失败，返回预置模型列表
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
