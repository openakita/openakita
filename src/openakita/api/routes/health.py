"""
Health check routes: GET /api/health, POST /api/health/check

POST /api/health/check 使用 dry_run=True 模式执行只读检测，
不会修改 provider 的健康状态和冷静期计数，避免干扰正在运行的 Agent。
"""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, Request

from ..schemas import HealthCheckRequest, HealthResult

logger = logging.getLogger(__name__)

router = APIRouter()


_lan_ip_cache: tuple[str, float] | None = None
_LAN_IP_TTL = 60


def _get_lan_ip() -> str:
    """Best-effort LAN IP detection via UDP connect (no traffic sent).

    Result is cached for 60s to avoid creating a socket on every health check
    (heartbeat polls every 5s).
    """
    global _lan_ip_cache
    now = time.time()
    if _lan_ip_cache and (now - _lan_ip_cache[1]) < _LAN_IP_TTL:
        return _lan_ip_cache[0]

    import socket

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        ip = "127.0.0.1"
    _lan_ip_cache = (ip, now)
    return ip


def _safe_int(val: str, default: int) -> int:
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


_VIRTUAL_PREFIXES = (
    "26.",       # Radmin VPN
    "25.",       # Hamachi
    "100.64.",   # CGNAT / Tailscale
    "172.17.",   # Docker default bridge
    "172.18.",   # Docker user-defined
    "172.19.",   # Docker user-defined
)


def _ip_score(ip: str) -> int:
    """Higher score = more likely to be the real LAN IP the user wants.

    - Virtual adapter prefixes (VPN, Docker, etc.)  → 0
    - Ends in .1 in private range (likely VM host / bridge)  → 1
    - 172.16-31.x.x (Hyper-V, Docker host range)   → 2
    - 10.x.x.x (often corporate/real but also VPN)  → 3
    - 192.168.x.x with DHCP-like last octet         → 4  (best guess)
    """
    for prefix in _VIRTUAL_PREFIXES:
        if ip.startswith(prefix):
            return 0

    octets = ip.split(".")
    last = int(octets[3]) if len(octets) == 4 else 0
    second = int(octets[1]) if len(octets) >= 2 else 0

    if ip.startswith("192.168."):
        return 2 if last == 1 else 4
    if ip.startswith("10."):
        return 2 if last == 1 else 3
    if ip.startswith("172.") and 16 <= second <= 31:
        return 1 if last == 1 else 2
    return 1


_all_ips_cache: tuple[list[str], float] | None = None


def _get_all_lan_ips() -> list[str]:
    """Return all non-loopback IPv4 addresses, sorted by likelihood of being
    the real LAN IP (highest score first). Cached 60s."""
    global _all_ips_cache
    now = time.time()
    if _all_ips_cache and (now - _all_ips_cache[1]) < _LAN_IP_TTL:
        return _all_ips_cache[0]

    import socket

    raw: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addr = info[4][0]
            if addr.startswith("127.") or addr.startswith("169.254."):
                continue
            if addr not in raw:
                raw.append(addr)
    except Exception:
        pass

    primary = _get_lan_ip()
    if primary not in raw and primary != "127.0.0.1":
        raw.append(primary)

    ordered = sorted(raw, key=_ip_score, reverse=True)

    _all_ips_cache = (ordered, now)
    return ordered


@router.get("/api/health")
async def health(request: Request):
    """Basic health check - returns 200 if the HTTP API is reachable.

    注意：HTTP API 可访问不等于整个后端业务已完成启动。IM 通道、晚绑定
    gateway、后台任务可能在 HTTP 之后继续初始化。因此这里同时返回
    ``readiness``，前端应使用 ``readiness.ready`` / ``readiness.phase`` 展示
    "启动中 / 部分就绪 / 运行中"，而不是只看 HTTP 200。
    """
    import os

    from openakita import __git_hash__, get_version_string
    from openakita import __version__ as backend_version

    readiness = getattr(request.app.state, "readiness", None)
    if not isinstance(readiness, dict):
        gateway = getattr(request.app.state, "gateway", None)
        readiness = {
            "phase": getattr(request.app.state, "startup_phase", "http_ready"),
            "http_ready": True,
            "im_ready": gateway is not None,
            "ready": bool(gateway is not None),
        }

    return {
        "status": "ok",
        "service": "openakita",
        "version": backend_version,
        "git_hash": __git_hash__,
        "version_full": get_version_string(),
        "pid": os.getpid(),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "agent_initialized": hasattr(request.app.state, "agent")
        and request.app.state.agent is not None,
        "local_ip": _get_lan_ip(),
        "all_ips": _get_all_lan_ips(),
        "api_host": os.environ.get("API_HOST", "127.0.0.1"),
        "api_port": _safe_int(os.environ.get("API_PORT", "18900"), 18900),
        "last_link_diagnostic": getattr(request.app.state, "last_link_diagnostic", None),
        "startup_phase": readiness.get("phase", "http_ready"),
        "readiness": readiness,
    }


@router.get("/api/logs/health-summary")
async def logs_health_summary():
    """Aggregate repeated background warnings into a UI-friendly summary."""
    from openakita.core.log_health import get_log_health_registry

    return get_log_health_registry().summary()


@router.get("/api/diagnostics/last-link")
async def last_link_diagnostic(request: Request):
    """Return the last web_fetch / browser link diagnostic for the Status panel."""
    return getattr(request.app.state, "last_link_diagnostic", None) or {}


@router.post("/api/diagnostics/clear-session-caches")
async def clear_session_caches_endpoint(request: Request, conversation_id: str | None = None):
    """User-triggered, non-destructive cache clear for the active session.

    Clears: WebFetch URL cache, ReasoningEngine read-only tool cache, browser
    navigation memory, last link diagnostic, per-conversation compression
    summaries.
    """
    from openakita.core.session_caches import clear_session_caches

    actual_agent = None
    try:
        from .chat import _get_existing_agent, _resolve_agent

        agent = _get_existing_agent(request, conversation_id or "") or getattr(
            request.app.state, "agent", None
        )
        actual_agent = _resolve_agent(agent) if agent else None
    except Exception:
        pass

    cleared = clear_session_caches(actual_agent)
    return {"ok": True, "cleared": cleared}


@router.get("/api/diagnostics/domain-rules")
async def domain_rules(conversation_id: str = ""):
    """Return blocked / approved hosts for a conversation."""
    from openakita.core.domain_allowlist import get_domain_allowlist

    return {"conversation_id": conversation_id, **get_domain_allowlist().list_for(conversation_id)}


@router.post("/api/diagnostics/domain-block")
async def domain_block(conversation_id: str, host: str):
    from openakita.core.domain_allowlist import get_domain_allowlist

    if not conversation_id or not host:
        return {"ok": False, "error": "conversation_id and host are required"}
    added = get_domain_allowlist().block(conversation_id, host)
    return {"ok": True, "changed": added, **get_domain_allowlist().list_for(conversation_id)}


@router.post("/api/diagnostics/domain-unblock")
async def domain_unblock(conversation_id: str, host: str):
    from openakita.core.domain_allowlist import get_domain_allowlist

    if not conversation_id or not host:
        return {"ok": False, "error": "conversation_id and host are required"}
    removed = get_domain_allowlist().unblock(conversation_id, host)
    return {"ok": True, "changed": removed, **get_domain_allowlist().list_for(conversation_id)}


def _get_llm_client(agent: object):
    """Resolve LLMClient from Agent."""
    from openakita.core.agent import Agent

    actual = agent if isinstance(agent, Agent) else None
    if actual is None:
        return None
    brain = getattr(actual, "brain", None)
    if brain is None:
        return None
    return getattr(brain, "_llm_client", None)


def _probe_command_version(command: str, *args: str) -> dict[str, str | bool]:
    import subprocess
    import sys

    from openakita.runtime_manager import resolve_toolchain_command

    path = resolve_toolchain_command(command)
    if not path:
        return {"available": False, "path": "", "version": "unavailable"}
    kwargs: dict = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 5,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.run([path, *(args or ("--version",))], **kwargs)
    except Exception as exc:
        return {"available": False, "path": path, "version": f"probe failed: {exc}"}
    output = (proc.stdout or proc.stderr or "").strip().splitlines()
    return {
        "available": proc.returncode == 0,
        "path": path,
        "version": output[-1] if output else f"exit {proc.returncode}",
    }


async def _check_endpoint_readonly(name: str, provider) -> HealthResult:
    """Check an endpoint in dry_run mode: test connectivity without modifying provider state."""
    t0 = time.time()
    try:
        await provider.health_check(dry_run=True)
        latency = round((time.time() - t0) * 1000)
        return HealthResult(
            name=name,
            status="healthy",
            latency_ms=latency,
            last_checked_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
    except Exception as e:
        latency = round((time.time() - t0) * 1000)
        error_msg = str(e)
        raw = error_msg.lower()
        if "connect" in raw or "connection refused" in raw or "unreachable" in raw:
            try:
                from openakita.llm.providers.proxy_utils import format_proxy_hint

                hint = format_proxy_hint()
                if hint:
                    error_msg += hint
            except Exception:
                pass
        return HealthResult(
            name=name,
            status="unhealthy",
            latency_ms=latency,
            error=error_msg[:800],
            consecutive_failures=getattr(provider, "consecutive_cooldowns", 0),
            cooldown_remaining=getattr(provider, "cooldown_remaining", 0),
            is_extended_cooldown=getattr(provider, "is_extended_cooldown", False),
            last_checked_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )


async def _check_with_timeout(name: str, provider, timeout: float = 30) -> HealthResult:
    """Wrap _check_endpoint_readonly with a per-endpoint timeout."""
    try:
        return await asyncio.wait_for(
            _check_endpoint_readonly(name, provider),
            timeout=timeout,
        )
    except TimeoutError:
        return HealthResult(
            name=name,
            status="unhealthy",
            error=f"Health check timed out ({timeout}s)",
            last_checked_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )


@router.get("/api/debug/pool-stats")
async def pool_stats(request: Request):
    """Diagnostic: return AgentInstancePool statistics."""
    pool = getattr(request.app.state, "agent_pool", None)
    if pool is None:
        return {"error": "AgentInstancePool not available", "pool_enabled": False}
    stats = pool.get_stats()
    stats["pool_enabled"] = True
    return stats


@router.get("/api/debug/orchestrator-state")
async def orchestrator_state(request: Request):
    """Diagnostic: return orchestrator internal sub-agent states and active tasks."""
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        try:
            from openakita.main import _orchestrator

            orchestrator = _orchestrator
        except (ImportError, AttributeError):
            pass
    if orchestrator is None:
        return {"error": "Orchestrator not available", "enabled": False}
    return {
        "enabled": True,
        "sub_agent_states": dict(getattr(orchestrator, "_sub_agent_states", {})),
        "active_tasks": list(getattr(orchestrator, "_active_tasks", {}).keys()),
        "health_stats": {
            k: {"total": v.total_requests, "success": v.successful, "failed": v.failed}
            for k, v in getattr(orchestrator, "_health_stats", {}).items()
        },
    }


@router.get("/api/diagnostics")
async def diagnostics():
    """Self-check: the backend reports its own runtime health.

    Called by the desktop app's environment diagnostic panel instead of
    trying to invoke _internal/python3 externally.
    """
    import os
    import platform
    import sys
    from pathlib import Path

    from openakita import __version__ as backend_version
    from openakita.runtime_manager import (
        get_runtime_environment_report,
        get_workspace_dependency_cache_root,
    )

    checks: list[dict] = []

    # C1: Runtime
    runtime_type = "bundled" if getattr(sys, "frozen", False) else "venv"
    checks.append(
        {
            "id": "C1_BUNDLED_RUNTIME",
            "title": "内置运行时",
            "status": "pass",
            "code": "RUNTIME_OK",
            "evidence": [f"Python {platform.python_version()}, {runtime_type}"],
            "autoFix": False,
            "fixHint": None,
        }
    )

    # C2: pip availability
    try:
        import pip

        pip_ver = pip.__version__
        checks.append(
            {
                "id": "C2_PIP",
                "title": "包管理器",
                "status": "pass",
                "code": "PIP_OK",
                "evidence": [f"pip {pip_ver}"],
                "autoFix": False,
                "fixHint": None,
            }
        )
    except Exception:
        checks.append(
            {
                "id": "C2_PIP",
                "title": "包管理器",
                "status": "warn",
                "code": "PIP_UNAVAILABLE",
                "evidence": ["pip not importable — optional module installation disabled"],
                "autoFix": False,
                "fixHint": None,
            }
        )

    # C3: Core package integrity
    try:
        from openakita.setup_center import bridge  # noqa: F401

        checks.append(
            {
                "id": "C3_CORE",
                "title": "核心引擎",
                "status": "pass",
                "code": "CORE_OK",
                "evidence": [f"openakita {backend_version}"],
                "autoFix": False,
                "fixHint": None,
            }
        )
    except Exception as exc:
        checks.append(
            {
                "id": "C3_CORE",
                "title": "核心引擎",
                "status": "fail",
                "code": "CORE_IMPORT_ERROR",
                "evidence": [str(exc)[:300]],
                "autoFix": False,
                "fixHint": "核心模块损坏，建议重装 OpenAkita",
            }
        )

    failing = [c for c in checks if c["status"] not in ("pass", "warn")]
    summary = "broken" if failing else "healthy"

    runtime_report = get_runtime_environment_report()
    node_toolchain = {
        "managed_node": runtime_report.get("toolchain_node"),
        "managed_bin": runtime_report.get("toolchain_node_bin"),
        "node": _probe_command_version("node"),
        "npm": _probe_command_version("npm"),
        "corepack": _probe_command_version("corepack"),
        "pnpm": _probe_command_version("pnpm"),
        "yarn": _probe_command_version("yarn"),
        "npm_prefix": str(get_workspace_dependency_cache_root() / "npm-prefix"),
        "npm_cache": str(get_workspace_dependency_cache_root() / "npm-cache"),
        "corepack_home": str(get_workspace_dependency_cache_root() / "corepack"),
        "workspace_cache": str(get_workspace_dependency_cache_root()),
    }
    package_paths: dict[str, str] = {}
    for mod_name in ("openakita", "pydantic", "pydantic_core", "certifi"):
        try:
            mod = __import__(mod_name)
            package_paths[mod_name] = str(Path(getattr(mod, "__file__", "") or "").resolve())
        except Exception as exc:
            package_paths[mod_name] = f"unavailable: {type(exc).__name__}: {exc}"

    return {
        "summary": summary,
        "checks": checks,
        "environment": {
            "platform": f"{sys.platform}-{platform.machine()}",
            "pythonVersion": platform.python_version(),
            "runtimeType": runtime_type,
            "openakitaVersion": backend_version,
            "pid": os.getpid(),
            "runtime": runtime_report,
            "toolchain": {
                "python": {
                    "app": runtime_report.get("app_python"),
                    "agent": runtime_report.get("agent_python"),
                    "managed": runtime_report.get("toolchain_python"),
                    "seedPackaged": runtime_report.get("bootstrap_python_seed_packaged"),
                    "abi": runtime_report.get("python_abi"),
                    "wheelTag": runtime_report.get("wheel_tag"),
                },
                "node": {
                    **node_toolchain,
                    "seedPackaged": runtime_report.get("bootstrap_node_seed_packaged"),
                },
            },
            "sysPrefix": sys.prefix,
            "sysBasePrefix": sys.base_prefix,
            "sysPathSummary": sys.path[:20],
            "packagePaths": package_paths,
            "envTrustSource": os.environ.get("OPENAKITA_ENV_TRUST_SOURCE", ""),
            "subprocessSecretScrub": os.environ.get("OPENAKITA_SUBPROCESS_SECRET_SCRUB") == "1",
        },
    }


async def _do_health_check(request: Request, body: HealthCheckRequest):
    """共享 GET / POST 的实际探测逻辑。"""
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        return {"error": "Agent not initialized"}

    llm_client = _get_llm_client(agent)
    if llm_client is None:
        return {"error": "LLM client not available"}

    results: list[HealthResult] = []

    if body.endpoint_name:
        provider = llm_client._providers.get(body.endpoint_name)
        if not provider:
            return {"error": f"Endpoint not found: {body.endpoint_name}"}
        result = await _check_with_timeout(body.endpoint_name, provider)
        results.append(result)
    else:
        tasks = [_check_with_timeout(name, p) for name, p in llm_client._providers.items()]
        results = list(await asyncio.gather(*tasks))

    return {"results": [r.model_dump() for r in results]}


@router.post("/api/health/check")
async def health_check_post(request: Request, body: HealthCheckRequest):
    """
    Check health of a specific LLM endpoint or all endpoints (POST).

    Uses dry_run mode: sends a real test request but does NOT modify
    the provider's healthy/cooldown state, ensuring no interference
    with ongoing Agent LLM calls.
    """
    return await _do_health_check(request, body)


# PR-S1: 新增 GET 版本，便于浏览器 / curl / 监控脚本一键探测，
# 不必每次都构造 POST 请求体。endpoint_name 通过 query string 传入；
# 不带参数则探测全部端点。
@router.get("/api/health/check")
async def health_check_get(request: Request, endpoint_name: str = ""):
    """
    Check health of a specific LLM endpoint or all endpoints (GET).

    GET /api/health/check                 → 探测全部端点
    GET /api/health/check?endpoint_name=x → 探测指定端点

    与 POST /api/health/check 行为一致；前端 / 监控集成可任选其一。
    """
    body = HealthCheckRequest(endpoint_name=endpoint_name or None)
    return await _do_health_check(request, body)


@router.get("/api/health/loop")
async def health_loop(request: Request):
    """Event loop 健康状态与 LLM 并发统计。"""
    from openakita.llm.client import LLMClient

    loop = asyncio.get_running_loop()

    # 测量 event loop 延迟：调度一个 callback 看实际执行需要多久
    lag_event = asyncio.Event()
    t0 = time.monotonic()
    loop.call_soon(lag_event.set)
    await lag_event.wait()
    lag_ms = round((time.monotonic() - t0) * 1000, 1)

    llm_stats = LLMClient.get_concurrency_stats()

    org_runtime = getattr(request.app.state, "org_runtime", None)
    org_stats = {}
    if org_runtime:
        for oid, sem in org_runtime._org_semaphores.items():
            active = org_runtime.max_concurrent_nodes_per_org - sem._value
            org_stats[oid] = {
                "active_nodes": active,
                "max": org_runtime.max_concurrent_nodes_per_org,
            }

    from openakita.core.engine_bridge import is_dual_loop

    return {
        "dual_loop": is_dual_loop(),
        "api_loop_lag_ms": lag_ms,
        "llm_concurrent": llm_stats,
        "org_concurrency": org_stats,
    }
