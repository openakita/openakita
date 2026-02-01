"""
服务商注册表

用于从各个 LLM 服务商获取模型列表和能力信息。
"""

from .base import ProviderRegistry, ProviderInfo, ModelInfo
from .anthropic import AnthropicRegistry
from .openai import OpenAIRegistry
from .dashscope import DashScopeRegistry
from .openrouter import OpenRouterRegistry
from .siliconflow import SiliconFlowRegistry

# 所有注册表
ALL_REGISTRIES = [
    AnthropicRegistry(),
    OpenAIRegistry(),
    DashScopeRegistry(),
    OpenRouterRegistry(),
    SiliconFlowRegistry(),
]

# 按 slug 索引
REGISTRY_BY_SLUG = {r.info.slug: r for r in ALL_REGISTRIES}


def get_registry(slug: str) -> ProviderRegistry:
    """根据 slug 获取注册表"""
    if slug not in REGISTRY_BY_SLUG:
        raise ValueError(f"Unknown provider: {slug}")
    return REGISTRY_BY_SLUG[slug]


def list_providers() -> list[ProviderInfo]:
    """列出所有支持的服务商"""
    return [r.info for r in ALL_REGISTRIES]


__all__ = [
    "ProviderRegistry",
    "ProviderInfo",
    "ModelInfo",
    "AnthropicRegistry",
    "OpenAIRegistry",
    "DashScopeRegistry",
    "OpenRouterRegistry",
    "SiliconFlowRegistry",
    "ALL_REGISTRIES",
    "get_registry",
    "list_providers",
]
