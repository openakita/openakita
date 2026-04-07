"""
硅基流动 (SiliconFlow) 服务商注册表

说明：
- 中国区: https://api.siliconflow.cn/v1
- 国际区: https://api.siliconflow.com/v1
"""

from ..capabilities import infer_capabilities
from .base import ModelInfo, ProviderInfo, ProviderRegistry, get_registry_client

# 预置模型列表（国内/国际共用）
_PRESET_MODELS = [
    "deepseek-ai/DeepSeek-V3",
    "deepseek-ai/DeepSeek-R1",
    "Qwen/Qwen2.5-72B-Instruct",
    "Qwen/Qwen2.5-32B-Instruct",
    "Qwen/QwQ-32B",
    "meta-llama/Llama-3.3-70B-Instruct",
]


class _SiliconFlowBase(ProviderRegistry):
    """硅基流动基类（国内/国际共用逻辑）"""

    def _provider_slug(self) -> str:
        return "siliconflow"

    async def list_models(self, api_key: str) -> list[ModelInfo]:
        """获取硅基流动模型列表"""
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
        """判断是否是 chat 模型"""
        exclude_keywords = ["embed", "rerank", "whisper", "tts", "speech"]
        return not any(kw in model_id.lower() for kw in exclude_keywords)

    def _get_preset_models(self) -> list[ModelInfo]:
        """返回预置模型列表"""
        return [
            ModelInfo(
                id=model_id,
                name=model_id,
                capabilities=infer_capabilities(model_id, provider_slug=self._provider_slug()),
            )
            for model_id in _PRESET_MODELS
        ]


class SiliconFlowRegistry(_SiliconFlowBase):
    """硅基流动注册表（中国区）"""

    info = ProviderInfo(
        name="硅基流动 SiliconFlow（中国区）",
        slug="siliconflow",
        api_type="openai",
        default_base_url="https://api.siliconflow.cn/v1",
        api_key_env_suggestion="SILICONFLOW_API_KEY",
        supports_model_list=True,
        supports_capability_api=False,
    )


class SiliconFlowInternationalRegistry(_SiliconFlowBase):
    """硅基流动注册表（国际区）"""

    info = ProviderInfo(
        name="SiliconFlow (International)",
        slug="siliconflow-intl",
        api_type="openai",
        default_base_url="https://api.siliconflow.com/v1",
        api_key_env_suggestion="SILICONFLOW_API_KEY",
        supports_model_list=True,
        supports_capability_api=False,
    )
