"""
OpenRouter 服务商注册表

OpenRouter 的 API 返回完整的能力信息，是最理想的情况。
"""

import httpx

from .base import ProviderRegistry, ProviderInfo, ModelInfo


class OpenRouterRegistry(ProviderRegistry):
    """OpenRouter 注册表"""
    
    info = ProviderInfo(
        name="OpenRouter",
        slug="openrouter",
        api_type="openai",
        default_base_url="https://openrouter.ai/api/v1",
        api_key_env_suggestion="OPENROUTER_API_KEY",
        supports_model_list=True,
        supports_capability_api=True,  # OpenRouter 返回能力信息
    )
    
    async def list_models(self, api_key: str) -> list[ModelInfo]:
        """获取 OpenRouter 模型列表"""
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
                    architecture = m.get("architecture", {})
                    
                    models.append(ModelInfo(
                        id=model_id,
                        name=m.get("name", model_id),
                        capabilities=self._parse_capabilities(architecture, model_id),
                        context_window=m.get("context_length"),
                        max_output_tokens=m.get("top_provider", {}).get("max_completion_tokens"),
                        pricing=m.get("pricing"),
                    ))
                
                return sorted(models, key=lambda x: x.name)
                
            except httpx.HTTPError as e:
                # API 调用失败，返回空列表
                return []
    
    def _parse_capabilities(self, architecture: dict, model_id: str) -> dict:
        """从 OpenRouter 架构信息解析能力"""
        input_modalities = architecture.get("input_modalities", [])
        supported_params = architecture.get("supported_parameters", [])
        
        # 基本能力从 API 获取
        caps = {
            "text": "text" in input_modalities or True,  # 所有模型都支持文本
            "vision": "image" in input_modalities,
            "video": False,  # OpenRouter API 未明确返回视频支持
            "tools": "tools" in supported_params or "function_call" in supported_params,
            "thinking": False,  # OpenRouter API 未明确返回此信息
        }
        
        # Thinking 能力需要基于模型名推断（因为 OpenRouter API 不返回）
        model_lower = model_id.lower()
        if any(kw in model_lower for kw in ["o1", "r1", "qwq", "thinking"]):
            caps["thinking"] = True
        
        # Video 能力需要基于模型名推断
        if any(kw in model_lower for kw in ["kimi", "gemini"]):
            caps["video"] = True
        
        return caps
