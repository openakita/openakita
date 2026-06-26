"""
阿里云 DashScope (百炼) 服务商注册表

采用混合方案：API 获取模型列表 + 预置能力表补充能力信息

说明：
- 国内区: https://dashscope.aliyuncs.com/compatible-mode/v1
- 国际区: https://dashscope-intl.aliyuncs.com/compatible-mode/v1
"""

from ..capabilities import infer_capabilities
from .base import ModelInfo, ProviderInfo, ProviderRegistry, create_registry_client

# 预置模型列表（国内/国际共用）
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
    """DashScope 基类（国内/国际共用逻辑）"""

    async def list_models(self, api_key: str) -> list[ModelInfo]:
        """
        获取 DashScope 模型列表

        使用混合方案：
        1. 调用 API 获取最新的可用模型列表
        2. 从预置能力表查找每个模型的能力
        3. 如果预置表没有该模型，使用智能推断
        """
        try:
            async with create_registry_client(self.info.default_base_url) as client:
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
        """获取模型能力"""
        return infer_capabilities(model_id, provider_slug="dashscope")

    @staticmethod
    def _get_preset_models() -> list[ModelInfo]:
        """返回预置模型列表"""
        return [
            ModelInfo(
                id=model_id,
                name=model_id,
                capabilities=infer_capabilities(model_id, provider_slug="dashscope"),
            )
            for model_id in _PRESET_MODELS
        ]


class DashScopeRegistry(_DashScopeBase):
    """阿里云 DashScope 注册表（中国区）"""

    info = ProviderInfo(
        name="阿里云 DashScope（中国区）",
        slug="dashscope",
        api_type="openai",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key_env_suggestion="DASHSCOPE_API_KEY",
        supports_model_list=True,
        supports_capability_api=False,
    )


class DashScopeInternationalRegistry(_DashScopeBase):
    """阿里云 DashScope 注册表（国际区）"""

    info = ProviderInfo(
        name="Alibaba DashScope (International)",
        slug="dashscope-intl",
        api_type="openai",
        default_base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        api_key_env_suggestion="DASHSCOPE_API_KEY",
        supports_model_list=True,
        supports_capability_api=False,
    )
