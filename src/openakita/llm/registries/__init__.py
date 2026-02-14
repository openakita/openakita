"""
服务商注册表

用于从各个 LLM 服务商获取模型列表和能力信息。

┌──────────────────────────────────────────────────────────────┐
│  唯一数据源: providers.json (同目录)                          │
│                                                              │
│  新增服务商时，只需：                                         │
│  1. 编写新的 XxxRegistry 类 (继承 ProviderRegistry)          │
│  2. 在 providers.json 中添加一条，registry_class 对应类名     │
│  3. 前端会自动同步 (构建时 vite 直接 import JSON)            │
└──────────────────────────────────────────────────────────────┘
"""

import json
from importlib import import_module
from pathlib import Path

from .base import ModelInfo, ProviderInfo, ProviderRegistry

# ── 从 providers.json 加载服务商声明 ──
_PROVIDERS_JSON = Path(__file__).parent / "providers.json"
_PROVIDER_ENTRIES: list[dict] = json.loads(_PROVIDERS_JSON.read_text(encoding="utf-8"))

# ── registry_class -> 模块映射 ──
_CLASS_MODULE_MAP: dict[str, str] = {
    "AnthropicRegistry": ".anthropic",
    "OpenAIRegistry": ".openai",
    "DashScopeRegistry": ".dashscope",
    "DashScopeInternationalRegistry": ".dashscope",
    "KimiChinaRegistry": ".kimi",
    "KimiInternationalRegistry": ".kimi",
    "MiniMaxChinaRegistry": ".minimax",
    "MiniMaxInternationalRegistry": ".minimax",
    "DeepSeekRegistry": ".deepseek",
    "OpenRouterRegistry": ".openrouter",
    "SiliconFlowRegistry": ".siliconflow",
    "SiliconFlowInternationalRegistry": ".siliconflow",
    "VolcEngineRegistry": ".volcengine",
    "ZhipuChinaRegistry": ".zhipu",
    "ZhipuInternationalRegistry": ".zhipu",
}


def _build_registries() -> list[ProviderRegistry]:
    """根据 providers.json 构建全部注册表实例"""
    registries: list[ProviderRegistry] = []
    for entry in _PROVIDER_ENTRIES:
        cls_name = entry["registry_class"]
        mod_name = _CLASS_MODULE_MAP.get(cls_name)
        if mod_name is None:
            raise ValueError(
                f"providers.json 中的 registry_class '{cls_name}' "
                f"未在 _CLASS_MODULE_MAP 中注册，请在 __init__.py 中添加映射"
            )
        mod = import_module(mod_name, package=__package__)
        cls = getattr(mod, cls_name)
        registries.append(cls())
    return registries


ALL_REGISTRIES = _build_registries()

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
    "ALL_REGISTRIES",
    "get_registry",
    "list_providers",
]
