"""
MiniMax Provider Registry (OpenAI Compatible)

Reference (common base_url values):
- China region:  https://api.minimaxi.com/v1
- International: https://api.minimax.io/v1

Note: MiniMax does not provide a /v1/models endpoint; users must enter model names manually.
"""

from .base import ModelInfo, ProviderInfo, ProviderRegistry


class MiniMaxChinaRegistry(ProviderRegistry):
    info = ProviderInfo(
        name="MiniMax (China)",
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
        name="MiniMax (International)",
        slug="minimax-int",
        api_type="openai",
        default_base_url="https://api.minimax.io/v1",
        api_key_env_suggestion="MINIMAX_API_KEY",
        supports_model_list=False,
        supports_capability_api=False,
    )

    async def list_models(self, api_key: str) -> list[ModelInfo]:
        return []
