"""
硅基流动 (SiliconFlow) 服务商注册表
"""

import httpx

from .base import ProviderRegistry, ProviderInfo, ModelInfo
from ..capabilities import infer_capabilities


class SiliconFlowRegistry(ProviderRegistry):
    """硅基流动注册表"""
    
    info = ProviderInfo(
        name="硅基流动",
        slug="siliconflow",
        api_type="openai",
        default_base_url="https://api.siliconflow.cn/v1",
        api_key_env_suggestion="SILICONFLOW_API_KEY",
        supports_model_list=True,
        supports_capability_api=False,  # 需要预置表
    )
    
    async def list_models(self, api_key: str) -> list[ModelInfo]:
        """获取硅基流动模型列表"""
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
                        capabilities=infer_capabilities(model_id, provider_slug="siliconflow"),
                    ))
                
                return sorted(models, key=lambda x: x.id)
                
            except httpx.HTTPError as e:
                # API 调用失败，返回预置模型列表
                return self._get_preset_models()
    
    def _is_chat_model(self, model_id: str) -> bool:
        """判断是否是 chat 模型"""
        # 排除嵌入模型、重排模型等
        exclude_keywords = ["embed", "rerank", "whisper", "tts", "speech"]
        return not any(kw in model_id.lower() for kw in exclude_keywords)
    
    def _get_preset_models(self) -> list[ModelInfo]:
        """返回预置模型列表"""
        preset = [
            "deepseek-ai/DeepSeek-V3",
            "deepseek-ai/DeepSeek-R1",
            "Qwen/Qwen2.5-72B-Instruct",
            "Qwen/Qwen2.5-32B-Instruct",
            "Qwen/QwQ-32B",
            "meta-llama/Llama-3.3-70B-Instruct",
        ]
        
        return [
            ModelInfo(
                id=model_id,
                name=model_id,
                capabilities=infer_capabilities(model_id, provider_slug="siliconflow"),
            )
            for model_id in preset
        ]
