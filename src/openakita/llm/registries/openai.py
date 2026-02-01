"""
OpenAI 服务商注册表
"""

import httpx

from .base import ProviderRegistry, ProviderInfo, ModelInfo
from ..capabilities import infer_capabilities


class OpenAIRegistry(ProviderRegistry):
    """OpenAI 注册表"""
    
    info = ProviderInfo(
        name="OpenAI",
        slug="openai",
        api_type="openai",
        default_base_url="https://api.openai.com/v1",
        api_key_env_suggestion="OPENAI_API_KEY",
        supports_model_list=True,
        supports_capability_api=False,  # API 只返回基本信息
    )
    
    async def list_models(self, api_key: str) -> list[ModelInfo]:
        """获取 OpenAI 模型列表"""
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.get(
                    f"{self.info.default_base_url}/models",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                    }
                )
                resp.raise_for_status()
                data = resp.json()
                
                models = []
                for m in data.get("data", []):
                    model_id = m.get("id", "")
                    # 只返回 chat 模型
                    if not self._is_chat_model(model_id):
                        continue
                    
                    models.append(ModelInfo(
                        id=model_id,
                        name=model_id,
                        capabilities=infer_capabilities(model_id, provider_slug="openai"),
                    ))
                
                return sorted(models, key=lambda x: x.id)
                
            except httpx.HTTPError as e:
                # API 调用失败，返回预置模型列表
                return self._get_preset_models()
    
    def _is_chat_model(self, model_id: str) -> bool:
        """判断是否是 chat 模型"""
        chat_prefixes = ["gpt-4", "gpt-3.5", "o1", "chatgpt"]
        return any(model_id.startswith(prefix) for prefix in chat_prefixes)
    
    def _get_preset_models(self) -> list[ModelInfo]:
        """返回预置模型列表"""
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
