"""
服务商注册表

用于从各个 LLM 服务商获取模型列表和能力信息。

┌──────────────────────────────────────────────────────────────┐
│  数据来源:                                                   │
│  1. 内置 providers.json (同目录, 随版本更新)                 │
│  2. 工作区 data/custom_providers.json (用户自定义, 可选)     │
│                                                              │
│  合并规则: 内置列表为基础, 工作区文件按 slug 覆盖或追加。    │
│  用户可通过 AI 工具 (manage_provider action) 或手动编辑       │
│  data/custom_providers.json 来增删改服务商。                 │
│                                                              │
│  新增内置服务商时，只需：                                     │
│  1. 编写新的 XxxRegistry 类 (继承 ProviderRegistry)          │
│  2. 在 providers.json 中添加一条，registry_class 对应类名     │
│  3. 前端会自动同步 (构建时 vite 直接 import JSON)            │
└──────────────────────────────────────────────────────────────┘
"""

import json
import logging
from importlib import import_module
from pathlib import Path

from .base import ModelInfo, ProviderInfo, ProviderRegistry

_logger = logging.getLogger(__name__)

# ── 从 providers.json 加载内置服务商声明 ──
_PROVIDERS_JSON = Path(__file__).parent / "providers.json"
_BUILTIN_ENTRIES: list[dict] = json.loads(_PROVIDERS_JSON.read_text(encoding="utf-8"))

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


def _entry_to_provider_info(entry: dict) -> ProviderInfo:
    """将 JSON entry 转换为 ProviderInfo"""
    return ProviderInfo(
        name=entry["name"],
        slug=entry["slug"],
        api_type=entry["api_type"],
        default_base_url=entry["default_base_url"],
        api_key_env_suggestion=entry.get("api_key_env_suggestion", ""),
        supports_model_list=entry.get("supports_model_list", True),
        supports_capability_api=entry.get("supports_capability_api", False),
        requires_api_key=entry.get("requires_api_key", True),
        is_local=entry.get("is_local", False),
        coding_plan_base_url=entry.get("coding_plan_base_url"),
        coding_plan_api_type=entry.get("coding_plan_api_type"),
    )


# ── 工作区自定义服务商管理 ──


def _get_custom_providers_path() -> Path:
    """获取工作区自定义服务商文件路径 (与 llm_endpoints.json 同级)"""
    from ..config import get_default_config_path

    return get_default_config_path().parent / "custom_providers.json"


def load_custom_providers() -> list[dict]:
    """从工作区加载自定义服务商列表"""
    path = _get_custom_providers_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as e:
        _logger.warning(f"Failed to load custom providers from {path}: {e}")
        return []


def save_custom_providers(entries: list[dict]) -> None:
    """保存自定义服务商列表到工作区"""
    path = _get_custom_providers_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _logger.info(f"Saved {len(entries)} custom providers to {path}")


def _merge_provider_entries() -> list[dict]:
    """合并内置 + 工作区自定义服务商。

    自定义条目按 slug 覆盖内置条目；新 slug 追加到末尾。
    """
    merged: dict[str, dict] = {}
    for entry in _BUILTIN_ENTRIES:
        merged[entry["slug"]] = entry

    custom = load_custom_providers()
    for entry in custom:
        slug = entry.get("slug", "")
        if not slug:
            continue
        if slug in merged:
            merged[slug] = {**merged[slug], **entry}
        else:
            merged[slug] = entry

    return list(merged.values())


def _build_registry_for_entry(entry: dict) -> ProviderRegistry | None:
    """为单个 provider entry 构建 registry 实例。"""
    cls_name = entry.get("registry_class", "")
    if not cls_name:
        api_type = entry.get("api_type", "openai")
        cls_name = "AnthropicRegistry" if api_type == "anthropic" else "OpenAIRegistry"

    mod_name = _CLASS_MODULE_MAP.get(cls_name)
    if mod_name is None:
        _logger.warning(
            f"registry_class '{cls_name}' 未在 _CLASS_MODULE_MAP 中注册，"
            f"跳过服务商 '{entry.get('name', '?')}'"
        )
        return None
    try:
        mod = import_module(mod_name, package=__package__)
        cls = getattr(mod, cls_name)
    except (ImportError, AttributeError) as e:
        _logger.warning(
            f"Registry '{cls_name}' (module={mod_name}) 加载失败，"
            f"跳过服务商 '{entry.get('name', '?')}': {e}"
        )
        return None

    instance = cls()
    instance.info = _entry_to_provider_info(entry)
    return instance


def _build_registries() -> list[ProviderRegistry]:
    """根据合并后的服务商列表构建全部注册表实例。

    单个 registry 加载失败不影响其他 provider（仅记录警告并跳过）。
    """
    registries: list[ProviderRegistry] = []
    for entry in _merge_provider_entries():
        reg = _build_registry_for_entry(entry)
        if reg is not None:
            registries.append(reg)
    return registries


ALL_REGISTRIES = _build_registries()

REGISTRY_BY_SLUG = {r.info.slug: r for r in ALL_REGISTRIES}


def reload_registries() -> int:
    """重新加载服务商注册表（合并内置 + 自定义），返回加载数量。"""
    global ALL_REGISTRIES, REGISTRY_BY_SLUG
    ALL_REGISTRIES = _build_registries()
    REGISTRY_BY_SLUG = {r.info.slug: r for r in ALL_REGISTRIES}
    _logger.info(f"Reloaded {len(ALL_REGISTRIES)} provider registries")
    return len(ALL_REGISTRIES)


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
    "load_custom_providers",
    "save_custom_providers",
    "reload_registries",
]
