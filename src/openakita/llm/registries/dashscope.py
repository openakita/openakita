"""
阿里云 DashScope 服务商注册表

采用混合方案：API 获取模型列表 + 预置能力表补充能力信息
"""

import httpx

from .base import ProviderRegistry, ProviderInfo, ModelInfo
from ..capabilities import infer_capabilities


class DashScopeRegistry(ProviderRegistry):
    """阿里云 DashScope 注册表"""
    
    info = ProviderInfo(
        name="阿里云 DashScope",
        slug="dashscope",
        api_type="openai",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key_env_suggestion="DASHSCOPE_API_KEY",
        supports_model_list=True,
        supports_capability_api=False,  # API 不返回能力信息，需要预置表
    )
    
    async def list_models(self, api_key: str) -> list[ModelInfo]:
        """
        获取 DashScope 模型列表
        
        使用混合方案：
        1. 调用 API 获取最新的可用模型列表
        2. 从预置能力表查找每个模型的能力
        3. 如果预置表没有该模型，使用智能推断
        """
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.get(
                    "https://dashscope.aliyuncs.com/api/v1/deployments/models",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                    },
                    params={"page_size": 200}
                )
                resp.raise_for_status()
                data = resp.json()
                
                models = []
                for m in data.get("output", {}).get("models", []):
                    model_name = m.get("model_name", "")
                    models.append(ModelInfo(
                        id=model_name,
                        name=model_name,
                        # 混合方案：传入 provider_slug="dashscope" 进行精确查找
                        capabilities=infer_capabilities(
                            model_name, 
                            provider_slug="dashscope"
                        ),
                    ))
                
                return sorted(models, key=lambda x: x.id)
                
            except httpx.HTTPError as e:
                # API 调用失败，返回预置模型列表
                return self._get_preset_models()
    
    def get_model_capabilities(self, model_id: str) -> dict:
        """
        获取模型能力（覆盖基类方法）
        
        优先级: 预置能力表(dashscope) > 跨服务商匹配 > 模型名推断 > 默认值
        """
        return infer_capabilities(model_id, provider_slug="dashscope")
    
    def _get_preset_models(self) -> list[ModelInfo]:
        """返回预置模型列表"""
        preset = [
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
        
        return [
            ModelInfo(
                id=model_id,
                name=model_id,
                capabilities=infer_capabilities(model_id, provider_slug="dashscope"),
            )
            for model_id in preset
        ]
