"""Shared validation helpers for persisted LLM endpoint configs."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from .capabilities import is_image_generation_model
from .types import is_local_endpoint_config

_CHAT_ENDPOINT_TYPES = {"endpoints", "compiler_endpoints"}


def endpoint_requires_api_key(endpoint: dict[str, Any]) -> bool:
    """Return whether an endpoint should have a real API key configured.

    Local runtimes such as Ollama/LM Studio can use a placeholder key, while
    hosted OpenAI-compatible providers, including OpenRouter free/auto routers,
    still require provider authentication.
    """
    provider = str(endpoint.get("provider") or "").strip()
    base_url = str(endpoint.get("base_url") or "").strip()
    return not is_local_endpoint_config(provider, base_url)


def endpoint_has_api_key(
    endpoint: dict[str, Any],
    *,
    api_key: str | None = None,
    existing_endpoint: dict[str, Any] | None = None,
    env_lookup: Callable[[str], str | None] | None = None,
) -> bool:
    """Check whether a new or edited endpoint has an effective API key."""
    if api_key and api_key.strip():
        return True

    direct = str(endpoint.get("api_key") or "").strip()
    if direct:
        return True

    lookup = env_lookup or os.environ.get
    env_names = [
        str(endpoint.get("api_key_env") or "").strip(),
        str((existing_endpoint or {}).get("api_key_env") or "").strip(),
    ]
    return any(env_name and (lookup(env_name) or "").strip() for env_name in env_names)


def missing_api_key_message(endpoint: dict[str, Any]) -> str:
    """Short, user-facing message for missing hosted-provider credentials."""
    provider = str(endpoint.get("provider") or "").strip().lower()
    model = str(endpoint.get("model") or "").strip().lower()
    if provider == "openrouter" or "openrouter.ai" in str(endpoint.get("base_url") or "").lower():
        if model in {"openrouter/free", "openrouter/auto"} or model.endswith(":free"):
            return "OpenRouter 免费/自动路由也需要 API Key，请在端点配置中填写 OPENROUTER_API_KEY。"
        return "OpenRouter 端点需要 API Key，请在端点配置中填写 OPENROUTER_API_KEY。"
    return "远程模型端点需要 API Key；本地 Ollama/LM Studio 才可以留空。"


def validate_endpoint_model_usage(endpoint: dict[str, Any], endpoint_type: str = "endpoints") -> str | None:
    """Return a friendly error when a model is saved to an incompatible endpoint list."""
    if endpoint_type not in _CHAT_ENDPOINT_TYPES:
        return None

    model = str(endpoint.get("model") or "").strip()
    if not is_image_generation_model(model):
        return None

    provider = str(endpoint.get("provider") or "").strip().lower()
    model_lower = model.lower()
    if provider == "dashscope" or model_lower.startswith(("qwen-image", "wanx-")):
        next_step = "请把 DashScope API Key 配置为 DASHSCOPE_API_KEY，然后在对话中使用内置 generate_image 工具生成图片。"
    else:
        next_step = "请使用该服务商的专用图片生成配置或图片生成工具，不要把它作为主聊天模型保存。"
    return (
        f"{model} 是图片生成模型，不是聊天模型端点。"
        f"{next_step}"
    )


def validate_endpoint_api_key(
    endpoint: dict[str, Any],
    *,
    api_key: str | None = None,
    existing_endpoint: dict[str, Any] | None = None,
    env_lookup: Callable[[str], str | None] | None = None,
) -> str | None:
    """Return a friendly validation error, or None when the endpoint is valid."""
    if not endpoint_requires_api_key(endpoint):
        return None
    if endpoint_has_api_key(
        endpoint,
        api_key=api_key,
        existing_endpoint=existing_endpoint,
        env_lookup=env_lookup,
    ):
        return None
    return missing_api_key_message(endpoint)
