"""
Setup Center Bridge.

This module provides a stable Python entrypoint for Setup Center (the Tauri app):

- `python -m openakita.setup_center.bridge list-providers`
- `python -m openakita.setup_center.bridge list-models --api-type ... --base-url ... [--provider-slug ...]`
- `python -m openakita.setup_center.bridge list-skills --workspace-dir ...`

All output is JSON on stdout; errors go to stderr and return a non-zero exit code.
"""

from __future__ import annotations

import openakita._ensure_utf8  # noqa: F401  # isort: skip

import argparse
import asyncio
import json
import os
import re
import sys
import zipfile
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


def _json_print(obj: Any) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.write("\n")


def _to_dict(obj: Any) -> Any:
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, dict):
        return {k: _to_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_dict(v) for v in obj]
    return obj


def list_providers() -> None:
    from openakita.llm.registries import list_providers as _list_providers

    providers = _list_providers()
    _json_print([_to_dict(p) for p in providers])


async def _list_models_openai(api_key: str, base_url: str, provider_slug: str | None) -> list[dict]:
    import httpx

    from openakita.llm.capabilities import infer_capabilities

    def _is_minimax_provider() -> bool:
        slug = (provider_slug or "").strip().lower()
        b = (base_url or "").strip().lower()
        return slug in {"minimax", "minimax-cn", "minimax-int"} or "minimax" in b or "minimaxi" in b

    def _is_volc_coding_plan_provider() -> bool:
        slug = (provider_slug or "").strip().lower()
        b = (base_url or "").strip().lower()
        is_volc = slug == "volcengine" or "volces.com" in b
        return is_volc and "/api/coding" in b

    def _is_longcat_provider() -> bool:
        slug = (provider_slug or "").strip().lower()
        b = (base_url or "").strip().lower()
        return slug == "longcat" or "longcat.chat" in b

    def _is_dashscope_coding_plan_provider() -> bool:
        slug = (provider_slug or "").strip().lower()
        b = (base_url or "").strip().lower()
        is_dashscope = slug in {"dashscope", "dashscope-intl"} or "dashscope.aliyuncs.com" in b
        return is_dashscope and "coding" in b

    def _is_qianfan_coding_plan_provider() -> bool:
        slug = (provider_slug or "").strip().lower()
        b = (base_url or "").strip().lower()
        is_qianfan = slug == "qianfan" or "qianfan.baidubce.com" in b
        return is_qianfan and "coding" in b

    def _minimax_fallback_models() -> list[dict]:
        # The MiniMax Anthropic/OpenAI-compatible docs only list a fixed model set and provide no /models endpoint.
        ids = [
            "MiniMax-M2.5",
            "MiniMax-M2.5-highspeed",
            "MiniMax-M2.1",
            "MiniMax-M2.1-highspeed",
            "MiniMax-M2",
        ]
        out = [
            {
                "id": mid,
                "name": mid,
                "capabilities": infer_capabilities(mid, provider_slug="minimax"),
            }
            for mid in ids
        ]
        out.sort(key=lambda x: x["id"])
        return out

    def _qianfan_coding_plan_fallback_models() -> list[dict]:
        ids = [
            "kimi-k2.5",
            "deepseek-v3.2",
            "glm-5",
            "minimax-m2.5",
        ]
        return [
            {
                "id": mid,
                "name": mid,
                "capabilities": infer_capabilities(mid, provider_slug="qianfan"),
            }
            for mid in ids
        ]

    def _volc_coding_plan_fallback_models() -> list[dict]:
        ids = [
            "doubao-seed-2.0-code",
            "doubao-seed-code",
            "glm-4.7",
            "deepseek-v3.2",
            "kimi-k2-thinking",
            "kimi-k2.5",
        ]
        return [
            {
                "id": mid,
                "name": mid,
                "capabilities": infer_capabilities(mid, provider_slug="volcengine"),
            }
            for mid in ids
        ]

    def _longcat_fallback_models() -> list[dict]:
        ids = [
            "LongCat-Flash-Chat",
            "LongCat-Flash-Thinking",
            "LongCat-Flash-Thinking-2601",
            "LongCat-Flash-Lite",
        ]
        out = [
            {
                "id": mid,
                "name": mid,
                "capabilities": infer_capabilities(mid, provider_slug="longcat"),
            }
            for mid in ids
        ]
        out.sort(key=lambda x: x["id"])
        return out

    def _dashscope_coding_plan_fallback_models() -> list[dict]:
        ids = [
            "qwen3.5-plus",
            "kimi-k2.5",
            "glm-5",
            "MiniMax-M2.5",
            "qwen3-max-2026-01-23",
            "qwen3-coder-next",
            "qwen3-coder-plus",
            "glm-4.7",
        ]
        out = [
            {
                "id": mid,
                "name": mid,
                "capabilities": infer_capabilities(mid, provider_slug="dashscope"),
            }
            for mid in ids
        ]
        out.sort(key=lambda x: x["id"])
        return out

    if _is_volc_coding_plan_provider():
        return _volc_coding_plan_fallback_models()
    if _is_dashscope_coding_plan_provider():
        return _dashscope_coding_plan_fallback_models()
    if _is_qianfan_coding_plan_provider():
        return _qianfan_coding_plan_fallback_models()
    if _is_longcat_provider():
        return _longcat_fallback_models()

    # The MiniMax-compatible endpoint has no model list endpoint; return the documented built-in candidates to avoid useless probes and false positives.
    if _is_minimax_provider():
        return _minimax_fallback_models()

    from openakita.llm.types import normalize_base_url

    url = normalize_base_url(base_url) + "/models"
    # Local services (Ollama/LM Studio, etc.) don't need a real API key; use a placeholder.
    effective_key = api_key.strip() or "local"
    auth_header = f"Bearer {effective_key}"

    async def _ensure_auth(request: httpx.Request):
        request.headers.setdefault("Authorization", auth_header)

    from openakita.llm.providers.proxy_utils import get_httpx_client_kwargs

    _is_local = any(h in base_url.lower() for h in ("localhost", "127.0.0.1", "[::1]"))
    client_kw = get_httpx_client_kwargs(timeout=30, is_local=_is_local)
    client_kw["follow_redirects"] = True
    client_kw["event_hooks"] = {"request": [_ensure_auth]}

    async with httpx.AsyncClient(**client_kw) as client:
        try:
            resp = await client.get(url, headers={"Authorization": auth_header})
            resp.raise_for_status()
            ct = (resp.headers.get("content-type") or "").lower()
            if "json" not in ct:
                preview = resp.text[:200].strip()
                raise ValueError(
                    f"The API returned a non-JSON response (content-type: {ct}). "
                    f"Please check that the Base URL is correct (it usually needs to end with /v1)."
                    f"\nResponse preview: {preview}"
                )
            data = resp.json()
        except httpx.HTTPStatusError:
            raise

    out: list[dict] = []
    for m in data.get("data", []):
        mid = str(m.get("id", "")).strip()
        if not mid:
            continue
        out.append(
            {
                "id": mid,
                "name": mid,
                "capabilities": infer_capabilities(mid, provider_slug=provider_slug),
            }
        )
    out.sort(key=lambda x: x["id"])
    return out


async def _list_models_anthropic(
    api_key: str, base_url: str, provider_slug: str | None
) -> list[dict]:
    import httpx

    from openakita.llm.capabilities import infer_capabilities

    def _is_minimax_provider() -> bool:
        slug = (provider_slug or "").strip().lower()
        b = (base_url or "").strip().lower()
        return slug in {"minimax", "minimax-cn", "minimax-int"} or "minimax" in b or "minimaxi" in b

    def _is_volc_coding_plan_provider() -> bool:
        slug = (provider_slug or "").strip().lower()
        b = (base_url or "").strip().lower()
        is_volc = slug == "volcengine" or "volces.com" in b
        return is_volc and "/api/coding" in b

    def _is_longcat_provider() -> bool:
        slug = (provider_slug or "").strip().lower()
        b = (base_url or "").strip().lower()
        return slug == "longcat" or "longcat.chat" in b

    def _is_dashscope_coding_plan_provider() -> bool:
        slug = (provider_slug or "").strip().lower()
        b = (base_url or "").strip().lower()
        is_dashscope = slug in {"dashscope", "dashscope-intl"} or "dashscope.aliyuncs.com" in b
        return is_dashscope and "coding" in b

    def _is_qianfan_coding_plan_provider() -> bool:
        slug = (provider_slug or "").strip().lower()
        b = (base_url or "").strip().lower()
        is_qianfan = slug == "qianfan" or "qianfan.baidubce.com" in b
        return is_qianfan and "coding" in b

    def _minimax_fallback_models() -> list[dict]:
        ids = [
            "MiniMax-M2.5",
            "MiniMax-M2.5-highspeed",
            "MiniMax-M2.1",
            "MiniMax-M2.1-highspeed",
            "MiniMax-M2",
        ]
        return [
            {
                "id": mid,
                "name": mid,
                "capabilities": infer_capabilities(mid, provider_slug="minimax"),
            }
            for mid in ids
        ]

    def _qianfan_coding_plan_fallback_models() -> list[dict]:
        ids = [
            "kimi-k2.5",
            "deepseek-v3.2",
            "glm-5",
            "minimax-m2.5",
        ]
        return [
            {
                "id": mid,
                "name": mid,
                "capabilities": infer_capabilities(mid, provider_slug="qianfan"),
            }
            for mid in ids
        ]

    def _volc_coding_plan_fallback_models() -> list[dict]:
        ids = [
            "doubao-seed-2.0-code",
            "doubao-seed-code",
            "glm-4.7",
            "deepseek-v3.2",
            "kimi-k2-thinking",
            "kimi-k2.5",
        ]
        return [
            {
                "id": mid,
                "name": mid,
                "capabilities": infer_capabilities(mid, provider_slug="volcengine"),
            }
            for mid in ids
        ]

    def _longcat_fallback_models() -> list[dict]:
        ids = [
            "LongCat-Flash-Chat",
            "LongCat-Flash-Thinking",
            "LongCat-Flash-Thinking-2601",
            "LongCat-Flash-Lite",
        ]
        return [
            {
                "id": mid,
                "name": mid,
                "capabilities": infer_capabilities(mid, provider_slug="longcat"),
            }
            for mid in ids
        ]

    def _dashscope_coding_plan_fallback_models() -> list[dict]:
        ids = [
            "qwen3.5-plus",
            "kimi-k2.5",
            "glm-5",
            "MiniMax-M2.5",
            "qwen3-max-2026-01-23",
            "qwen3-coder-next",
            "qwen3-coder-plus",
            "glm-4.7",
        ]
        return [
            {
                "id": mid,
                "name": mid,
                "capabilities": infer_capabilities(mid, provider_slug="dashscope"),
            }
            for mid in ids
        ]

    if _is_volc_coding_plan_provider():
        return _volc_coding_plan_fallback_models()
    if _is_dashscope_coding_plan_provider():
        return _dashscope_coding_plan_fallback_models()
    if _is_qianfan_coding_plan_provider():
        return _qianfan_coding_plan_fallback_models()
    if _is_longcat_provider():
        return _longcat_fallback_models()

    # The MiniMax-compatible endpoint has no model list endpoint; return the documented built-in candidates to avoid useless probes and false positives.
    if _is_minimax_provider():
        return _minimax_fallback_models()

    b = base_url.rstrip("/")
    url = b + "/models" if b.endswith("/v1") else b + "/v1/models"

    from openakita.llm.providers.proxy_utils import get_httpx_client_kwargs

    _is_local = any(h in base_url.lower() for h in ("localhost", "127.0.0.1", "[::1]"))
    client_kw = get_httpx_client_kwargs(timeout=30, is_local=_is_local)

    async with httpx.AsyncClient(**client_kw) as client:
        try:
            resp = await client.get(
                url,
                headers={
                    "x-api-key": api_key,
                    # Some Anthropic-compatible gateways only recognize Bearer.
                    "Authorization": f"Bearer {api_key}",
                    "anthropic-version": "2023-06-01",
                },
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError:
            raise

    out: list[dict] = []
    for m in data.get("data", []):
        mid = str(m.get("id", "")).strip()
        if not mid:
            continue
        out.append(
            {
                "id": mid,
                "name": str(m.get("display_name", mid)),
                "capabilities": infer_capabilities(mid, provider_slug=provider_slug),
            }
        )
    return out


async def list_models(
    api_type: str, base_url: str, provider_slug: str | None, api_key: str
) -> None:
    api_type = (api_type or "").strip().lower()
    base_url = (base_url or "").strip()
    if not api_type:
        raise ValueError("--api-type cannot be empty")
    if not base_url:
        raise ValueError("--base-url cannot be empty")
    # Local providers (Ollama/LM Studio, etc.) don't need an API key; allow empty.
    # The frontend passes a placeholder key, but a fully empty value is also tolerated.

    if api_type in ("openai", "openai_responses"):
        _json_print(await _list_models_openai(api_key, base_url, provider_slug))
        return
    if api_type == "anthropic":
        _json_print(await _list_models_anthropic(api_key, base_url, provider_slug))
        return

    raise ValueError(f"Unsupported api-type: {api_type}")


async def health_check_endpoint(workspace_dir: str, endpoint_name: str | None) -> None:
    """Probe LLM endpoint connectivity while updating business state (cooldown/mark_healthy)."""
    import time

    from openakita.llm.client import LLMClient

    wd = Path(workspace_dir).expanduser().resolve()
    config_path = wd / "data" / "llm_endpoints.json"
    if not config_path.exists():
        raise ValueError(f"Endpoint config file not found: {config_path}")

    env_path = wd / ".env"
    if env_path.exists():
        for line in env_path.read_bytes().decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            eq = line.find("=")
            if eq > 0:
                val = line[eq + 1 :].strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                    val = val[1:-1]
                os.environ.setdefault(line[:eq].strip(), val)

    client = LLMClient(config_path=config_path)

    results = []
    targets = list(client._providers.items())
    if endpoint_name:
        targets = [(n, p) for n, p in targets if n == endpoint_name]
        if not targets:
            raise ValueError(f"Endpoint not found: {endpoint_name}")

    for name, provider in targets:
        t0 = time.time()
        try:
            await provider.health_check()
            latency = round((time.time() - t0) * 1000)
            results.append(
                {
                    "name": name,
                    "status": "healthy",
                    "latency_ms": latency,
                    "error": None,
                    "error_category": None,
                    "consecutive_failures": 0,
                    "cooldown_remaining": 0,
                    "is_extended_cooldown": False,
                    "last_checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
            )
        except Exception as e:
            latency = round((time.time() - t0) * 1000)
            results.append(
                {
                    "name": name,
                    "status": "unhealthy" if provider.consecutive_cooldowns >= 3 else "degraded",
                    "latency_ms": latency,
                    "error": str(e)[:500],
                    "error_category": provider.error_category,
                    "consecutive_failures": provider.consecutive_cooldowns,
                    "cooldown_remaining": round(provider.cooldown_remaining),
                    "is_extended_cooldown": provider.is_extended_cooldown,
                    "last_checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
            )

    _json_print(results)


async def health_check_im(workspace_dir: str, channel: str | None) -> None:
    """Probe IM channel connectivity."""
    import httpx

    wd = Path(workspace_dir).expanduser().resolve()

    env: dict[str, str] = {}
    env_path = wd / ".env"
    if env_path.exists():
        for line in env_path.read_bytes().decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            eq = line.find("=")
            if eq > 0:
                env[line[:eq].strip()] = line[eq + 1 :]

    channels_def = [
        {
            "id": "telegram",
            "name": "Telegram",
            "enabled_key": "TELEGRAM_ENABLED",
            "required_keys": ["TELEGRAM_BOT_TOKEN"],
        },
        {
            "id": "feishu",
            "name": "Feishu",
            "enabled_key": "FEISHU_ENABLED",
            "required_keys": ["FEISHU_APP_ID", "FEISHU_APP_SECRET"],
        },
        {
            "id": "wework",
            "name": "WeCom",
            "enabled_key": "WEWORK_ENABLED",
            "required_keys": ["WEWORK_CORP_ID", "WEWORK_TOKEN", "WEWORK_ENCODING_AES_KEY"],
        },
        {
            "id": "dingtalk",
            "name": "DingTalk",
            "enabled_key": "DINGTALK_ENABLED",
            "required_keys": ["DINGTALK_CLIENT_ID", "DINGTALK_CLIENT_SECRET"],
        },
        {
            "id": "onebot",
            "name": "OneBot",
            "enabled_key": "ONEBOT_ENABLED",
            "required_keys": [],  # Dynamic: forward needs WS_URL, reverse needs a port
        },
        {
            "id": "qqbot",
            "name": "QQ Official Bot",
            "enabled_key": "QQBOT_ENABLED",
            "required_keys": ["QQBOT_APP_ID", "QQBOT_APP_SECRET"],
        },
        {
            "id": "wework_ws",
            "name": "WeCom (WS)",
            "enabled_key": "WEWORK_WS_ENABLED",
            "required_keys": ["WEWORK_WS_BOT_ID", "WEWORK_WS_SECRET"],
        },
        {
            "id": "wechat",
            "name": "WeChat",
            "enabled_key": "WECHAT_ENABLED",
            "required_keys": ["WECHAT_TOKEN"],
        },
    ]

    import time

    targets = channels_def
    if channel:
        targets = [c for c in targets if c["id"] == channel]
        if not targets:
            raise ValueError(f"Unknown IM channel: {channel}")

    results = []
    for ch in targets:
        enabled = env.get(ch["enabled_key"], "").strip().lower() in ("true", "1", "yes")
        if not enabled:
            results.append(
                {
                    "channel": ch["id"],
                    "name": ch["name"],
                    "status": "disabled",
                    "error": None,
                    "last_checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
            )
            continue

        missing = [k for k in ch["required_keys"] if not env.get(k, "").strip()]
        if missing:
            results.append(
                {
                    "channel": ch["id"],
                    "name": ch["name"],
                    "status": "unhealthy",
                    "error": f"Missing configuration: {', '.join(missing)}",
                    "last_checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
            )
            continue

        # Actual connectivity test
        try:
            from openakita.llm.providers.proxy_utils import get_httpx_client_kwargs

            ch_client_kw = get_httpx_client_kwargs(timeout=15)
            async with httpx.AsyncClient(**ch_client_kw) as client:
                if ch["id"] == "telegram":
                    token = env["TELEGRAM_BOT_TOKEN"]
                    resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
                    resp.raise_for_status()
                    data = resp.json()
                    if not data.get("ok"):
                        raise Exception(data.get("description", "Telegram API returned an error"))
                elif ch["id"] == "feishu":
                    app_id = env["FEISHU_APP_ID"]
                    app_secret = env["FEISHU_APP_SECRET"]
                    resp = await client.post(
                        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                        json={"app_id": app_id, "app_secret": app_secret},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    if data.get("code", -1) != 0:
                        raise Exception(data.get("msg", "Feishu verification failed"))
                elif ch["id"] == "wework":
                    # Smart bot mode doesn't need secret/access_token and can't be verified via API.
                    # Only check that required parameters are complete.
                    corp_id = env.get("WEWORK_CORP_ID", "").strip()
                    token = env.get("WEWORK_TOKEN", "").strip()
                    aes_key = env.get("WEWORK_ENCODING_AES_KEY", "").strip()
                    if not corp_id or not token or not aes_key:
                        missing = []
                        if not corp_id:
                            missing.append("WEWORK_CORP_ID")
                        if not token:
                            missing.append("WEWORK_TOKEN")
                        if not aes_key:
                            missing.append("WEWORK_ENCODING_AES_KEY")
                        raise Exception(f"Missing required parameters: {', '.join(missing)}")
                elif ch["id"] == "dingtalk":
                    client_id = env["DINGTALK_CLIENT_ID"]
                    client_secret = env["DINGTALK_CLIENT_SECRET"]
                    resp = await client.post(
                        "https://api.dingtalk.com/v1.0/oauth2/accessToken",
                        json={"appKey": client_id, "appSecret": client_secret},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    if not data.get("accessToken"):
                        raise Exception(data.get("message", "DingTalk verification failed"))
                elif ch["id"] == "onebot":
                    ob_mode = env.get("ONEBOT_MODE", "reverse").strip().lower()
                    if ob_mode == "forward":
                        ws_url = env.get("ONEBOT_WS_URL", "")
                        if not ws_url.startswith(("ws://", "wss://")):
                            raise Exception(f"Invalid WebSocket URL: {ws_url}")
                        http_url = ws_url.replace("ws://", "http://").replace("wss://", "https://")
                        resp = await client.get(http_url, timeout=5)
                    else:
                        port_str = env.get("ONEBOT_REVERSE_PORT", "6700").strip()
                        try:
                            port = int(port_str)
                            if not (1 <= port <= 65535):
                                raise ValueError
                        except (ValueError, TypeError):
                            raise Exception(f"Invalid port: {port_str}")
                elif ch["id"] == "qqbot":
                    # QQ Official Bot: verify AppID/AppSecret by fetching an access token.
                    app_id = env["QQBOT_APP_ID"]
                    app_secret = env["QQBOT_APP_SECRET"]
                    resp = await client.post(
                        "https://bots.qq.com/app/getAppAccessToken",
                        json={"appId": app_id, "clientSecret": app_secret},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    if not data.get("access_token"):
                        raise Exception(data.get("message", "QQ bot verification failed"))
                elif ch["id"] == "wework_ws":
                    bot_id = env.get("WEWORK_WS_BOT_ID", "").strip()
                    secret = env.get("WEWORK_WS_SECRET", "").strip()
                    if not bot_id or not secret:
                        missing_ws = []
                        if not bot_id:
                            missing_ws.append("WEWORK_WS_BOT_ID")
                        if not secret:
                            missing_ws.append("WEWORK_WS_SECRET")
                        raise Exception(f"Missing required parameters: {', '.join(missing_ws)}")
                elif ch["id"] == "wechat":
                    token = env.get("WECHAT_TOKEN", "").strip()
                    if not token:
                        raise Exception("Missing required parameter: WECHAT_TOKEN")

            results.append(
                {
                    "channel": ch["id"],
                    "name": ch["name"],
                    "status": "healthy",
                    "error": None,
                    "last_checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
            )
        except Exception as e:
            results.append(
                {
                    "channel": ch["id"],
                    "name": ch["name"],
                    "status": "unhealthy",
                    "error": str(e)[:500],
                    "last_checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
            )

    _json_print(results)


def ensure_channel_deps(workspace_dir: str) -> None:
    """Check Python dependencies for enabled IM channels and auto pip install any that are missing."""
    import importlib
    import subprocess

    from openakita.python_compat import patch_simplejson_jsondecodeerror
    from openakita.runtime_env import (
        get_channel_deps_dir,
        get_python_executable,
        inject_module_paths_runtime,
    )

    def _build_pip_env(py_path: Path) -> dict[str, str]:
        e = os.environ.copy()
        for k in (
            "PYTHONPATH",
            "PYTHONHOME",
            "PYTHONSTARTUP",
            "VIRTUAL_ENV",
            "CONDA_PREFIX",
            "CONDA_DEFAULT_ENV",
            "CONDA_SHLVL",
            "CONDA_PYTHON_EXE",
            "PIP_INDEX_URL",
            "PIP_TARGET",
            "PIP_PREFIX",
            "PIP_USER",
            "PIP_REQUIRE_VIRTUALENV",
        ):
            e.pop(k, None)
        if py_path.parent.name == "_internal":
            parts = [str(py_path.parent)]
            for sub in ("Lib", "DLLs"):
                p = py_path.parent / sub
                if p.is_dir():
                    parts.append(str(p))
            e["PYTHONPATH"] = os.pathsep.join(parts)
        return e

    def _probe_python(py: str, env: dict[str, str], extra: dict) -> tuple[bool, str]:
        try:
            p = subprocess.run(
                [py, "-c", "import encodings, pip; print('ok')"],
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                **extra,
            )
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"
        if p.returncode == 0:
            return True, ""
        return False, (p.stderr or p.stdout or "").strip()[-600:]

    def _find_offline_wheels(py_path: Path) -> Path | None:
        candidates = [
            py_path.parent.parent / "modules" / "channel-deps" / "wheels",
            py_path.parent / "modules" / "channel-deps" / "wheels",
        ]
        for c in candidates:
            if c.is_dir():
                return c
        return None

    wd = Path(workspace_dir).expanduser().resolve()

    env: dict[str, str] = {}
    env_path = wd / ".env"
    if env_path.exists():
        for line in env_path.read_bytes().decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            eq = line.find("=")
            if eq > 0:
                env[line[:eq].strip()] = line[eq + 1 :].strip()

    from openakita.channels.deps import CHANNEL_DEPS

    channel_deps = CHANNEL_DEPS

    enabled_key_map = {
        "feishu": "FEISHU_ENABLED",
        "dingtalk": "DINGTALK_ENABLED",
        "wework": "WEWORK_ENABLED",
        "wework_ws": "WEWORK_WS_ENABLED",
        "onebot": "ONEBOT_ENABLED",
        "onebot_reverse": "ONEBOT_ENABLED",
        "qqbot": "QQBOT_ENABLED",
        "wechat": "WECHAT_ENABLED",
    }

    inject_module_paths_runtime()
    patch_simplejson_jsondecodeerror()

    missing: list[str] = []
    for channel, enabled_key in enabled_key_map.items():
        if env.get(enabled_key, "").strip().lower() not in ("true", "1", "yes"):
            continue
        for import_name, pip_name in channel_deps.get(channel, []):
            try:
                importlib.import_module(import_name)
            except ImportError as exc:
                if (
                    import_name == "lark_oapi"
                    and "JSONDecodeError" in str(exc)
                    and "simplejson" in str(exc)
                ):
                    patch_simplejson_jsondecodeerror()
                    try:
                        importlib.import_module(import_name)
                        continue
                    except Exception:
                        pass
                if pip_name not in missing:
                    missing.append(pip_name)

    if not missing:
        _json_print({"status": "ok", "installed": [], "message": "All dependencies are ready"})
        return

    py = get_python_executable() or sys.executable
    py_path = Path(py)
    target_dir = get_channel_deps_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    extra: dict = {}
    if sys.platform == "win32":
        extra["creationflags"] = subprocess.CREATE_NO_WINDOW

    pip_env = _build_pip_env(py_path)
    ok, probe = _probe_python(py, pip_env, extra)
    if not ok and py_path.parent.name == "_internal":
        pip_env["PYTHONHOME"] = str(py_path.parent)
        ok, probe = _probe_python(py, pip_env, extra)
    if not ok:
        _json_print(
            {
                "status": "error",
                "installed": [],
                "missing": missing,
                "message": f"Python runtime error (cannot import encodings/pip): {probe}",
            }
        )
        return

    # Prefer offline install (when the installer ships bundled wheels)
    wheels_dir = _find_offline_wheels(py_path)
    if wheels_dir is not None:
        try:
            offline_cmd = [
                py,
                "-m",
                "pip",
                "install",
                "--no-index",
                "--find-links",
                str(wheels_dir),
                "--target",
                str(target_dir),
                "--prefer-binary",
                *missing,
            ]
            off = subprocess.run(
                offline_cmd,
                env=pip_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=240,
                **extra,
            )
            if off.returncode == 0:
                importlib.invalidate_caches()
                inject_module_paths_runtime()
                _json_print(
                    {
                        "status": "ok",
                        "installed": missing,
                        "message": f"Installed (offline): {', '.join(missing)}",
                    }
                )
                return
        except Exception:
            pass

    # Online mirror fallback
    user_index = os.environ.get("PIP_INDEX_URL", "").strip()
    mirrors: list[tuple[str, str]] = []
    if user_index:
        host = user_index.split("//")[1].split("/")[0] if "//" in user_index else ""
        mirrors.append((user_index, host))
    mirrors.extend(
        [
            ("https://mirrors.aliyun.com/pypi/simple/", "mirrors.aliyun.com"),
            ("https://pypi.tuna.tsinghua.edu.cn/simple/", "pypi.tuna.tsinghua.edu.cn"),
            ("https://pypi.org/simple/", "pypi.org"),
        ]
    )

    last_err = ""
    for index_url, trusted_host in mirrors:
        cmd = [
            py,
            "-m",
            "pip",
            "install",
            "--target",
            str(target_dir),
            "-i",
            index_url,
            "--prefer-binary",
            "--timeout",
            "60",
            *missing,
        ]
        if trusted_host:
            cmd.extend(["--trusted-host", trusted_host])
        try:
            result = subprocess.run(
                cmd,
                env=pip_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                **extra,
            )
            if result.returncode == 0:
                importlib.invalidate_caches()
                inject_module_paths_runtime()
                _json_print(
                    {
                        "status": "ok",
                        "installed": missing,
                        "message": f"Installed: {', '.join(missing)}",
                    }
                )
                return
            last_err = (result.stderr or result.stdout or "").strip()[-500:]
        except Exception as e:
            last_err = str(e)

    _json_print(
        {
            "status": "error",
            "installed": [],
            "missing": missing,
            "message": f"Installation failed: {last_err}",
        }
    )


async def feishu_onboard_start(domain: str) -> None:
    """Start the Feishu Device Flow: init handshake + begin to get device_code and QR URL."""
    from openakita.setup.feishu_onboard import FeishuOnboard

    ob = FeishuOnboard(domain=domain)
    await ob.init()
    begin_data = await ob.begin()
    result = {
        "device_code": begin_data.get("device_code", ""),
        "verification_uri": begin_data.get("verification_uri_complete", ""),
        "interval": begin_data.get("interval", 5),
        "expire_in": begin_data.get("expire_in", 600),
    }
    _json_print(result)


async def feishu_onboard_poll(domain: str, device_code: str) -> None:
    """Poll the Device Flow authorization status once."""
    from openakita.setup.feishu_onboard import FeishuOnboard

    ob = FeishuOnboard(domain=domain)
    result = await ob.poll(device_code)
    _json_print(result)


async def feishu_validate(app_id: str, app_secret: str, domain: str) -> None:
    """Validate Feishu credentials."""
    from openakita.setup.feishu_onboard import validate_credentials

    result = await validate_credentials(app_id, app_secret, domain=domain)
    _json_print(result)


async def wecom_onboard_start() -> None:
    """Generate the WeCom QR onboarding code and return auth_url + scode."""
    from openakita.setup.wecom_onboard import WecomOnboard

    ob = WecomOnboard()
    data = await ob.generate()
    result = {
        "auth_url": data.get("auth_url", ""),
        "scode": data.get("scode", ""),
    }
    _json_print(result)


async def wecom_onboard_poll(scode: str) -> None:
    """Poll the WeCom QR onboarding result once."""
    from openakita.setup.wecom_onboard import WecomOnboard

    ob = WecomOnboard()
    result = await ob.poll(scode)
    _json_print(result)


async def qqbot_onboard_start() -> None:
    """Create a QQ login session and return session_id and QR URL."""
    from openakita.setup.qqbot_onboard import QQBotOnboard

    ob = QQBotOnboard()
    try:
        result = await ob.create_session()
        _json_print(result)
    finally:
        await ob.close()


async def qqbot_onboard_poll(session_id: str) -> None:
    """Poll the QQ QR login status once."""
    from openakita.setup.qqbot_onboard import QQBotOnboard

    ob = QQBotOnboard()
    try:
        result = await ob.poll(session_id)
        _json_print(result)
    finally:
        await ob.close()


async def qqbot_onboard_create() -> None:
    """Create a QQ bot and return app_id / app_secret."""
    from openakita.setup.qqbot_onboard import QQBotOnboard

    ob = QQBotOnboard()
    try:
        result = await ob.create_bot()
        _json_print(result)
    finally:
        await ob.close()


async def qqbot_onboard_poll_and_create(session_id: str) -> None:
    """Atomic operation: poll to confirm login state and create the bot (reuses the same httpx client so cookies persist)."""
    from openakita.setup.qqbot_onboard import QQBotOnboard

    ob = QQBotOnboard()
    try:
        result = await ob.poll_and_create(session_id)
        _json_print(result)
    finally:
        await ob.close()


async def qqbot_validate(app_id: str, app_secret: str) -> None:
    """Validate QQ bot credentials."""
    from openakita.setup.qqbot_onboard import validate_credentials

    result = await validate_credentials(app_id, app_secret)
    _json_print(result)


async def wechat_onboard_start() -> None:
    """Fetch the WeChat iLink Bot login QR code and return uuid and qrcode_url."""
    from openakita.setup.wechat_onboard import WeChatOnboard

    ob = WeChatOnboard()
    try:
        result = await ob.fetch_qrcode()
        _json_print(result)
    finally:
        await ob.close()


async def wechat_onboard_poll(qrcode: str) -> None:
    """Poll the WeChat QR login status once."""
    from openakita.setup.wechat_onboard import WeChatOnboard

    ob = WeChatOnboard()
    try:
        result = await ob.poll_status(qrcode)
        _json_print(result)
    finally:
        await ob.close()


def list_skills(workspace_dir: str) -> None:
    from openakita.skills.loader import SkillLoader

    wd = Path(workspace_dir).expanduser().resolve()
    if not wd.exists() or not wd.is_dir():
        raise ValueError(f"--workspace-dir does not exist or is not a directory: {workspace_dir}")

    # External-skill enablement state (Setup Center uses this to render the "enable/disable" toggle).
    # File: <workspace>/data/skills.json
    # - Missing / no external_allowlist => all external skills enabled (legacy behavior)
    # - external_allowlist: [] => all external skills disabled
    external_allowlist: set[str] | None = None
    try:
        cfg_path = wd / "data" / "skills.json"
        if cfg_path.exists():
            raw = cfg_path.read_text(encoding="utf-8")
            cfg = json.loads(raw) if raw.strip() else {}
            al = cfg.get("external_allowlist", None)
            if isinstance(al, list):
                external_allowlist = {str(x).strip() for x in al if str(x).strip()}
    except Exception:
        external_allowlist = None

    loader = SkillLoader()
    loader.load_all(base_path=wd)
    skills = loader.registry.list_all()
    out = []
    for s in skills:
        skill_path = getattr(s, "skill_path", None)
        source_url = None
        if skill_path:
            try:
                origin_file = Path(skill_path) / ".openakita-source"
                if origin_file.exists():
                    source_url = origin_file.read_text(encoding="utf-8").strip()
            except Exception:
                pass
        sid = getattr(s, "skill_id", None) or s.name
        out.append(
            {
                "skill_id": sid,
                "name": s.name,
                "description": s.description,
                "system": bool(getattr(s, "system", False)),
                "enabled": bool(getattr(s, "system", False))
                or (external_allowlist is None)
                or (sid in external_allowlist),
                "tool_name": getattr(s, "tool_name", None),
                "category": getattr(s, "category", None),
                "path": skill_path,
                "source_url": source_url,
                "config": getattr(s, "config", None) or getattr(s, "config_schema", None),
            }
        )
    _json_print({"count": len(out), "skills": out})


def _looks_like_github_shorthand(url: str) -> bool:
    """Return True if the URL is GitHub shorthand like 'owner/repo' or 'owner/repo@skill'.

    Excludes local paths (containing backslashes, starting with '.' or '/', or a drive letter like C:).
    """
    if " " in url:
        return False
    if url.startswith((".", "/", "~")) or "\\" in url:
        return False
    if len(url) > 1 and url[1] == ":":
        return False  # Windows drive-letter path, e.g. C:\\...
    # Must contain at least one '/' separating owner/repo.
    parts = url.split("@")[0] if "@" in url else url
    return "/" in parts and len(parts.split("/")) == 2


def _sanitize_skill_dir_name(name: str) -> str:
    """Sanitize user-provided skill name into a safe directory name."""
    cleaned = (name or "").strip().replace("\\", "/").strip("/")
    if "/" in cleaned:
        cleaned = cleaned.split("/")[-1]
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", cleaned).strip("-._")
    return cleaned or "custom-skill"


def _resolve_skills_dir(workspace_dir: str) -> Path:
    """Compute the skill installation directory.

    Prefer the workspace_dir passed in from Tauri (supports multiple workspaces);
    if it's empty, use the OPENAKITA_ROOT environment variable to determine the root,
    then finally fall back to a default path.
    """
    if workspace_dir and workspace_dir.strip():
        return Path(workspace_dir).expanduser().resolve() / "skills"
    import os

    root = os.environ.get("OPENAKITA_ROOT", "").strip()
    if root:
        return Path(root) / "workspaces" / "default" / "skills"
    return Path.home() / ".openakita" / "workspaces" / "default" / "skills"


def _has_git() -> bool:
    """Check whether git is installed on the system."""
    import shutil

    return shutil.which("git") is not None


_GITHUB_ZIP_MIRRORS: list[str] = [
    "https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip",
    "https://gh-proxy.com/https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip",
    "https://mirror.ghproxy.com/https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip",
    "https://ghproxy.net/https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip",
]


def _try_platform_skill_download(skill_id: str, dest_dir: Path) -> bool:
    """Try downloading a cached skill ZIP from the OpenAkita platform.

    Returns True if successful, False otherwise.
    """
    import io
    import urllib.request
    import zipfile

    from openakita.config import settings

    hub_url = (getattr(settings, "hub_api_url", "") or "").rstrip("/")
    if not hub_url:
        return False

    url = f"{hub_url}/skills/{skill_id}/download"
    headers = {"User-Agent": "OpenAkita-SetupCenter"}
    api_key = getattr(settings, "hub_api_key", "") or ""
    if api_key:
        headers["X-Akita-Key"] = api_key

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        if len(data) < 22:
            return False
        dest_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            zf.extractall(dest_dir)
        skill_md = dest_dir / "SKILL.md"
        if skill_md.exists():
            return True
        # ZIP didn't contain SKILL.md — clean up the directory we created
        import shutil

        shutil.rmtree(str(dest_dir), ignore_errors=True)
        return False
    except Exception:
        # Clean up partially created directory on any failure
        if dest_dir.exists():
            import shutil

            shutil.rmtree(str(dest_dir), ignore_errors=True)
        return False


def _download_github_zip(repo_owner: str, repo_name: str, dest_dir: Path) -> None:
    """Download a repo ZIP via the GitHub Archive API and extract it to dest_dir (no git required).

    Automatically tries the main/master branches and falls back to domestic CDN mirrors if the direct connection fails.
    """
    import io
    import shutil
    import tempfile
    import urllib.request
    import zipfile

    data: bytes | None = None
    last_err: Exception | None = None

    for branch in ("main", "master"):
        if data is not None:
            break
        for tpl in _GITHUB_ZIP_MIRRORS:
            url = tpl.format(owner=repo_owner, repo=repo_name, branch=branch)
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "OpenAkita"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = resp.read()
                break
            except Exception as e:
                last_err = e

    if data is None:
        raise RuntimeError(
            f"Unable to download repository {repo_owner}/{repo_name}. Check your network or install Git. (Last error: {last_err})"
        )

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        _validate_zip_members(zf)
        tmp_extract = Path(tempfile.mkdtemp(prefix="openakita_zip_"))
        try:
            zf.extractall(tmp_extract)
            children = list(tmp_extract.iterdir())
            src = children[0] if len(children) == 1 and children[0].is_dir() else tmp_extract
            shutil.copytree(str(src), str(dest_dir))
        finally:
            shutil.rmtree(str(tmp_extract), ignore_errors=True)


def _validate_zip_members(zf: zipfile.ZipFile) -> None:
    """Reject ZIP archives containing path-traversal members (Zip Slip)."""
    import os

    for name in zf.namelist():
        normalized = os.path.normpath(name)
        if (
            name.startswith("/")
            or name.startswith("\\")
            or normalized.startswith("..")
            or os.path.isabs(normalized)
        ):
            raise RuntimeError(f"Zip Slip detected: dangerous member '{name}'")


def _git_clone(args: list[str]) -> None:
    """Run git clone, raising a friendly error when git is not available."""
    import subprocess

    try:
        extra: dict = {}
        if sys.platform == "win32":
            extra["creationflags"] = subprocess.CREATE_NO_WINDOW
        subprocess.run(
            args,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **extra,
        )
    except FileNotFoundError:
        raise FileNotFoundError(
            "git command not found. Install Git (https://git-scm.com) or use the GitHub shorthand format to install the skill."
        )


def _parse_github_url(url: str) -> tuple[str, str, str | None] | None:
    """Extract (owner, repo, subdir) from an HTTPS GitHub URL; return None for non-GitHub URLs.

    Delegates to the unified parser in skills.source_url, keeping the bridge interface as a plain tuple.
    """
    from openakita.skills.source_url import parse_github_source

    result = parse_github_source(url)
    if result is not None:
        return result.owner, result.repo, result.subdir
    return None


def _parse_gitee_url(url: str) -> tuple[str, str] | None:
    """Extract (owner, repo) from an HTTPS Gitee URL; return None for non-Gitee URLs."""
    import re

    m = re.match(r"https?://gitee\.com/([^/]+)/([^/.]+)", url)
    if m:
        return m.group(1), m.group(2)
    return None


def _download_gitee_zip(repo_owner: str, repo_name: str, dest_dir: Path) -> None:
    """Download a repo ZIP via the Gitee Archive API and extract it to dest_dir (no git required)."""
    import io
    import shutil
    import tempfile
    import urllib.request
    import zipfile

    data: bytes | None = None
    last_err: Exception | None = None

    for branch in ("master", "main"):
        if data is not None:
            break
        url = f"https://gitee.com/{repo_owner}/{repo_name}/repository/archive/{branch}.zip"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "OpenAkita"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
        except Exception as e:
            last_err = e

    if data is None:
        raise RuntimeError(
            f"Unable to download Gitee repository {repo_owner}/{repo_name}. Check your network. (Last error: {last_err})"
        )

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        _validate_zip_members(zf)
        tmp_extract = Path(tempfile.mkdtemp(prefix="openakita_gitee_"))
        try:
            zf.extractall(tmp_extract)
            children = list(tmp_extract.iterdir())
            src = children[0] if len(children) == 1 and children[0].is_dir() else tmp_extract
            shutil.copytree(str(src), str(dest_dir))
        finally:
            shutil.rmtree(str(tmp_extract), ignore_errors=True)


def _is_valid_skill_dir(d: Path) -> bool:
    """Return True if the directory exists and contains SKILL.md (excludes stale empty directories)."""
    return d.is_dir() and (d / "SKILL.md").exists()


def _read_skill_source(d: Path) -> str:
    """Read the install-source marker from a skill directory."""
    try:
        return (d / ".openakita-source").read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _cleanup_broken_skill_dir(d: Path) -> None:
    """Clean up a stale invalid skill directory (no SKILL.md). Raises on failure."""
    import shutil

    shutil.rmtree(d)


def _ensure_target_available(target: Path, url: str) -> None:
    """Ensure the install target directory is usable: not existing, or clean up if stale.

    - Directory doesn't exist → return immediately.
    - Directory exists but has no SKILL.md (stale) → clean up and return.
    - Directory exists with SKILL.md + same source → raise "skill already installed".
    - Directory exists with SKILL.md + different source → raise "skill directory name conflict".
    """
    if not target.exists():
        return
    if not _is_valid_skill_dir(target):
        try:
            _cleanup_broken_skill_dir(target)
        except Exception:
            raise ValueError(f"Unable to clean up stale directory; please remove it manually: {target}")
        return
    if _read_skill_source(target) == url:
        raise ValueError(f"Skill already installed: {target}")
    raise ValueError(f"Skill directory name conflict: {target}")


_CMD_PREFIXES = re.compile(
    r"^(?:npx\s+skills?\s+(?:add|install)|openakita\s+(?:install[- ]skill|skill\s+install))\s+",
    re.IGNORECASE,
)


def install_skill(workspace_dir: str, url: str) -> None:
    """Install a skill from a Git URL, GitHub shorthand, or local directory."""
    url = _CMD_PREFIXES.sub("", url.strip()).strip()
    if not url:
        raise ValueError("Please provide a valid skill address, e.g. owner/repo or a Git URL")

    skills_dir = _resolve_skills_dir(workspace_dir)
    skills_dir.mkdir(parents=True, exist_ok=True)

    if url.startswith("github:"):
        # github:user/repo/path -> clone from GitHub
        parts = url.replace("github:", "").split("/")
        if len(parts) < 2:
            raise ValueError(f"Invalid GitHub URL: {url}")
        owner, repo = parts[0], parts[1]
        skill_name = parts[-1] if len(parts) > 2 else repo
        target = skills_dir / skill_name

        _ensure_target_available(target, url)

        if _has_git():
            git_url = f"https://github.com/{owner}/{repo}.git"
            _git_clone(["git", "clone", "--depth", "1", git_url, str(target)])
        else:
            _download_github_zip(owner, repo, target)

    elif url.startswith("http://") or url.startswith("https://"):
        gh = _parse_github_url(url)
        ge = _parse_gitee_url(url)

        if gh:
            # GitHub URL (including blob/tree) — always clone using the canonical repo URL
            owner, repo, gh_subdir = gh
            skill_name = (gh_subdir or "").rsplit("/", 1)[-1] if gh_subdir else repo
            skill_name = _sanitize_skill_dir_name(skill_name)
            target = skills_dir / skill_name
            _ensure_target_available(target, url)

            if gh_subdir:
                # Has a subpath: clone to a temp directory, then extract the subdirectory.
                import shutil
                import tempfile

                tmp_parent = Path(tempfile.mkdtemp(prefix="openakita_gh_"))
                tmp_dir = tmp_parent / "repo"
                try:
                    repo_url = f"https://github.com/{owner}/{repo}.git"
                    if _has_git():
                        _git_clone(["git", "clone", "--depth", "1", repo_url, str(tmp_dir)])
                    else:
                        _download_github_zip(owner, repo, tmp_dir)
                    source_dir = tmp_dir / gh_subdir
                    if not source_dir.is_dir():
                        raise ValueError(f"Subdirectory not found in repo {owner}/{repo}: {gh_subdir}")
                    shutil.copytree(str(source_dir), str(target))
                finally:
                    shutil.rmtree(str(tmp_parent), ignore_errors=True)
            else:
                if _has_git():
                    repo_url = f"https://github.com/{owner}/{repo}.git"
                    _git_clone(["git", "clone", "--depth", "1", repo_url, str(target)])
                else:
                    _download_github_zip(owner, repo, target)
        elif ge:
            skill_name = url.rstrip("/").split("/")[-1].replace(".git", "")
            target = skills_dir / skill_name
            _ensure_target_available(target, url)
            if _has_git():
                _git_clone(["git", "clone", "--depth", "1", url, str(target)])
            else:
                _download_gitee_zip(ge[0], ge[1], target)
        else:
            skill_name = url.rstrip("/").split("/")[-1].replace(".git", "")
            target = skills_dir / skill_name
            _ensure_target_available(target, url)
            _git_clone(["git", "clone", "--depth", "1", url, str(target)])

    elif _looks_like_github_shorthand(url):
        # GitHub shorthand format: "owner/repo@skill-name" or "owner/repo"
        import shutil
        import tempfile

        if "@" in url:
            repo_part, requested_skill = url.split("@", 1)
            requested_skill = requested_skill.strip().replace("\\", "/").strip("/")
            if not requested_skill:
                requested_skill = repo_part.split("/")[-1]
        else:
            repo_part = url
            requested_skill = repo_part.split("/")[-1]

        owner, repo = repo_part.split("/", 1)
        skill_name = _sanitize_skill_dir_name(requested_skill)
        target = skills_dir / skill_name

        if target.exists():
            if _is_valid_skill_dir(target) and _read_skill_source(target) != url:
                # Same skill name from a different source: disambiguate by prepending the owner.
                skill_name = _sanitize_skill_dir_name(f"{owner}-{requested_skill}")
                target = skills_dir / skill_name
            # Whether the original path or the disambiguated one, check uniformly below.
        _ensure_target_available(target, url)

        # Strategy 1: Try platform cache first
        platform_skill_id = f"{owner}-{repo}-{skill_name}".lower().replace("/", "-")
        if _try_platform_skill_download(platform_skill_id, target):
            try:
                origin_file = target / ".openakita-source"
                origin_file.write_text(url, encoding="utf-8")
            except Exception:
                pass
            _json_print({"status": "ok", "skill_dir": str(target), "source": "platform-cache"})
            return

        # Strategy 2: git clone / ZIP download
        tmp_parent = Path(tempfile.mkdtemp(prefix="openakita_skill_"))
        tmp_dir = tmp_parent / "repo"
        try:
            if _has_git():
                repo_url = f"https://github.com/{repo_part}.git"
                _git_clone(["git", "clone", "--depth", "1", repo_url, str(tmp_dir)])
            else:
                _download_github_zip(owner, repo, tmp_dir)

            # Support a skillId that is a subpath (e.g. "skills/web-search")
            preferred_rel_paths: list[str] = []
            if requested_skill:
                preferred_rel_paths.append(requested_skill)
                if requested_skill.startswith("skills/"):
                    stripped = requested_skill[len("skills/") :]
                    if stripped:
                        preferred_rel_paths.append(stripped)
                else:
                    preferred_rel_paths.append(f"skills/{requested_skill}")
            if skill_name:
                preferred_rel_paths.extend([f"skills/{skill_name}", skill_name])

            source_dir: Path | None = None
            seen: set[str] = set()
            for rel in preferred_rel_paths:
                rel_norm = rel.replace("\\", "/").strip("/")
                if not rel_norm or rel_norm in seen:
                    continue
                seen.add(rel_norm)
                candidate = tmp_dir / rel_norm
                if candidate.is_dir():
                    source_dir = candidate
                    break

            # If no subdirectory matched, treat the whole repo as a single skill.
            source_dir = source_dir or tmp_dir
            shutil.copytree(str(source_dir), str(target))
            if source_dir == tmp_dir:
                # Clean up the .git directory produced by cloning.
                git_dir = target / ".git"
                if git_dir.exists():
                    shutil.rmtree(str(git_dir), ignore_errors=True)
        finally:
            shutil.rmtree(str(tmp_parent), ignore_errors=True)
    else:
        # Local path — copy into workspace skills directory
        src = Path(url).expanduser().resolve()
        if not src.exists():
            raise ValueError(f"Source path does not exist: {url}")
        if not src.is_dir():
            raise ValueError(f"Source path is not a directory: {url}")
        import shutil

        target = skills_dir / src.name
        _ensure_target_available(target, url)
        shutil.copytree(str(src), str(target))

    # Record install origin for marketplace matching (Issue #15)
    try:
        origin_file = target / ".openakita-source"
        origin_file.write_text(url, encoding="utf-8")
    except Exception:
        pass

    _json_print({"status": "ok", "skill_dir": str(target)})


def uninstall_skill(workspace_dir: str, skill_name: str) -> None:
    """Uninstall a skill."""
    import shutil

    skills_dir = _resolve_skills_dir(workspace_dir)
    target = (skills_dir / skill_name).resolve()

    if not target.exists():
        raise ValueError(f"Skill not found: {skill_name}")

    # Prevent path traversal: make sure the resolved path is still inside skills_dir.
    # Use relative_to instead of str.startswith to avoid prefix collisions (e.g. skills_evil/).
    try:
        target.relative_to(skills_dir.resolve())
    except ValueError:
        raise ValueError(f"Refusing to delete a skill outside the workspace: {target}")

    # Check whether it's a system skill (SKILL.md contains system: true).
    skill_md = target / "SKILL.md"
    if skill_md.exists():
        content = skill_md.read_bytes().decode("utf-8", errors="replace")
        if "system: true" in content.lower()[:500]:
            raise ValueError(f"Refusing to delete a system skill: {skill_name}")

    shutil.rmtree(str(target))
    _json_print({"status": "ok", "removed": skill_name})


def list_marketplace() -> None:
    """List skills available in the marketplace (from the registry or GitHub)."""
    # TODO: fetch from the real registry API.
    # For now, return a hard-coded sample list.
    marketplace = [
        {
            "name": "web-search",
            "description": "Web search via Serper/Google",
            "author": "openakita",
            "url": "github:openakita/skills/web-search",
            "stars": 42,
            "tags": ["search", "web"],
        },
        {
            "name": "code-interpreter",
            "description": "Python code interpreter with data analysis and visualization support",
            "author": "openakita",
            "url": "github:openakita/skills/code-interpreter",
            "stars": 38,
            "tags": ["code", "data-analysis"],
        },
        {
            "name": "browser-use",
            "description": "Browser automation for web interaction and data scraping",
            "author": "openakita",
            "url": "github:openakita/skills/browser-use",
            "stars": 25,
            "tags": ["browser", "automation"],
        },
        {
            "name": "image-gen",
            "description": "AI image generation with DALL-E / Stable Diffusion support",
            "author": "openakita",
            "url": "github:openakita/skills/image-gen",
            "stars": 19,
            "tags": ["image", "generation"],
        },
    ]
    _json_print(marketplace)


def get_skill_config(workspace_dir: str, skill_name: str) -> None:
    """Get the configuration schema for a skill."""
    from openakita.skills.loader import SkillLoader

    wd = Path(workspace_dir).expanduser().resolve()
    loader = SkillLoader()
    loader.load_all(base_path=wd)

    entry = loader.registry.get(skill_name)
    if entry is None:
        raise ValueError(f"Skill not found: {skill_name}")

    _json_print(
        {
            "name": entry.name,
            "config": entry.config or [],
        }
    )


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)

    p = argparse.ArgumentParser(prog="openakita.setup_center.bridge")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list-providers", help="List LLM providers (JSON)")

    pm = sub.add_parser("list-models", help="Fetch the model list (JSON)")
    pm.add_argument("--api-type", required=True, help="openai | anthropic")
    pm.add_argument("--base-url", required=True, help="API Base URL (for openai, usually ends with /v1)")
    pm.add_argument("--provider-slug", default="", help="Optional: used for capability inference and registry matching")

    ps = sub.add_parser("list-skills", help="List skills (JSON)")
    ps.add_argument(
        "--workspace-dir", required=True, help="Workspace directory (used to scan skills/.cursor/skills, etc.)"
    )

    ph = sub.add_parser("health-check-endpoint", help="Check LLM endpoint health (JSON)")
    ph.add_argument("--workspace-dir", required=True, help="Workspace directory")
    ph.add_argument("--endpoint-name", default="", help="Optional: only check the given endpoint (empty = all)")

    pi = sub.add_parser("health-check-im", help="Check IM channel connectivity (JSON)")
    pi.add_argument("--workspace-dir", required=True, help="Workspace directory")
    pi.add_argument("--channel", default="", help="Optional: only check the given channel id (empty = all)")

    p_ecd = sub.add_parser("ensure-channel-deps", help="Check and auto-install dependencies for enabled IM channels (JSON)")
    p_ecd.add_argument("--workspace-dir", required=True, help="Workspace directory")

    p_inst = sub.add_parser("install-skill", help="Install a skill (from URL or path)")
    p_inst.add_argument("--workspace-dir", required=True, help="Workspace directory")
    p_inst.add_argument("--url", required=True, help="Skill source URL or path")

    p_uninst = sub.add_parser("uninstall-skill", help="Uninstall a skill")
    p_uninst.add_argument("--workspace-dir", required=True, help="Workspace directory")
    p_uninst.add_argument("--skill-name", required=True, help="Skill name")

    sub.add_parser("list-marketplace", help="List marketplace skills (JSON)")

    p_cfg = sub.add_parser("get-skill-config", help="Get the skill configuration schema (JSON)")
    p_cfg.add_argument("--workspace-dir", required=True, help="Workspace directory")
    p_cfg.add_argument("--skill-name", required=True, help="Skill name")

    p_fos = sub.add_parser("feishu-onboard-start", help="Start the Feishu Device Flow QR onboarding (JSON)")
    p_fos.add_argument("--domain", default="feishu", help="feishu | lark")

    p_fop = sub.add_parser("feishu-onboard-poll", help="Poll the Feishu Device Flow authorization status (JSON)")
    p_fop.add_argument("--domain", default="feishu", help="feishu | lark")
    p_fop.add_argument("--device-code", required=True, help="The device_code returned by init")

    p_fv = sub.add_parser("feishu-validate", help="Validate Feishu credentials (JSON)")
    p_fv.add_argument("--app-id", required=True, help="Feishu App ID")
    p_fv.add_argument("--app-secret", required=True, help="Feishu App Secret")
    p_fv.add_argument("--domain", default="feishu", help="feishu | lark")

    sub.add_parser("wecom-onboard-start", help="Generate the WeCom QR onboarding code (JSON)")

    p_wop = sub.add_parser("wecom-onboard-poll", help="Poll the WeCom QR onboarding result (JSON)")
    p_wop.add_argument("--scode", required=True, help="The scode returned by generate")

    sub.add_parser("qqbot-onboard-start", help="Create a QQ login session (JSON)")

    p_qop = sub.add_parser("qqbot-onboard-poll", help="Poll the QQ QR login status (JSON)")
    p_qop.add_argument("--session-id", required=True, help="The session_id returned by create_session")

    sub.add_parser("qqbot-onboard-create", help="Create a QQ bot (JSON)")

    p_qpc = sub.add_parser("qqbot-onboard-poll-and-create", help="Atomic poll+create (JSON)")
    p_qpc.add_argument("--session-id", required=True, help="The session_id returned by create_session")

    p_qv = sub.add_parser("qqbot-validate", help="Validate QQ bot credentials (JSON)")
    p_qv.add_argument("--app-id", required=True, help="QQ bot App ID")
    p_qv.add_argument("--app-secret", required=True, help="QQ bot App Secret")

    sub.add_parser("wechat-onboard-start", help="Fetch the WeChat login QR code (JSON)")

    p_wcp = sub.add_parser("wechat-onboard-poll", help="Poll the WeChat QR login status (JSON)")
    p_wcp.add_argument("--qrcode", required=True, help="The qrcode returned by get_bot_qrcode")

    args = p.parse_args(argv)

    if args.cmd == "list-providers":
        list_providers()
        return

    if args.cmd == "list-models":
        api_key = os.environ.get("SETUPCENTER_API_KEY", "")
        asyncio.run(
            list_models(
                api_type=args.api_type,
                base_url=args.base_url,
                provider_slug=(args.provider_slug.strip() or None),
                api_key=api_key,
            )
        )
        return

    if args.cmd == "list-skills":
        list_skills(args.workspace_dir)
        return

    if args.cmd == "health-check-endpoint":
        asyncio.run(
            health_check_endpoint(
                workspace_dir=args.workspace_dir,
                endpoint_name=(args.endpoint_name.strip() or None),
            )
        )
        return

    if args.cmd == "health-check-im":
        asyncio.run(
            health_check_im(
                workspace_dir=args.workspace_dir,
                channel=(args.channel.strip() or None),
            )
        )
        return

    if args.cmd == "ensure-channel-deps":
        ensure_channel_deps(workspace_dir=args.workspace_dir)
        return

    if args.cmd == "install-skill":
        install_skill(workspace_dir=args.workspace_dir, url=args.url)
        return

    if args.cmd == "uninstall-skill":
        uninstall_skill(workspace_dir=args.workspace_dir, skill_name=args.skill_name)
        return

    if args.cmd == "list-marketplace":
        list_marketplace()
        return

    if args.cmd == "get-skill-config":
        get_skill_config(workspace_dir=args.workspace_dir, skill_name=args.skill_name)
        return

    if args.cmd == "feishu-onboard-start":
        asyncio.run(feishu_onboard_start(domain=args.domain))
        return

    if args.cmd == "feishu-onboard-poll":
        asyncio.run(feishu_onboard_poll(domain=args.domain, device_code=args.device_code))
        return

    if args.cmd == "feishu-validate":
        asyncio.run(
            feishu_validate(
                app_id=args.app_id,
                app_secret=args.app_secret,
                domain=args.domain,
            )
        )
        return

    if args.cmd == "wecom-onboard-start":
        asyncio.run(wecom_onboard_start())
        return

    if args.cmd == "wecom-onboard-poll":
        asyncio.run(wecom_onboard_poll(scode=args.scode))
        return

    if args.cmd == "qqbot-onboard-start":
        asyncio.run(qqbot_onboard_start())
        return

    if args.cmd == "qqbot-onboard-poll":
        asyncio.run(qqbot_onboard_poll(session_id=args.session_id))
        return

    if args.cmd == "qqbot-onboard-create":
        asyncio.run(qqbot_onboard_create())
        return

    if args.cmd == "qqbot-onboard-poll-and-create":
        asyncio.run(qqbot_onboard_poll_and_create(session_id=args.session_id))
        return

    if args.cmd == "qqbot-validate":
        asyncio.run(
            qqbot_validate(
                app_id=args.app_id,
                app_secret=args.app_secret,
            )
        )
        return

    if args.cmd == "wechat-onboard-start":
        asyncio.run(wechat_onboard_start())
        return

    if args.cmd == "wechat-onboard-poll":
        asyncio.run(wechat_onboard_poll(qrcode=args.qrcode))
        return

    raise SystemExit(2)


if __name__ == "__main__":
    from openakita.runtime_env import IS_FROZEN, ensure_ssl_certs, inject_module_paths

    if IS_FROZEN:
        ensure_ssl_certs()
        inject_module_paths()

    try:
        main()
    except Exception as e:
        sys.stderr.write(str(e))
        sys.stderr.write("\n")
        raise
