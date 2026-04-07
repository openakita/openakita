"""
MiniMax 服务商注册表（OpenAI 兼容）

参考（常见 base_url）：
- 中国区： https://api.minimaxi.com/v1
- 国际区： https://api.minimax.io/v1

注意：MiniMax 不提供 /v1/models 端点，需用户手动填写模型名称。
"""

from .base import ModelInfo, ProviderInfo, ProviderRegistry


class MiniMaxChinaRegistry(ProviderRegistry):
    info = ProviderInfo(
        name="MiniMax（中国区）",
        slug="minimax-cn",
        api_type="openai",
        default_base_url="https://api.minimaxi.com/v1",
        api_key_env_suggestion="MINIMAX_API_KEY",
        supports_model_list=False,
        supports_capability_api=False,
    )

    async def list_models(self, api_key: str) -> list[ModelInfo]:
        return []


class MiniMaxInternationalRegistry(ProviderRegistry):
    info = ProviderInfo(
        name="MiniMax（国际区）",
        slug="minimax-int",
        api_type="openai",
        default_base_url="https://api.minimax.io/v1",
        api_key_env_suggestion="MINIMAX_API_KEY",
        supports_model_list=False,
        supports_capability_api=False,
    )

    async def list_models(self, api_key: str) -> list[ModelInfo]:
        return []
