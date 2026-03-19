"""
Config routes: workspace info, env read/write, endpoints read/write, skills config.

These endpoints mirror the Tauri commands (workspace_read_file, workspace_update_env,
workspace_write_file) but exposed via HTTP so the desktop app can operate in "remote mode"
when connected to an already-running serve instance.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── Helpers ───────────────────────────────────────────────────────────


def _project_root() -> Path:
    """Return the project root (settings.project_root or cwd)."""
    try:
        from openakita.config import settings
        return Path(settings.project_root)
    except Exception:
        return Path.cwd()


def _parse_env(content: str) -> dict[str, str]:
    """Parse .env file content into a dict (same logic as Tauri bridge)."""
    # Strip UTF-8 BOM if present (e.g. files saved by Windows Notepad)
    if content.startswith("\ufeff"):
        content = content[1:]
    env: dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip surrounding quotes; unescape only \" and \\ (produced by _quote_env_value)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            inner = value[1:-1]
            if "\\" in inner:
                # Only unescape sequences produced by our own writer
                inner = inner.replace("\\\\", "\x00").replace('\\"', '"').replace("\x00", "\\")
            value = inner
        else:
            # Unquoted: strip inline comment (# preceded by whitespace)
            for sep in (" #", "\t#"):
                idx = value.find(sep)
                if idx != -1:
                    value = value[:idx].rstrip()
                    break
        env[key] = value
    return env


def _needs_quoting(value: str) -> bool:
    """Check whether a .env value must be quoted to survive round-trip parsing."""
    if not value:
        return False
    if value[0] in (" ", "\t") or value[-1] in (" ", "\t"):
        return True  # leading/trailing whitespace
    if value[0] in ('"', "'"):
        return True  # starts with a quote char
    for ch in (' ', '#', '"', "'", '\\'):
        if ch in value:
            return True
    return False


def _quote_env_value(value: str) -> str:
    """Quote a .env value only when it contains characters that would be
    mangled by typical .env parsers.  Plain values (the vast majority of
    API keys, URLs, flags) are written unquoted for maximum compatibility
    with older OpenAkita versions and third-party .env tooling."""
    if not _needs_quoting(value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _update_env_content(
    existing: str,
    entries: dict[str, str],
    delete_keys: set[str] | None = None,
) -> str:
    """Merge entries into existing .env content (preserves comments, order).

    - Non-empty values are written (quoted for round-trip safety).
    - Empty string values are **ignored** (original line preserved).
    - Keys in *delete_keys* are explicitly removed.
    """
    delete_keys = delete_keys or set()
    lines = existing.splitlines()
    updated_keys: set[str] = set()
    new_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue
        if "=" not in stripped:
            new_lines.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in delete_keys:
            updated_keys.add(key)
            continue  # explicit delete — skip line
        if key in entries:
            value = entries[key]
            if value == "":
                # Empty value → preserve the existing line (do NOT delete)
                new_lines.append(line)
            else:
                new_lines.append(f"{key}={_quote_env_value(value)}")
            updated_keys.add(key)
        else:
            new_lines.append(line)

    # Append new keys that weren't in the existing content
    for key, value in entries.items():
        if key not in updated_keys and value != "":
            new_lines.append(f"{key}={_quote_env_value(value)}")

    return "\n".join(new_lines) + "\n"


# ─── Pydantic models ──────────────────────────────────────────────────


class EnvUpdateRequest(BaseModel):
    entries: dict[str, str]
    delete_keys: list[str] = []


class EndpointsWriteRequest(BaseModel):
    content: dict  # Full JSON content of llm_endpoints.json


class SkillsWriteRequest(BaseModel):
    content: dict  # Full JSON content of skills.json


class DisabledViewsRequest(BaseModel):
    views: list[str]  # e.g. ["skills", "im", "token_stats"]


class AgentModeRequest(BaseModel):
    enabled: bool


class ListModelsRequest(BaseModel):
    api_type: str  # "openai" | "anthropic"
    base_url: str
    provider_slug: str | None = None
    api_key: str


# ─── Routes ────────────────────────────────────────────────────────────


@router.get("/api/config/workspace-info")
async def workspace_info():
    """Return current workspace path and basic info."""
    root = _project_root()
    return {
        "workspace_path": str(root),
        "workspace_name": root.name,
        "env_exists": (root / ".env").exists(),
        "endpoints_exists": (root / "data" / "llm_endpoints.json").exists(),
    }


@router.get("/api/config/env")
async def read_env():
    """Read .env file content as key-value pairs."""
    env_path = _project_root() / ".env"
    if not env_path.exists():
        return {"env": {}, "raw": ""}
    content = env_path.read_bytes().decode("utf-8", errors="replace")
    env = _parse_env(content)
    # Mask sensitive values for display (keys containing TOKEN, SECRET, PASSWORD, KEY)
    masked = {}
    sensitive_pattern = re.compile(r"(TOKEN|SECRET|PASSWORD|KEY|APIKEY)", re.IGNORECASE)
    for k, v in env.items():
        if sensitive_pattern.search(k) and v:
            masked[k] = v[:4] + "***" + v[-2:] if len(v) > 6 else "***"
        else:
            masked[k] = v
    return {"env": masked, "masked": masked, "raw": ""}


@router.post("/api/config/env")
async def write_env(body: EnvUpdateRequest):
    """Update .env file with key-value entries (merge, preserving comments).

    - Non-empty values are upserted.
    - Empty string values are ignored (original value preserved).
    - Keys listed in ``delete_keys`` are explicitly removed.
    """
    env_path = _project_root() / ".env"
    existing = ""
    if env_path.exists():
        existing = env_path.read_bytes().decode("utf-8", errors="replace")
    import re as _re
    _env_key_pattern = _re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
    for key in body.entries:
        if not _env_key_pattern.match(key):
            from fastapi import HTTPException as _HE
            raise _HE(status_code=400, detail=f"Invalid env key: {key}")

    new_content = _update_env_content(
        existing, body.entries, delete_keys=set(body.delete_keys)
    )
    env_path.write_text(new_content, encoding="utf-8")
    for key, value in body.entries.items():
        if value:
            os.environ[key] = value
    for key in body.delete_keys:
        os.environ.pop(key, None)
    count = len([v for v in body.entries.values() if v]) + len(body.delete_keys)
    logger.info(f"[Config API] Updated .env with {count} entries")
    return {"status": "ok", "updated_keys": list(body.entries.keys())}


@router.get("/api/config/endpoints")
async def read_endpoints():
    """Read data/llm_endpoints.json."""
    ep_path = _project_root() / "data" / "llm_endpoints.json"
    if not ep_path.exists():
        return {"endpoints": [], "raw": {}}
    try:
        data = json.loads(ep_path.read_text(encoding="utf-8"))
        return {"endpoints": data.get("endpoints", []), "raw": data}
    except Exception as e:
        return {"error": str(e), "endpoints": [], "raw": {}}


@router.post("/api/config/endpoints")
async def write_endpoints(body: EndpointsWriteRequest):
    """Write data/llm_endpoints.json."""
    ep_path = _project_root() / "data" / "llm_endpoints.json"
    ep_path.parent.mkdir(parents=True, exist_ok=True)
    ep_path.write_text(
        json.dumps(body.content, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("[Config API] Updated llm_endpoints.json")
    return {"status": "ok"}


def _get_endpoint_manager():
    """Get or create the EndpointManager singleton for the current workspace."""
    from openakita.llm.endpoint_manager import EndpointManager
    root = _project_root()
    _mgr = getattr(_get_endpoint_manager, "_instance", None)
    if _mgr is None or _mgr._ws_dir != root:
        _mgr = EndpointManager(root)
        _get_endpoint_manager._instance = _mgr
    return _mgr


class SaveEndpointRequest(BaseModel):
    endpoint: dict
    api_key: str | None = None
    endpoint_type: str = "endpoints"
    expected_version: str | None = None


class DeleteEndpointRequest(BaseModel):
    endpoint_type: str = "endpoints"
    clean_env: bool = True


@router.post("/api/config/save-endpoint")
async def save_endpoint(body: SaveEndpointRequest, request: Request):
    """Save or update an LLM endpoint atomically.

    Writes the API key to .env and the endpoint config to llm_endpoints.json
    in a single coordinated operation. Then triggers hot-reload.
    """
    from openakita.llm.endpoint_manager import ConflictError

    mgr = _get_endpoint_manager()
    try:
        result = mgr.save_endpoint(
            endpoint=body.endpoint,
            api_key=body.api_key,
            endpoint_type=body.endpoint_type,
            expected_version=body.expected_version,
        )
    except ConflictError as e:
        return {"status": "conflict", "error": str(e), "current_version": e.current_version}
    except (ValueError, Exception) as e:
        logger.error("[Config API] save-endpoint failed: %s", e, exc_info=True)
        return {"status": "error", "error": str(e)}

    # Auto-reload running clients
    _trigger_reload(request)

    return {
        "status": "ok",
        "endpoint": result,
        "version": mgr.get_version(),
    }


@router.delete("/api/config/endpoint/{name}")
async def delete_endpoint_by_name(
    name: str, request: Request, endpoint_type: str = "endpoints", clean_env: bool = True
):
    """Delete an LLM endpoint by name. Cleans up the .env key if no longer used."""
    mgr = _get_endpoint_manager()
    removed = mgr.delete_endpoint(name, endpoint_type=endpoint_type, clean_env=clean_env)
    if removed is None:
        return {"status": "not_found", "name": name}

    _trigger_reload(request)
    return {"status": "ok", "removed": removed, "version": mgr.get_version()}


@router.get("/api/config/endpoint-status")
async def endpoint_status():
    """Return key presence status for all configured endpoints."""
    mgr = _get_endpoint_manager()
    return {"endpoints": mgr.get_endpoint_status()}


def _trigger_reload(request: Request) -> bool:
    """Trigger hot-reload of LLM clients after config change."""
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        return False
    brain = getattr(agent, "brain", None) or getattr(agent, "_local_agent", None)
    if brain and hasattr(brain, "brain"):
        brain = brain.brain
    llm_client = getattr(brain, "_llm_client", None) if brain else None
    if llm_client is None:
        llm_client = getattr(agent, "_llm_client", None)
    if llm_client is None:
        return False
    try:
        llm_client.reload()
        if brain and hasattr(brain, "reload_compiler_client"):
            brain.reload_compiler_client()
        gateway = getattr(request.app.state, "gateway", None)
        if gateway and hasattr(gateway, "stt_client") and gateway.stt_client:
            from openakita.llm.config import load_endpoints_config
            _, _, stt_eps, _ = load_endpoints_config()
            gateway.stt_client.reload(stt_eps)
        logger.info("[Config API] Hot-reload triggered after config change")
        return True
    except Exception as e:
        logger.error("[Config API] Hot-reload failed: %s", e, exc_info=True)
        return False


@router.post("/api/config/reload")
async def reload_config(request: Request):
    """Hot-reload LLM endpoints config from disk into the running agent.

    This should be called after writing llm_endpoints.json so the running
    service picks up changes without a full restart.
    """
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        return {"status": "ok", "reloaded": False, "reason": "agent not initialized"}

    # Navigate: agent → brain → _llm_client
    brain = getattr(agent, "brain", None) or getattr(agent, "_local_agent", None)
    if brain and hasattr(brain, "brain"):
        brain = brain.brain  # agent wrapper → actual agent → brain
    llm_client = getattr(brain, "_llm_client", None) if brain else None
    if llm_client is None:
        # Try direct attribute on agent
        llm_client = getattr(agent, "_llm_client", None)

    if llm_client is None:
        return {"status": "ok", "reloaded": False, "reason": "llm_client not found"}

    try:
        success = llm_client.reload()

        # 同时刷新编译端点（Brain 对象上的 compiler_client）
        compiler_reloaded = False
        brain_obj = brain  # 上面已经解析过的 brain 对象
        if brain_obj and hasattr(brain_obj, "reload_compiler_client"):
            compiler_reloaded = brain_obj.reload_compiler_client()

        # 同时刷新 STT 端点（Gateway 上的 stt_client）
        stt_reloaded = False
        gateway = getattr(request.app.state, "gateway", None)
        if gateway and hasattr(gateway, "stt_client") and gateway.stt_client:
            try:
                from openakita.llm.config import load_endpoints_config
                _, _, stt_eps, _ = load_endpoints_config()
                gateway.stt_client.reload(stt_eps)
                stt_reloaded = True
            except Exception as stt_err:
                logger.warning(f"[Config API] STT reload failed: {stt_err}")

        if success:
            logger.info("[Config API] LLM endpoints reloaded successfully")
            return {
                "status": "ok",
                "reloaded": True,
                "endpoints": len(llm_client.endpoints),
                "compiler_reloaded": compiler_reloaded,
                "stt_reloaded": stt_reloaded,
            }
        else:
            return {"status": "ok", "reloaded": False, "reason": "reload returned false"}
    except Exception as e:
        logger.error(f"[Config API] Reload failed: {e}", exc_info=True)
        return {"status": "error", "reloaded": False, "reason": str(e)}


@router.post("/api/config/restart")
async def restart_service(request: Request):
    """触发服务优雅重启。

    流程：设置重启标志 → 触发 shutdown_event → serve() 主循环检测标志后重新初始化。
    前端应在调用后轮询 /api/health 直到服务恢复。
    """
    from openakita import config as cfg

    cfg._restart_requested = True
    shutdown_event = getattr(request.app.state, "shutdown_event", None)
    if shutdown_event is not None:
        logger.info("[Config API] Restart requested, triggering graceful shutdown for restart")
        shutdown_event.set()
        return {"status": "restarting"}
    else:
        logger.warning("[Config API] Restart requested but no shutdown_event available")
        cfg._restart_requested = False
        return {"status": "error", "message": "restart not available in this mode"}


@router.get("/api/config/skills")
async def read_skills_config():
    """Read data/skills.json (skill selection/allowlist)."""
    sk_path = _project_root() / "data" / "skills.json"
    if not sk_path.exists():
        return {"skills": {}}
    try:
        data = json.loads(sk_path.read_text(encoding="utf-8"))
        return {"skills": data}
    except Exception as e:
        return {"error": str(e), "skills": {}}


@router.post("/api/config/skills")
async def write_skills_config(body: SkillsWriteRequest):
    """Write data/skills.json."""
    sk_path = _project_root() / "data" / "skills.json"
    sk_path.parent.mkdir(parents=True, exist_ok=True)
    sk_path.write_text(
        json.dumps(body.content, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("[Config API] Updated skills.json")
    return {"status": "ok"}


@router.get("/api/config/disabled-views")
async def read_disabled_views():
    """Read the list of disabled module views."""
    dv_path = _project_root() / "data" / "disabled_views.json"
    if not dv_path.exists():
        return {"disabled_views": []}
    try:
        data = json.loads(dv_path.read_text(encoding="utf-8"))
        return {"disabled_views": data.get("disabled_views", [])}
    except Exception as e:
        return {"error": str(e), "disabled_views": []}


@router.post("/api/config/disabled-views")
async def write_disabled_views(body: DisabledViewsRequest):
    """Update the list of disabled module views."""
    dv_path = _project_root() / "data" / "disabled_views.json"
    dv_path.parent.mkdir(parents=True, exist_ok=True)
    dv_path.write_text(
        json.dumps({"disabled_views": body.views}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info(f"[Config API] Updated disabled_views: {body.views}")
    return {"status": "ok", "disabled_views": body.views}


@router.get("/api/config/agent-mode")
async def read_agent_mode():
    """返回多Agent模式开关状态"""
    from openakita.config import settings

    return {"multi_agent_enabled": settings.multi_agent_enabled}


def _hot_patch_agent_tools(request: Request, *, enable: bool) -> None:
    """Dynamically register / unregister multi-agent tools on the live global Agent."""
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        return
    try:
        from openakita.tools.definitions.agent import AGENT_TOOLS
        from openakita.tools.definitions.org_setup import ORG_SETUP_TOOLS
        from openakita.tools.handlers.agent import create_handler as create_agent_handler
        from openakita.tools.handlers.org_setup import create_handler as create_org_setup_handler

        all_tools = AGENT_TOOLS + ORG_SETUP_TOOLS
        tool_names = [t["name"] for t in all_tools]

        if enable:
            existing = {t["name"] for t in agent._tools}
            for t in all_tools:
                if t["name"] not in existing:
                    agent._tools.append(t)
                agent.tool_catalog.add_tool(t)
            agent.handler_registry.register(
                "agent", create_agent_handler(agent),
                [t["name"] for t in AGENT_TOOLS],
            )
            agent.handler_registry.register(
                "org_setup", create_org_setup_handler(agent),
                [t["name"] for t in ORG_SETUP_TOOLS],
            )
            logger.info("[Config API] Agent + org_setup tools hot-patched onto global agent")
        else:
            agent._tools = [t for t in agent._tools if t["name"] not in set(tool_names)]
            for name in tool_names:
                agent.tool_catalog.remove_tool(name)
            agent.handler_registry.unregister("agent")
            try:
                agent.handler_registry.unregister("org_setup")
            except Exception:
                pass
            logger.info("[Config API] Agent + org_setup tools removed from global agent")
    except Exception as e:
        logger.warning(f"[Config API] Failed to hot-patch agent tools: {e}")


@router.post("/api/config/agent-mode")
async def write_agent_mode(body: AgentModeRequest, request: Request):
    """切换多Agent模式（Beta）。修改立即生效并持久化。"""
    from openakita.config import runtime_state, settings

    old = settings.multi_agent_enabled
    settings.multi_agent_enabled = body.enabled
    runtime_state.save()
    logger.info(
        f"[Config API] multi_agent_enabled: {old} -> {body.enabled}"
    )

    if body.enabled and not old:
        try:
            from openakita.main import _init_orchestrator
            await _init_orchestrator()
            from openakita.main import _orchestrator
            if _orchestrator is not None:
                request.app.state.orchestrator = _orchestrator
                logger.info("[Config API] Orchestrator initialized and bound to app.state")
        except Exception as e:
            logger.warning(f"[Config API] Failed to init orchestrator on mode switch: {e}")
        try:
            from openakita.agents.presets import ensure_presets_on_mode_enable
            ensure_presets_on_mode_enable(settings.data_dir / "agents")
        except Exception as e:
            logger.warning(f"[Config API] Failed to deploy presets: {e}")

        _hot_patch_agent_tools(request, enable=True)

    elif not body.enabled and old:
        _hot_patch_agent_tools(request, enable=False)

    # 通知 pool 刷新版本号，旧会话的 Agent 下次请求时自动重建
    pool = getattr(request.app.state, "agent_pool", None)
    if pool is not None:
        pool.notify_skills_changed()

    return {"status": "ok", "multi_agent_enabled": body.enabled}


@router.get("/api/config/providers")
async def list_providers_api():
    """返回后端已注册的 LLM 服务商列表。

    前端可在后端运行时通过此 API 获取最新的 provider 列表，
    确保前后端数据一致。
    """
    try:
        from openakita.llm.registries import list_providers

        providers = list_providers()
        return {
            "providers": [
                {
                    "name": p.name,
                    "slug": p.slug,
                    "api_type": p.api_type,
                    "default_base_url": p.default_base_url,
                    "api_key_env_suggestion": getattr(p, "api_key_env_suggestion", ""),
                    "supports_model_list": getattr(p, "supports_model_list", True),
                    "supports_capability_api": getattr(p, "supports_capability_api", False),
                    "requires_api_key": getattr(p, "requires_api_key", True),
                    "is_local": getattr(p, "is_local", False),
                    "coding_plan_base_url": getattr(p, "coding_plan_base_url", None),
                    "coding_plan_api_type": getattr(p, "coding_plan_api_type", None),
                    "note": getattr(p, "note", None),
                }
                for p in providers
            ]
        }
    except Exception as e:
        logger.error(f"[Config API] list-providers failed: {e}")
        return {"providers": [], "error": str(e)}


@router.post("/api/config/list-models")
async def list_models_api(body: ListModelsRequest):
    """拉取 LLM 端点的模型列表（远程模式替代 Tauri openakita_list_models 命令）。

    直接复用 bridge.list_models 的逻辑，在后端进程内异步调用，无需 subprocess。
    """
    try:
        from openakita.setup_center.bridge import (
            _list_models_anthropic,
            _list_models_openai,
        )

        api_type = (body.api_type or "").strip().lower()
        base_url = (body.base_url or "").strip()
        api_key = (body.api_key or "").strip()
        provider_slug = (body.provider_slug or "").strip() or None

        if not api_type:
            return {"error": "api_type 不能为空", "models": []}
        if not base_url:
            return {"error": "base_url 不能为空", "models": []}
        # 本地服务商（Ollama/LM Studio 等）不需要 API Key，允许空值
        if not api_key:
            api_key = "local"  # placeholder for local providers

        if api_type in ("openai", "openai_responses"):
            models = await _list_models_openai(api_key, base_url, provider_slug)
        elif api_type == "anthropic":
            models = await _list_models_anthropic(api_key, base_url, provider_slug)
        else:
            return {"error": f"不支持的 api_type: {api_type}", "models": []}

        return {"models": models}
    except Exception as e:
        logger.error(f"[Config API] list-models failed: {e}", exc_info=True)
        # 将原始 Python 异常转为用户友好的提示
        raw = str(e).lower()
        friendly = str(e)
        if "errno 2" in raw or "no such file" in raw:
            friendly = "SSL 证书文件缺失，请重新安装或更新应用"
        elif "connect" in raw or "connection refused" in raw or "no route" in raw or "unreachable" in raw:
            friendly = "无法连接到服务商，请检查 API 地址和网络连接"
            try:
                from openakita.llm.providers.proxy_utils import format_proxy_hint

                hint = format_proxy_hint()
                if hint:
                    friendly += hint
            except Exception:
                pass
        elif "401" in raw or "unauthorized" in raw or "invalid api key" in raw or "authentication" in raw:
            friendly = "API Key 无效或已过期，请检查后重试"
        elif "403" in raw or "forbidden" in raw or "permission" in raw:
            friendly = "API Key 权限不足，请确认已开通模型访问权限"
        elif "404" in raw or "not found" in raw:
            friendly = "该服务商不支持模型列表查询，您可以手动输入模型名称"
        elif "timeout" in raw or "timed out" in raw:
            friendly = "请求超时，请检查网络或稍后重试"
        elif len(friendly) > 150:
            friendly = friendly[:150] + "…"
        return {"error": friendly, "models": []}
