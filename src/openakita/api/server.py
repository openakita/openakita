"""
FastAPI HTTP API server for OpenAkita.

集成在 `openakita serve` 中，提供：
- Chat (SSE streaming)
- Models list
- Health check
- Skills management
- File upload

默认端口：18900
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import openakita._ensure_utf8  # noqa: F401  # Windows UTF-8 编码保护

from .auth import WebAccessConfig, create_auth_middleware
from .middleware_setup_gate import create_setup_gate_middleware
from .routes import (
    _orgs_v2_legacy_redirects,
    agents,
    bug_report,
    chat,
    chat_models,
    config,
    diagnostics,
    feishu_onboard,
    files,
    health,
    hub,
    identity,
    im,
    inbox,
    logs,
    mcp,
    memory,
    memory_repair,
    orgs_v2,
    orgs_v2_runtime,
    orgs_v2_stream,
    pending_approvals,
    qqbot_onboard,
    scheduler,
    sessions,
    skill_categories,
    skill_stats,
    skills,
    token_stats,
    upload,
    wechat_onboard,
    wecom_onboard,
    workspace_io,
    workspaces,
)
from .routes import (
    build_info as build_info_routes,
)
from .routes import (
    web_search as web_search_routes,
)

try:
    from .routes import plugins as plugins_routes
except ImportError:
    plugins_routes = None
    logging.getLogger(__name__).debug("Plugin routes not available")
from .routes import (
    auth as auth_routes,
)
from .routes import (
    websocket as ws_routes,
)

logger = logging.getLogger(__name__)

# Default port. The actual host is decided by
# :func:`openakita.api.host_resolution.resolve_api_host` at startup and passed
# into :func:`start_api_server` explicitly. Do not import a module-level
# ``API_HOST`` constant — it was removed because it captured ``os.environ``
# at import time, which is too early (``.env`` may not be loaded yet) and
# made the resolution logic invisible to tests.
API_PORT = int(os.environ.get("API_PORT", "18900"))


def get_api_host_for_health_display(app_state: Any | None = None) -> str:
    """Best-effort answer to "which host did we bind to?" for /api/health.

    Prefers the value stored on FastAPI ``app.state.actual_bind_host`` (the
    truth, set by :func:`start_api_server` after uvicorn binds), then falls
    back to the ``API_HOST`` env var, finally to ``127.0.0.1``.
    """
    if app_state is not None:
        actual = getattr(app_state, "actual_bind_host", None)
        if isinstance(actual, str) and actual:
            return actual
    return os.environ.get("API_HOST", "").strip() or "127.0.0.1"


def is_port_free(host: str, port: int) -> bool:
    """检测端口是否可用（快速单次检测）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _resolve_force_exit_grace_s() -> int:
    """Read ``shutdown_force_exit_grace_s`` from settings with a safe fallback."""
    try:
        from openakita.config import settings

        return int(getattr(settings, "shutdown_force_exit_grace_s", 15) or 0)
    except Exception:
        return 15


def _do_force_exit(grace_s: int) -> None:
    """Last-mile body of the force-exit watchdog (mechanism-agnostic).

    Extracted so both the threading and the legacy asyncio paths share
    one place that flushes logs and pulls the plug. Tests patch
    ``openakita.api.server.os._exit`` and intercept the call here.
    """
    logger.error(
        "[Shutdown] Graceful shutdown exceeded %ss grace window; "
        "forcing os._exit(0). See _v32_biz/_phase_b_watchdog_redesign.md.",
        grace_s,
    )
    try:
        for h in logging.getLogger().handlers:
            with contextlib.suppress(Exception):
                h.flush()
    finally:
        os._exit(0)


def _arm_force_exit_watchdog_sync(app: FastAPI) -> None:
    """Sprint 15 / v32 Phase B: ``threading.Timer`` force-exit safety net.

    Why this exists — see ``_v32_biz/_phase_b_watchdog_redesign.md``.
    v31 ``_arm_force_exit_watchdog_async`` registered the watchdog with
    ``asyncio.create_task``. uvicorn's lifespan teardown cancels every
    pending asyncio task, so ``await asyncio.sleep(grace_s)`` raised
    ``CancelledError`` long before the grace window elapsed: v31 PHASEA
    runs saw 4/4 ``Force-exit safety net armed`` but 0/4 ``forcing
    os._exit(0)``.

    ``threading.Timer`` is a plain Thread that sleeps in its own OS
    timer; uvicorn's asyncio teardown has no handle on it, so the
    timer fires on schedule even after the lifespan loop is gone.

    Design notes:
      * Intentionally *NOT* cancelled when graceful shutdown succeeds.
        If the process has already exited when the timer fires, the
        callback never runs (Python has reaped the interpreter); if
        graceful is hung, the timer is the only escape hatch. The
        wasted ~15s of idle Thread sleep is cheap.
      * Daemon thread, so it never *itself* blocks process exit on the
        happy path.
      * Idempotent against the ``app.state._force_exit_task`` attribute
        the v31 code already pinned (multi-tab /api/shutdown safety).
    """
    grace_s = _resolve_force_exit_grace_s()
    if grace_s <= 0:
        logger.warning(
            "[Shutdown] Force-exit safety net disabled (grace_s=%s); "
            "graceful path must complete on its own.",
            grace_s,
        )
        return

    if getattr(app.state, "_force_exit_task", None) is not None:
        logger.debug(
            "[Shutdown] Force-exit safety net already armed; skipping duplicate"
        )
        return

    try:
        import threading

        def _fire() -> None:
            _do_force_exit(grace_s)

        timer = threading.Timer(float(grace_s), _fire)
        timer.name = "openakita-force-exit-watchdog"
        timer.daemon = True
        timer.start()
        app.state._force_exit_task = timer
        app.state._force_exit_mechanism = "threading.Timer"
        logger.info(
            "[Shutdown] Force-exit safety net armed (grace=%ss, "
            "mechanism=threading.Timer); graceful path runs first.",
            grace_s,
        )
    except Exception as exc:  # noqa: BLE001 -- never break the shutdown route
        logger.warning(
            "[Shutdown] Failed to arm threading force-exit safety net: %s", exc
        )


def _arm_force_exit_watchdog_async(app: FastAPI) -> None:
    """Legacy v31 asyncio-based watchdog. **Known broken** under uvicorn
    lifespan teardown — kept solely as a rollback path behind
    ``settings.shutdown_force_exit_use_threading=False``. Do not call
    directly from production code paths.
    """
    grace_s = _resolve_force_exit_grace_s()
    if grace_s <= 0:
        logger.warning(
            "[Shutdown] Force-exit safety net disabled (grace_s=%s); "
            "graceful path must complete on its own.",
            grace_s,
        )
        return

    if getattr(app.state, "_force_exit_task", None) is not None:
        logger.debug(
            "[Shutdown] Force-exit safety net already armed; skipping duplicate"
        )
        return

    async def _force_exit() -> None:
        try:
            await asyncio.sleep(grace_s)
        except asyncio.CancelledError:
            return
        _do_force_exit(grace_s)

    try:
        loop = asyncio.get_event_loop()
        task = loop.create_task(_force_exit(), name="openakita-force-exit-watchdog")
        app.state._force_exit_task = task
        app.state._force_exit_mechanism = "asyncio.Task"
        logger.info(
            "[Shutdown] Force-exit safety net armed (grace=%ss, "
            "mechanism=asyncio.Task [legacy]); graceful path runs first.",
            grace_s,
        )
    except Exception as exc:  # noqa: BLE001 -- never break the shutdown route
        logger.warning(
            "[Shutdown] Failed to arm asyncio force-exit safety net: %s", exc
        )


def _schedule_force_exit_after_grace(app: FastAPI) -> None:
    """Public entry: arm a last-resort ``os._exit(0)`` watchdog.

    Dispatches to ``_arm_force_exit_watchdog_sync`` (threading.Timer,
    default) or ``_arm_force_exit_watchdog_async`` (legacy asyncio) based
    on ``settings.shutdown_force_exit_use_threading``.

    Why dispatch instead of hard-coding the new mechanism: the
    rollback knob lets operators flip back to the v31 behaviour if a
    fresh threading regression ever surfaces, without redeploying.
    """
    try:
        from openakita.config import settings

        use_threading = bool(
            getattr(settings, "shutdown_force_exit_use_threading", True)
        )
    except Exception:
        use_threading = True

    if use_threading:
        _arm_force_exit_watchdog_sync(app)
    else:
        _arm_force_exit_watchdog_async(app)


def wait_for_port_free(host: str, port: int, timeout: float = 30.0) -> bool:
    """等待端口释放，返回 True 表示端口可用。

    用于重启场景下等待旧进程释放 TCP 端口（避免 TIME_WAIT 竞态）。
    """
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if is_port_free(host, port):
            return True
        time.sleep(0.5)
    return False


def _find_web_dist() -> Path | None:
    """Locate the web frontend dist directory.

    Search order:
    1. apps/setup-center/dist-web/ (development source checkout)
    2. openakita/web/ (pip wheel install & PyInstaller bundle)
    """
    # Inside the installed package
    pkg_web = Path(__file__).parent.parent / "web"

    # Development: relative to project root
    dev_web = Path(__file__).parent.parent.parent.parent / "apps" / "setup-center" / "dist-web"

    return _select_web_dist(pkg_web=pkg_web, dev_web=dev_web)


def _select_web_dist(*, pkg_web: Path, dev_web: Path) -> Path | None:
    """Choose the web frontend assets directory.

    In editable/source checkouts both directories may exist: ``pkg_web`` holds
    staged release assets, while ``dev_web`` is what ``npm run build:web``
    updates. Prefer ``dev_web`` so local backend runs do not serve stale staged
    assets after frontend changes.
    """
    if (dev_web / "index.html").exists():
        return dev_web

    if (pkg_web / "index.html").exists():
        return pkg_web

    return None


def _find_docs_dist() -> Path | None:
    """Locate bundled user docs dist directory.

    Search order:
    1. openakita/docs_dist/ (pip wheel install)
    2. docs-site/.vitepress/dist/ (development)
    """
    pkg_docs = Path(__file__).parent.parent / "docs_dist"
    if (pkg_docs / "index.html").exists():
        return pkg_docs

    dev_docs = Path(__file__).parent.parent.parent.parent / "docs-site" / ".vitepress" / "dist"
    if (dev_docs / "index.html").exists():
        return dev_docs

    return None


def _docs_version_matches_bundled(bundled: Path, version_dir: Path) -> bool:
    """Return True when the deployed docs look current enough to reuse."""
    if not version_dir.is_dir():
        return False

    try:
        bundled_files = {path.relative_to(bundled) for path in bundled.rglob("*") if path.is_file()}
        deployed_files = {
            path.relative_to(version_dir) for path in version_dir.rglob("*") if path.is_file()
        }
        if bundled_files != deployed_files:
            return False

        for relative_path in bundled_files:
            source_path = bundled / relative_path
            deployed_path = version_dir / relative_path
            if not deployed_path.is_file():
                return False
            source_stat = source_path.stat()
            deployed_stat = deployed_path.stat()
            if source_stat.st_size != deployed_stat.st_size:
                return False
            if source_stat.st_mtime_ns == deployed_stat.st_mtime_ns:
                continue
            if source_path.read_bytes() != deployed_path.read_bytes():
                return False

        for relative_name in ("index.html", "hashmap.json", "versions.html"):
            source_path = bundled / relative_name
            deployed_path = version_dir / relative_name
            if source_path.is_file():
                if not deployed_path.is_file():
                    return False
                if source_path.read_bytes() != deployed_path.read_bytes():
                    return False
    except OSError:
        return False

    return True


def _sync_docs_tree(bundled: Path, version_dir: Path) -> None:
    import shutil

    version_dir.mkdir(parents=True, exist_ok=True)
    bundled_files: set[Path] = set()
    bundled_dirs: set[Path] = set()

    for source_path in bundled.rglob("*"):
        relative_path = source_path.relative_to(bundled)
        if source_path.is_dir():
            bundled_dirs.add(relative_path)
            (version_dir / relative_path).mkdir(parents=True, exist_ok=True)
            continue
        if source_path.is_file():
            bundled_files.add(relative_path)
            deployed_path = version_dir / relative_path
            deployed_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, deployed_path)

    deployed_files = [path for path in version_dir.rglob("*") if path.is_file()]
    for deployed_path in deployed_files:
        if deployed_path.relative_to(version_dir) not in bundled_files:
            deployed_path.unlink()

    deployed_dirs = sorted(
        (path for path in version_dir.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for deployed_path in deployed_dirs:
        if deployed_path.relative_to(version_dir) not in bundled_dirs:
            deployed_path.rmdir()


def _record_docs_version(docs_root: Path, version_clean: str) -> None:
    import json

    versions_file = docs_root / "versions.json"
    try:
        versions = json.loads(versions_file.read_text("utf-8")) if versions_file.exists() else []
        if not isinstance(versions, list):
            versions = []
        if version_clean not in versions:
            versions.insert(0, version_clean)
            versions_file.write_text(json.dumps(versions, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Could not update docs versions index: {e}")


def _deploy_docs(data_dir: Path, app_version: str) -> Path | None:
    """Deploy bundled docs to data/docs/v{version}/ and refresh same-version assets.

    Historical versions are never deleted so users can switch between them.
    """
    bundled = _find_docs_dist()
    if not bundled:
        return None

    docs_root = data_dir / "docs"
    version_clean = app_version.split("+")[0]
    version_dir = docs_root / f"v{version_clean}"

    docs_root.mkdir(parents=True, exist_ok=True)
    if _docs_version_matches_bundled(bundled, version_dir):
        _record_docs_version(docs_root, version_clean)
        return docs_root

    try:
        _sync_docs_tree(bundled, version_dir)
    except Exception as e:
        logger.warning(f"Could not deploy user docs v{version_clean}: {e}")
        if version_dir.exists():
            _record_docs_version(docs_root, version_clean)
            return docs_root
        return None

    logger.info(f"Deployed user docs v{version_clean} → {version_dir}")
    _record_docs_version(docs_root, version_clean)

    return docs_root


def _mount_web_frontend(app: FastAPI) -> None:
    """Mount the web frontend static files if available.

    Uses StaticFiles for /web/* with html=True for SPA fallback (index.html).
    """
    import mimetypes

    from fastapi.staticfiles import StaticFiles

    # On some Windows systems the registry maps .js to text/plain, causing
    # browsers to reject ES module scripts.  Ensure correct MIME types are
    # registered before StaticFiles serves any content.
    _mime_overrides = {
        ".js": "application/javascript",
        ".mjs": "application/javascript",
        ".css": "text/css",
        ".json": "application/json",
        ".wasm": "application/wasm",
        ".svg": "image/svg+xml",
    }
    for ext, mime in _mime_overrides.items():
        mimetypes.add_type(mime, ext)

    web_dist = _find_web_dist()
    if not web_dist:
        logger.debug("Web frontend not found, skipping static file mount")
        return

    logger.info(f"Mounting web frontend from {web_dist}")
    app.mount("/web", StaticFiles(directory=str(web_dist), html=True), name="web-frontend")


def _build_on_stop_org_cancel_inflight_handler(
    org_command_service: Any,
) -> Any:
    """Mint the ``on_stop_org`` callback used by ``OrgRuntime`` lifecycle.

    Sprint-5 P0-2 wired ``POST /api/v2/orgs/{id}/stop`` to drain in-flight
    commands through :meth:`OrgCommandService.cancel_all_for_org`. Sprint-6
    P0-2 added the ``cancelled_by`` source that survives onto
    ``events.jsonl``. Sprint-7 P0-A fixes the regression caught by v18
    (audit ``_orgs_business_capability_audit_v7.md`` §1.2 + §5 finding 5):
    the Sprint-6 wiring interpolated the lifecycle's inner reason kwarg
    (``"stop"`` / ``"restart"`` / ...) into a compound
    ``stop_org:<reason>`` source value, so the on-disk
    ``cancelled_by="stop_org:stop"`` no longer matched the Sprint-6
    changelog's contracted single-value taxonomy
    {``user_cancel``, ``stop_org``, ``watchdog``}.

    The handler now passes the literal ``"stop_org"`` to
    ``cancel_all_for_org`` regardless of the lifecycle's inner reason --
    the inner reason ("stop" vs "restart") is preserved on the separate
    ``org_stopped`` lifecycle event payload (see
    :meth:`OrgLifecycleManager.stop_org`), so dropping the suffix here
    loses no information for downstream readers.

    Extracted to a module-level builder so a regression test can pin
    the literal source string without standing up the full
    :func:`create_app` lifespan.
    """

    async def _on_stop_org_cancel_inflight(org_id: str, reason: str) -> None:  # noqa: ARG001 -- protocol shape
        try:
            cancelled = await org_command_service.cancel_all_for_org(
                org_id, reason="stop_org"
            )
            if cancelled:
                logger.info(
                    "stop_org cancelled %d in-flight orgs_v2 command(s) (org=%s)",
                    len(cancelled),
                    org_id,
                )
        except Exception:
            logger.debug("stop_org cancel-all failed", exc_info=True)

    return _on_stop_org_cancel_inflight


def _has_web_frontend_mount(app: FastAPI) -> bool:
    return any(getattr(route, "name", None) == "web-frontend" for route in app.routes)


def _attach_agent_to_app(app: FastAPI, agent: Any) -> None:
    app.state.agent = agent

    pm = getattr(agent, "_plugin_manager", None)
    if pm is None:
        return

    # Writes go to the shared backing dict; ``_host_refs`` is a filtered
    # read-only view for plugins (no ``__setitem__`` / ``pop``).
    ext = pm._external_host_refs
    ext["api_app"] = app
    pending = ext.pop("_pending_plugin_routers", [])
    for plugin_id, router in pending:
        try:
            # F-2 §B: third-party plugin endpoints are excluded from the public
            # OpenAPI schema so a single plugin's broken return-type annotation
            # cannot 500 /openapi.json (Pydantic >=2.12 walks ForwardRefs inside
            # register-factory closures), and because plugin endpoints are not a
            # stable public API contract -- the frontend always reaches them via
            # explicit /api/plugins/{id}/... URLs, never via OpenAPI codegen.
            app.include_router(
                router,
                prefix=f"/api/plugins/{plugin_id}",
                include_in_schema=False,
            )
            logger.info("Mounted pending plugin routes for '%s'", plugin_id)
        except Exception as e:
            logger.warning("Failed to mount pending routes for plugin '%s': %s", plugin_id, e)

    pending_ui = ext.pop("_pending_plugin_ui_mounts", [])
    for plugin_id, ui_dist_dir in pending_ui:
        try:
            pm._do_mount_plugin_ui(app, plugin_id, ui_dist_dir)
        except Exception as e:
            logger.warning("Failed to mount pending UI for plugin '%s': %s", plugin_id, e)


def _startup_health_check_clients(app_state: Any) -> tuple[Any | None, Any | None, Any | None]:
    _agent = getattr(app_state, "agent", None)
    _brain = getattr(_agent, "brain", None) if _agent else None
    _llm_client = getattr(_brain, "_llm_client", None) if _brain else None
    _compiler_client = getattr(_brain, "_compiler_client", None) if _brain else None

    if not (_llm_client and hasattr(_llm_client, "startup_health_check")):
        _llm_client = None
    if not (_compiler_client and hasattr(_compiler_client, "startup_health_check")):
        _compiler_client = None

    return _brain, _llm_client, _compiler_client


async def _run_startup_llm_health_checks(app_state: Any) -> None:
    try:
        _brain, _llm_client, _compiler_client = _startup_health_check_clients(app_state)
    except Exception as e:
        logger.debug(f"[Startup] Endpoint health check skipped: {e}")
        return

    # Endpoint health check: detect stale/broken endpoints early.
    try:
        if _llm_client:
            _results = await _llm_client.startup_health_check()
            _ok = sum(1 for v in _results.values() if v == "ok")
            _fail = len(_results) - _ok
            if _fail:
                logger.warning(
                    f"[Startup] Endpoint health check: {_ok} ok, {_fail} failed — "
                    f"{', '.join(f'{k}={v}' for k, v in _results.items() if v != 'ok')}"
                )
            else:
                logger.info(f"[Startup] All {_ok} endpoints healthy")
    except Exception as e:
        logger.debug(f"[Startup] Endpoint health check skipped: {e}")

    # Compiler endpoint health check.
    try:
        if _compiler_client:
            comp_result = await _compiler_client.startup_health_check()
            comp_failed = {k: v for k, v in comp_result.items() if v != "ok"}
            if comp_failed:
                for ep_name, status in comp_failed.items():
                    _brain._compiler_on_failure(f"startup: {ep_name}={status}")
                logger.warning(
                    f"[Startup] Compiler health check failed: "
                    f"{', '.join(f'{k}={v}' for k, v in comp_failed.items())}. "
                    f"Compiler tasks will use main model."
                )
            else:
                logger.info(f"[Startup] Compiler endpoints all healthy: {list(comp_result.keys())}")
    except Exception as e:
        logger.debug(f"[Startup] Compiler health check skipped: {e}")


def _schedule_startup_llm_health_check(app_state: Any) -> asyncio.Task[None] | None:
    existing = getattr(app_state, "llm_startup_health_check_task", None)
    if existing is not None and not existing.done():
        return existing

    try:
        _, _llm_client, _compiler_client = _startup_health_check_clients(app_state)
    except Exception as e:
        logger.debug(f"[Startup] Endpoint health check skipped: {e}")
        app_state.llm_startup_health_check_task = None
        return None

    if _llm_client is None and _compiler_client is None:
        app_state.llm_startup_health_check_task = None
        return None

    task = asyncio.create_task(
        _run_startup_llm_health_checks(app_state),
        name="openakita-startup-llm-health-check",
    )
    app_state.llm_startup_health_check_task = task
    logger.info("[Startup] LLM endpoint health check scheduled in background")
    return task


async def _cancel_startup_llm_health_check(app_state: Any) -> None:
    task = getattr(app_state, "llm_startup_health_check_task", None)
    if task is None or task.done():
        app_state.llm_startup_health_check_task = None
        return

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        logger.debug("[Shutdown] Startup LLM health check cancelled")
    finally:
        app_state.llm_startup_health_check_task = None


def create_app(
    agent: Any = None,
    shutdown_event: asyncio.Event | None = None,
    session_manager: Any = None,
    gateway: Any = None,
    orchestrator: Any = None,
    agent_pool: Any = None,
) -> FastAPI:
    """Create the FastAPI application with all routes mounted."""

    from openakita import get_version_string

    tags_metadata = [
        {"name": "认证", "description": "登录、登出、Token 刷新"},
        {"name": "对话", "description": "聊天交互、消息控制"},
        {"name": "智能体", "description": "Agent 配置文件、Bot 管理、协作拓扑"},
        {"name": "模型", "description": "可用模型/端点列表"},
        {"name": "配置", "description": "工作区配置、环境变量、端点管理"},
        {"name": "技能", "description": "技能市场、安装、配置"},
        {"name": "MCP", "description": "MCP 服务器连接与工具管理"},
        {"name": "记忆", "description": "长期记忆 CRUD 与向量检索"},
        {"name": "会话", "description": "会话历史管理"},
        {"name": "文件", "description": "文件浏览与上传"},
        {"name": "身份", "description": "AI 身份定义文件管理"},
        {"name": "定时任务", "description": "计划任务调度"},
        {"name": "即时通讯", "description": "IM 渠道与消息"},
        {"name": "站内信", "description": "客户端站内信、升级公告与未读状态"},
        {"name": "Hub", "description": "Agent/Skill 导入导出与市场"},
        {"name": "工作区", "description": "备份、导入导出"},
        {"name": "健康检查", "description": "服务健康、诊断、调试"},
        {"name": "统计", "description": "Token 用量统计"},
        {"name": "日志", "description": "服务日志查询"},
        {"name": "反馈", "description": "Bug 报告与功能建议"},
        {"name": "WebSocket", "description": "实时事件推送"},
        {"name": "系统", "description": "根路径、关机等系统操作"},
    ]

    app = FastAPI(
        title="OpenAkita API",
        description=(
            "OpenAkita 智能体平台 HTTP API\n\n"
            "提供对话、Agent 管理、技能配置、MCP 工具、定时任务等完整接口。\n\n"
            "- Swagger UI: `/docs`\n"
            "- ReDoc: `/redoc`"
        ),
        version=get_version_string(),
        openapi_tags=tags_metadata,
    )

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(request, exc: RequestValidationError):
        """Return Pydantic validation errors as a flat string detail
        so the frontend never receives raw error objects."""
        msgs = []
        for err in exc.errors():
            loc = " → ".join(str(part) for part in err.get("loc", []))
            msg = err.get("msg", "validation error")
            msgs.append(f"{loc}: {msg}" if loc else msg)
        return JSONResponse(
            status_code=422,
            content={"detail": "; ".join(msgs) if msgs else "Validation error"},
        )

    # Web access authentication — registered BEFORE CORS so that in Starlette's
    # middleware stack (last-added = outermost) CORS wraps auth, ensuring all
    # responses (including 401) carry proper CORS headers.
    try:
        from openakita.config import settings

        data_dir = Path(settings.project_root) / "data"
    except Exception:
        data_dir = Path.cwd() / "data"
    web_access_config = WebAccessConfig(data_dir)
    app.state.web_access_config = web_access_config

    auth_mw = create_auth_middleware(web_access_config)
    app.middleware("http")(auth_mw)

    # Setup gate runs **before** the auth middleware on the inbound side
    # (FastAPI middleware is LIFO: added later = executed earlier). It
    # short-circuits non-loopback requests with HTTP 428 when the web-access
    # password has not been configured yet, so the frontend can route the
    # user to the SetupView before any 401 noise.
    setup_gate_mw = create_setup_gate_middleware(web_access_config)
    app.middleware("http")(setup_gate_mw)

    # CORS configuration (outermost middleware — added last)
    # NOTE: allow_origins=["*"] is incompatible with allow_credentials=True per
    # the browser spec.  When no explicit origins are configured we fall back to
    # allow_origin_regex which matches any origin, achieving the same permissive
    # behaviour while satisfying the spec.
    cors_origins = os.environ.get("CORS_ORIGINS", "").strip()
    cors_kwargs: dict[str, Any] = {
        "allow_credentials": True,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
    }
    if cors_origins:
        origins = [o.strip() for o in cors_origins.split(",") if o.strip()]
        # Always include Capacitor mobile origins so mobile apps work
        # regardless of what the user configured in CORS_ORIGINS.
        for cap_origin in ("http://localhost", "capacitor://localhost"):
            if cap_origin not in origins:
                origins.append(cap_origin)
        cors_kwargs["allow_origins"] = origins
    else:
        cors_kwargs["allow_origin_regex"] = r".*"
    app.add_middleware(CORSMiddleware, **cors_kwargs)

    # Store references in app state
    app.state.agent = agent
    app.state.shutdown_event = shutdown_event
    app.state.session_manager = session_manager
    app.state.gateway = gateway
    app.state.orchestrator = orchestrator
    app.state.agent_pool = agent_pool
    app.state.startup_phase = "http_ready" if gateway is None else "running"
    app.state.readiness = {
        "phase": app.state.startup_phase,
        "http_ready": True,
        "im_ready": gateway is not None,
        "ready": gateway is not None,
    }

    if agent is not None:
        _attach_agent_to_app(app, agent)

    # Initialize OrgManager & OrgRuntime
    from openakita.orgs._default_agent_builder import DefaultAgentBuilder
    from openakita.orgs._runtime_agent_pipeline import (
        AgentCache,
        AgentPipelineExecutor,
        ProfileResolver,
    )
    from openakita.orgs._runtime_templates import ensure_builtin_templates
    from openakita.orgs.manager import OrgManager
    from openakita.orgs.runtime import OrgRuntime, _InMemoryEventBus
    from openakita.orgs.store import set_default_org_manager

    org_manager = OrgManager(data_dir)
    ensure_builtin_templates(data_dir / "org_templates")
    app.state.org_manager = org_manager
    # Sprint 13 H2 RC-1 wiring closure (v29 CRUD-1/2 残留): publish the
    # FastAPI-owned OrgManager as the process-wide default so the
    # JsonOrgStore shim returned by ``get_default_store()`` and the
    # spec CRUD's ``_resolve_manager_for_writes()`` resolve to the
    # same instance (and the same _cache) as ``app.state.org_manager``.
    # Without this call, the spec router's lazy fallback minted a
    # second OrgManager rooted at the same disk dir, splitting the
    # in-memory cache and leaving mint GET reads stale right after a
    # spec PATCH / DELETE -- the exact symptom v29 CRUD-1 / CRUD-2 hit.
    set_default_org_manager(org_manager)
    # H3 fix (audit ``_orgs_business_capability_audit_v1.md`` §3.2 P0):
    # build the AgentPipelineExecutor BEFORE OrgRuntime and inject its
    # ``activate_and_run`` as the ``agent_dispatch`` callback. Pre-fix
    # ``CommandDispatchManager._agent_dispatch`` was ``None`` for every
    # process, so every user command landed only on the tracker and the
    # in-memory event-bus -- no agent ever ran. We share a single
    # ``_InMemoryEventBus`` between the executor and the runtime so the
    # executor's ``agent_run_started`` / ``agent_run_failed`` /
    # ``llm_usage`` events flow through the same H4 persist + stream
    # bridges OrgRuntime installs in ``__init__``.
    #
    # Sprint-2 P0-1 (audit ``_orgs_business_capability_audit_v2.md``
    # §5 / §8): the v13 run found H1-H4 wired correctly but every
    # orgs_v2 command bouncing off ``_NullAgentBuilder`` (60+ commands,
    # 0 LLM calls). We now inject :class:`DefaultAgentBuilder` whose
    # node agents reuse the desktop ``Agent``'s ``Brain`` for a real
    # single-shot LLM call. The brain is read lazily via
    # ``app.state.agent`` because the lifespan composes the runtime
    # *before* ``main.py`` finishes building the desktop Agent; the
    # closure picks the brain up on first ``build()`` call. If the
    # desktop Agent is still missing (early cold-boot races, headless
    # tests) ``DefaultAgentBuilder`` raises ``BuilderUnavailable`` and
    # the executor turns that into the v1-parity ``agent_run_failed
    # reason=agent_build_failed`` event -- identical observable to the
    # legacy ``_NullAgentBuilder``.
    org_event_bus = _InMemoryEventBus()

    def _orgs_v2_brain_provider() -> Any:
        candidate = getattr(app.state, "agent", None)
        if candidate is None:
            return None
        return getattr(candidate, "brain", None)

    # Sprint-4 P0-1 (audit ``_orgs_business_capability_audit_v4.md``
    # §6.2): wire the agent executor's ``dispatch_subtask`` back into
    # ``DefaultAgentBuilder`` so per-node agents can recurse when the
    # LLM emits ``<dispatch target="...">...</dispatch>`` blocks. The
    # callback closure captures ``agent_executor`` *after* it is
    # defined below; ``DefaultAgentBuilder.build`` is lazy (only fires
    # on first node activation) so the forward reference resolves by
    # the time anyone actually calls it.
    #
    # The parent ``command_id`` travels through a ContextVar
    # (``current_command_id_var``) that the executor sets at the start
    # of ``activate_and_run``, so the subtask callback can attribute
    # the child run to the same id without threading it through
    # ``agent.run(content)``. Children share the parent's command id
    # by design: outcomes / cancellation / status are tracked at the
    # user-command granularity, not per-node.
    from openakita.orgs._runtime_agent_pipeline import (
        current_command_id_var,
    )

    profile_resolver = ProfileResolver(lookup=org_manager)

    async def _dispatch_subtask_cb(
        *,
        org_id: str,
        parent_node_id: str,
        child_node_id: str,
        child_content: str,
    ) -> str:
        return await agent_executor.dispatch_subtask(
            org_id=org_id,
            parent_node_id=parent_node_id,
            parent_command_id=current_command_id_var.get("") or None,
            child_node_id=child_node_id,
            child_content=child_content,
        )

    # Sprint-5 P0-1 (audit ``_orgs_business_capability_audit_v5.md`` §5.2
    # #1 + §7.1): the node agent's tool-use round emits
    # ``node_tool_called`` / ``node_tool_completed`` / ``node_tool_failed``
    # events. We hand the builder a thin emit closure rather than the
    # raw bus reference so future bus swaps (Sprint-6+ WebSocketEventBus)
    # do not need a constructor signature change.
    async def _node_tool_event_emit(event_name: str, payload: dict[str, Any]) -> None:
        try:
            await org_event_bus.emit(event_name, payload)
        except Exception:
            logger.debug("orgs_v2 node tool event emit failed", exc_info=True)

    # Sprint-6 P0-1 (RCA ``_v17_p1_rca.md`` §1.5): the node agent's
    # tool execution now routes through a :class:`NodeToolHost`
    # whose handler registry is the *populated* one from the desktop
    # Agent (filesystem / memory / web_search / 20 system handlers +
    # every plugin-registered tool). Without this v17 saw 0
    # ``node_tool_completed`` events because the global
    # ``default_handler_registry`` is empty (Sprint-5 misread of the
    # v1 wiring; see RCA §1.2.3). The provider closure resolves the
    # host lazily because both ``app.state.agent`` and the runtime
    # are populated by ``main.py`` after this lifespan callback
    # returns -- mirrors the Sprint-2 ``brain_provider`` rationale.
    def _orgs_v2_node_tool_host_provider() -> Any:
        rt = getattr(app.state, "org_runtime", None)
        if rt is None:
            return None
        get_host = getattr(rt, "get_node_tool_host", None)
        if not callable(get_host):
            return None
        return get_host()

    agent_cache = AgentCache(
        builder=DefaultAgentBuilder(
            brain_provider=_orgs_v2_brain_provider,
            dispatch_callback=_dispatch_subtask_cb,
            event_emitter=_node_tool_event_emit,
            tool_host_provider=_orgs_v2_node_tool_host_provider,
        )
    )

    # Sprint-6 P0-2 (RCA ``_v17_p1_rca.md`` §2.5): resolve the
    # cancel source the outcome cache stashed
    # (``stop_org`` / ``watchdog``) so the ``except CancelledError``
    # branch in :meth:`AgentPipelineExecutor.activate_and_run` can
    # stamp it on the ``agent_run_cancelled`` events.jsonl payload.
    # The lookup is lazy because :class:`OrgCommandService` is
    # constructed *after* the executor here; the closure picks it
    # up on first cancel.
    def _orgs_v2_cancel_source_provider(command_id: str) -> str | None:
        svc = getattr(app.state, "org_command_service", None)
        if svc is None:
            return None
        getter = getattr(svc, "get_cancel_source", None)
        if not callable(getter):
            return None
        try:
            return getter(command_id)
        except Exception:  # noqa: BLE001 -- best-effort observability
            return None

    agent_executor = AgentPipelineExecutor(
        cache=agent_cache,
        resolver=profile_resolver,
        lookup=org_manager,
        event_bus=org_event_bus,
        cancel_source_provider=_orgs_v2_cancel_source_provider,
    )

    async def _agent_dispatch(
        org_id: str,
        target_node_id: str,
        command_id: str,
        content: str,
    ) -> dict[str, Any]:
        return await agent_executor.activate_and_run(
            org_id=org_id,
            node_id=target_node_id,
            content=content,
            command_id=command_id,
        )

    # P-RC-9 P9.6 made ``OrgRuntime.__init__`` keyword-only with required
    # ``lookup`` / ``persistence`` / ``lifecycle_emitter`` Protocols.  The
    # v2 ``OrgManager`` itself implements ``OrgLookupProtocol`` and owns
    # the default ``_FilesystemOrgPersistence`` + ``_NoopOrgLifecycleEmitter``
    # siblings, so the composition root re-uses them.  See
    # ``tests/api/test_server_app_wiring.py`` for the regression guard.
    org_runtime = OrgRuntime(
        lookup=org_manager,
        persistence=org_manager._persistence,
        lifecycle_emitter=org_manager._lifecycle,
        event_bus=org_event_bus,
        agent_dispatch=_agent_dispatch,
    )
    app.state.org_runtime = org_runtime
    app.state.org_agent_executor = agent_executor
    from openakita.orgs.command_service import OrgCommandService, set_command_service

    # P-RC-9 P9.4 made ``OrgCommandService.__init__`` keyword-only after
    # the leading ``runtime`` argument; pass session_manager by name.
    #
    # Sprint-2 P0-2 (audit ``_orgs_business_capability_audit_v2.md`` §5
    # F1-new): ``GET /api/v2/orgs/{id}/commands/{cid}`` was returning
    # ``phase=done, error=null`` while ``events.jsonl`` showed
    # ``agent_run_failed reason=agent_build_failed``. The status was
    # written by ``_run_minimal``'s success branch (because
    # ``runtime.send_command`` returns "submitted" before the agent
    # dispatch callback observes failure). We now share the same
    # ``_InMemoryEventBus`` with the service so it can subscribe to
    # ``agent_run_*`` events keyed by command_id and reflect the real
    # outcome back through ``get_status`` -- UI shows "failed" when the
    # node actually failed.
    # Sprint-9 supervisor takeover: inject the live executor +
    # per-org sqlite checkpointer factory so submit() can build a
    # :class:`Supervisor` per command via
    # :mod:`openakita.runtime.supervisor_factory`.
    from openakita.runtime.supervisor_factory import get_or_create_checkpointer

    def _executor_provider() -> Any:
        return getattr(app.state, "org_agent_executor", None)

    def _checkpointer_provider(org_id: str) -> Any:
        return get_or_create_checkpointer(org_id)

    org_command_service = OrgCommandService(
        org_runtime,
        session_manager=session_manager,
        event_bus=org_event_bus,
        executor_provider=_executor_provider,
        checkpointer_provider=_checkpointer_provider,
    )
    set_command_service(org_command_service)
    app.state.org_command_service = org_command_service

    # B1 (audit data-contract gap): wire the per-org ProjectStore /
    # OrgBlackboard / NodeScheduler registries. Pre-fix these were never
    # attached to app.state, so GET /{id}/{projects,memory,tasks,...}
    # all 503'd ("subsystem_not_wired") and the kanban / blackboard /
    # project UI panels were permanently empty. The registries resolve
    # the real per-org backend from the request path (see
    # ``orgs_v2_runtime._get_project_store`` / ``_get_blackboard``), so
    # org isolation is preserved. ``/_p97/health`` reports all_wired once
    # these (plus the runtime status/node-status methods above) exist.
    from openakita.orgs.scoped_subsystems import (
        OrgScopedBlackboard,
        OrgScopedProjectStore,
        OrgScopedScheduler,
    )

    app.state.project_store = OrgScopedProjectStore(org_manager)
    app.state.org_blackboard = OrgScopedBlackboard(org_manager)
    app.state.node_scheduler = OrgScopedScheduler(org_manager)

    # B4/B5/B6: hand the runtime the per-org project/blackboard registries
    # so its contract-bridge event tap can persist delegated subtasks as
    # kanban tasks and node deliverables as blackboard facts/resources.
    org_runtime.set_contract_sinks(
        project_store=app.state.project_store,
        blackboard=app.state.org_blackboard,
    )

    # Sprint-5 P0-2 (audit v5 §5.2 #1 + v15 §6.2.4 B6.4): wire the
    # lifecycle ``on_stop_org`` callback so ``POST /api/v2/orgs/{id}/stop``
    # cancels every per-org in-flight task instead of just flipping
    # the spec to STOPPED while the LLM keeps burning tokens.
    org_runtime.set_on_stop_org(
        _build_on_stop_org_cancel_inflight_handler(org_command_service)
    )

    # Sprint-6 P0-1 (RCA ``_v17_p1_rca.md`` §1.5): mint a
    # :class:`NodeToolHost` from the desktop Agent if one is already
    # wired. ``main.py`` may complete Agent initialisation after this
    # lifespan runs, in which case ``update_agent`` /
    # ``update_runtime_refs`` re-runs the bind below. The provider
    # closure handed to :class:`DefaultAgentBuilder` reads the host
    # lazily from ``app.state.org_runtime``, so a late bind here is
    # observed on the next node activation.
    _refresh_node_tool_host(app)

    # Mount routes
    app.include_router(auth_routes.router, tags=["认证"])
    app.include_router(agents.router, tags=["智能体"])
    app.include_router(bug_report.router, tags=["反馈"])
    app.include_router(chat.router, tags=["对话"])
    app.include_router(chat_models.router, tags=["模型"])
    app.include_router(config.router, tags=["配置"])
    app.include_router(diagnostics.router, tags=["诊断"])
    app.include_router(feishu_onboard.router, tags=["飞书扫码"])
    app.include_router(qqbot_onboard.router, tags=["QQ扫码"])
    app.include_router(wechat_onboard.router, tags=["微信扫码"])
    app.include_router(wecom_onboard.router, tags=["企微扫码"])
    app.include_router(files.router, tags=["文件"])
    app.include_router(health.router, tags=["健康检查"])
    app.include_router(im.router, tags=["即时通讯"])
    app.include_router(inbox.router, tags=["站内信"])
    app.include_router(logs.router, tags=["日志"])
    app.include_router(mcp.router, tags=["MCP"])
    app.include_router(memory.router, tags=["记忆"])
    app.include_router(memory_repair.router, tags=["记忆修复"])
    app.include_router(scheduler.router, tags=["定时任务"])
    app.include_router(pending_approvals.router, tags=["待审批"])
    app.include_router(sessions.router, tags=["会话"])
    app.include_router(skills.router, tags=["技能"])
    app.include_router(skill_categories.router, tags=["技能分类"])
    app.include_router(skill_stats.router, tags=["统计"])
    app.include_router(token_stats.router, tags=["统计"])
    app.include_router(upload.router, tags=["文件"])
    app.include_router(web_search_routes.router)
    app.include_router(workspace_io.router, tags=["工作区"])
    app.include_router(workspaces.router, tags=["工作区管理"])
    app.include_router(ws_routes.router, tags=["WebSocket"])
    app.include_router(hub.router, tags=["Hub"])
    app.include_router(identity.router, tags=["身份"])
    # v2 organisation facade — gated at request time by
    # ``settings.runtime_v2_enabled`` (returns 404 when off). Safe to
    # always-mount because the route bodies refuse to serve when the
    # flag is false.
    app.include_router(orgs_v2.router)
    # P-RC-2 commit P2.3: SSE stream endpoint for v2 orgs
    # (``GET /api/v2/orgs/{id}/stream``). Same flag-gating story as
    # ``orgs_v2.router`` -- always-mount, refuse-to-serve when
    # ``runtime_v2_enabled`` is False.
    app.include_router(orgs_v2_stream.router)
    # P-RC-9 P9.7a-2c: v2 runtime router skeleton (`/api/v2/orgs`).
    # Registered BEFORE the 308 redirect shim so the future P9.7
    # mint endpoints (and the current `/_p97/health` probe) take
    # precedence over the redirect for any path they claim.
    app.include_router(orgs_v2_runtime.router)
    # P-RC-9 P9.7a-2a: 308 Permanent Redirect shim for the
    # original P-RC-3 Group A paths under ``/api/v2/orgs[/...]``
    # (frontend rewiring lands in P9.8). See DECISIONS.md D-1
    # (R3 LOCKED). Registered LAST so future P9.7 mint endpoints
    # at the same ``/api/v2/orgs`` prefix take precedence over
    # the redirect for routes the mint actually claims.
    #
    # ROADMAP — Legacy shim removal target: OpenAkita 2.1.0 minor.
    # Deprecation headers were applied in Fix-G5 (RCA v11 §3). The
    # shim is tracked by ``docs/follow-ups/skipped-items-roadmap.md``
    # §A.3; monitor ``GET /api/diagnostics/legacy-shim-stats`` to
    # confirm the 30-day-zero-hits exit criterion before removal.
    app.include_router(_orgs_v2_legacy_redirects.router)
    # P-RC-2 commit P2.8: GET /api/build-info for the frontend
    # stale-bundle banner. Always-mounted, unauthenticated.
    app.include_router(build_info_routes.router)
    if plugins_routes is not None:
        app.include_router(plugins_routes.router)

    @app.get("/", tags=["系统"])
    async def root():
        # If web frontend is available, redirect to it
        web_dist = _find_web_dist()
        if web_dist or _has_web_frontend_mount(app):
            from fastapi.responses import RedirectResponse

            return RedirectResponse(url="/web/")
        return {
            "service": "openakita",
            "api_version": "1.0.0",
            "status": "running",
        }

    # ── Serve uploaded avatar files ──
    from fastapi.staticfiles import StaticFiles as _StaticFiles

    from openakita.config import settings as _settings

    _avatar_dir = _settings.data_dir / "avatars"
    _avatar_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/api/avatars", _StaticFiles(directory=str(_avatar_dir)), name="avatars")

    # NOTE: prior to SDK 0.7.0 we mounted /api/plugins/_sdk/* here to serve
    # bootstrap.js and the shared ui-kit out of the openakita_plugin_sdk
    # wheel. The SDK no longer ships any frontend assets — every plugin UI
    # is now expected to be self-contained under its own ui/dist/_assets/
    # directory (see plugins/tongyi-image and plugins/seedance-video for
    # working examples, or plugins-archive/_shared/web-uikit/README.md for
    # how to revive an archived plugin's UI). Do not re-add this mount.

    # ── Serve versioned user docs ──
    from openakita import get_version_string as _get_ver

    _docs_ver = _get_ver().split("+")[0]
    _docs_root = _deploy_docs(data_dir, _docs_ver)
    if _docs_root:
        from fastapi.responses import RedirectResponse as _Redirect
        from fastapi.staticfiles import StaticFiles as _DocsStatic

        @app.get("/user-docs", include_in_schema=False)
        @app.get("/user-docs/", include_in_schema=False)
        async def _docs_redirect(request: Request):
            target = f"/user-docs/v{_docs_ver}/"
            if request.url.query:
                target = f"{target}?{request.url.query}"
            response = _Redirect(target)
            response.headers["Cache-Control"] = "no-store"
            return response

        app.mount(
            "/user-docs",
            _DocsStatic(directory=str(_docs_root), html=True),
            name="user-docs",
        )
        logger.info(f"Mounted user docs at /user-docs/ from {_docs_root}")

    # ── Serve web frontend static files ──
    _mount_web_frontend(app)

    @app.on_event("startup")
    async def _import_pending_feedback():
        """Import any feedback records staged by Tauri while the backend was down."""
        try:
            from openakita.config import settings

            home = settings.openakita_home
        except Exception:
            home = Path.home() / ".openakita"
        pending = home / "pending_feedback.json"
        if not pending.exists():
            return
        try:
            import json as _json

            records = _json.loads(pending.read_text("utf-8"))
            if not isinstance(records, list):
                pending.unlink(missing_ok=True)
                return
            from .routes import feedback_store

            imported = 0
            for rec in records:
                try:
                    await feedback_store.save_record(
                        report_id=rec.get("reportId") or rec.get("report_id", ""),
                        feedback_token=rec.get("feedbackToken") or rec.get("feedback_token"),
                        title=rec.get("title", ""),
                        report_type=rec.get("reportType") or rec.get("report_type", "bug"),
                        contact_email=rec.get("contactEmail") or rec.get("contact_email", ""),
                        submitted_at=rec.get("submittedAt") or rec.get("submitted_at"),
                    )
                    imported += 1
                except Exception as rec_err:
                    logger.warning("Skip bad pending feedback record: %s", rec_err)
            pending.unlink(missing_ok=True)
            if imported:
                logger.info(
                    "Imported %d pending feedback record(s) from Tauri offline staging",
                    imported,
                )
        except Exception as exc:
            logger.warning("Failed to import pending feedback: %s", exc)
            pending.unlink(missing_ok=True)

    @app.on_event("startup")
    async def _cleanup_memory_recovery_pending():
        try:
            from .routes.memory_repair import _cleanup_old_recovery_pending

            await asyncio.to_thread(_cleanup_old_recovery_pending)
        except Exception as e:
            logger.debug("[Startup] Memory recovery pending cleanup skipped: %s", e)

    @app.on_event("startup")
    async def _cleanup_expired_resume_state():
        """Issue #608: drop crash-leftover cancel-resume snapshots in
        ``data/working_messages/`` so a process that died mid-turn doesn't
        keep re-injecting yesterday's half-finished tool state on resume."""
        try:
            from openakita.core.cancel_cleanup import cleanup_expired_working_messages

            removed = await asyncio.to_thread(cleanup_expired_working_messages, base_dir=data_dir)
            if removed:
                logger.info("[Startup] Removed %d expired cancel-resume snapshot(s)", removed)
        except Exception as e:
            logger.debug("[Startup] Cancel-resume snapshot cleanup skipped: %s", e)

    @app.on_event("startup")
    async def _wire_pending_approvals_sse():
        """Bridge PendingApprovalsStore events to WebSocket and owner IM delivery.

        The Store is policy-loop-agnostic and may call its synchronous hook from
        another thread. WebSocket broadcast is already cross-loop safe; IM
        delivery is scheduled onto the API loop captured during startup.
        """
        try:
            from openakita.agent.pending_approval_notifications import (
                build_pending_approval_event_hook,
                notify_pending_approval_im,
            )
            from openakita.api.routes.websocket import fire_event
            from openakita.core.pending_approvals import get_pending_approvals_store

            api_loop = asyncio.get_running_loop()

            async def _notify_owner(payload: dict) -> None:
                try:
                    from openakita.scheduler import get_active_scheduler

                    active_scheduler = get_active_scheduler()
                    if active_scheduler is None:
                        active_scheduler = getattr(
                            getattr(app.state, "agent", None), "task_scheduler", None
                        )
                    await notify_pending_approval_im(
                        payload,
                        scheduler=active_scheduler,
                        gateway=getattr(app.state, "gateway", None),
                    )
                except Exception:
                    logger.warning(
                        "[PendingApprovals] unexpected IM notification failure for %s",
                        payload.get("id"),
                        exc_info=True,
                    )

            event_hook = build_pending_approval_event_hook(
                loop=api_loop,
                fire_event=fire_event,
                notify_owner=_notify_owner,
            )

            get_pending_approvals_store().set_event_hook(event_hook)
            logger.info("[Startup] PendingApprovals WebSocket/IM hook wired")
        except Exception as e:
            logger.warning("[Startup] PendingApprovals event wire failed: %s", e)

        # C17 Phase B.4：把 UIConfirmBus 的 confirm_initiated /
        # confirm_revoked 广播绑到同一条 fire_event 通道，让多端 UI 共享
        # confirm 生命周期信号。
        try:
            from openakita.api.routes.websocket import fire_event
            from openakita.core.ui_confirm_bus import get_ui_confirm_bus

            def _confirm_hook(event_type: str, payload: dict) -> None:
                fire_event(event_type, payload)

            get_ui_confirm_bus().set_broadcast_hook(_confirm_hook)
            logger.info("[Startup] UIConfirmBus broadcast hook wired")
        except Exception as e:
            logger.warning("[Startup] UIConfirmBus broadcast wire failed: %s", e)

        # C18 Phase A：POLICIES.yaml hot-reload。默认 disabled；用户在
        # POLICIES.yaml 的 ``hot_reload.enabled: true`` opt-in 即生效。
        # 失败安全：``start_hot_reloader`` 内部全 try/except，未启动也不
        # 影响 server 启动。
        try:
            from openakita.core.policy_v2.hot_reload import start_hot_reloader

            reloader = start_hot_reloader()
            if reloader is not None:
                logger.info("[Startup] PolicyHotReloader started")
            else:
                logger.debug(
                    "[Startup] PolicyHotReloader not started (disabled or no POLICIES.yaml)"
                )
        except Exception as e:
            logger.warning("[Startup] PolicyHotReloader wire failed: %s", e)

    @app.post("/api/shutdown", tags=["系统"])
    async def shutdown(request: Request):
        """Gracefully shut down the OpenAkita service process.

        Only allowed from localhost for security.
        Uses the shared shutdown_event to trigger the same graceful cleanup
        path as SIGINT/SIGTERM (sessions saved, IM adapters stopped, etc.).

        Sprint 14 / v31 Phase A safety net: schedule a background
        ``os._exit(0)`` after ``settings.shutdown_force_exit_grace_s``
        seconds. The graceful path always runs first; this only fires when
        something hung the lifespan (v23/v24/v26/v28/v29/v30 all needed
        ``Stop-Process`` because Phase A never returned within 13~20 s).
        """
        from .auth import get_client_ip

        trust_proxy = os.environ.get("TRUST_PROXY", "").lower() in ("1", "true", "yes")
        real_ip = get_client_ip(request, trust_proxy=trust_proxy)
        is_local = real_ip in ("127.0.0.1", "::1", "localhost") or (
            real_ip.startswith("::ffff:") and real_ip[7:] == "127.0.0.1"
        )
        if not is_local:
            return JSONResponse(
                status_code=403,
                content={"detail": "Shutdown only allowed from localhost"},
            )
        logger.info("Shutdown requested via API")
        if app.state.shutdown_event is not None:
            app.state.shutdown_event.set()
            _schedule_force_exit_after_grace(app)
            return {"status": "shutting_down"}
        logger.warning("No shutdown_event available, shutdown request ignored")
        return {"status": "error", "message": "shutdown not available in this mode"}

    @app.on_event("startup")
    async def _start_inbox_service():
        try:
            from openakita.config import settings
            from openakita.inbox import get_inbox_service

            if settings.inbox_enabled:
                await get_inbox_service().start()
        except Exception as e:
            logger.warning("[Startup] Inbox service startup skipped: %s", e)

    @app.on_event("shutdown")
    async def _stop_inbox_service():
        try:
            from openakita.inbox import get_inbox_service

            await get_inbox_service().stop()
        except Exception as e:
            logger.debug("[Shutdown] Inbox service stop skipped: %s", e)

    @app.on_event("startup")
    async def _startup_org_runtime():
        loop = asyncio.get_running_loop()
        loop.slow_callback_duration = 0.5
        # v22 RCA RC-7: ``OrgRuntime`` is a composed lifecycle component
        # whose surface is ``start_org`` / ``stop_org`` / ``pause_org`` /
        # ``resume_org`` (see ``orgs/runtime.py``). The single
        # ``OrgRuntime.start()`` entrypoint was retired when org
        # lifecycle was decomposed; calling it here only produced a
        # warning on every boot. Reconcile / NodeToolHost / SSE bus
        # wiring already happens through dedicated component init, so
        # nothing additional needs to fire at startup.
        # v22 P1 (audit v10 §19 / cmd_..._f092f4 slot leak): start
        # the ``OrgCommandService`` reconcile loop so a stale
        # ``_running_by_root`` slot eventually drops even if the
        # ``_schedule_run.run`` ``finally`` block was skipped. The
        # hard ceiling wrapper inside ``_run_supervisor_with_hard_ceiling``
        # is the first line of defence; reconcile is the second.
        # Best-effort: a startup failure here must not crash the API.
        svc = getattr(app.state, "org_command_service", None)
        if svc is not None:
            start_loop = getattr(svc, "start_reconcile_loop", None)
            if callable(start_loop):
                try:
                    await start_loop()
                except Exception as exc:  # noqa: BLE001 -- best-effort
                    logger.warning(
                        "[Startup] OrgCommandService.start_reconcile_loop failed: %s",
                        exc,
                    )
        # Sprint-9 supervisor HTTP takeover: the legacy
        # ``OrgCommandService.start_watchdog()`` wall-clock loop is
        # gone. Stall detection is now LLM-evaluated by the
        # supervisor's :class:`StallDetector` on
        # :class:`ProgressLedger` signals, with the hard
        # ``max_turns`` cap as the only wall-style guard. The new
        # reconcile loop above only reconciles bookkeeping; it does
        # not perform any wall-clock termination.

        _schedule_startup_llm_health_check(app.state)

    @app.on_event("shutdown")
    async def _shutdown_org_runtime():
        # v22 P1: stop the reconcile loop FIRST so it cannot fire
        # against a half-torn-down runtime. Best-effort; a shutdown
        # failure here only logs.
        #
        # Sprint 14 / v31 Phase A hardening: every unbounded ``await``
        # in this lifespan handler is now wrapped in a per-stage
        # ``settings.lifespan_stage_timeout_s`` (default 8s) so a hung
        # checkpointer / runtime.shutdown cannot block subsequent stages
        # the way Phase A reproduced 6/6 in v23~v30.
        try:
            from openakita.config import settings as _settings

            stage_timeout = float(
                getattr(_settings, "lifespan_stage_timeout_s", 8) or 8
            )
        except Exception:
            stage_timeout = 8.0

        svc = getattr(app.state, "org_command_service", None)
        if svc is not None:
            stop_loop = getattr(svc, "stop_reconcile_loop", None)
            if callable(stop_loop):
                try:
                    await stop_loop(timeout=2.0)
                except Exception as exc:  # noqa: BLE001 -- best-effort
                    logger.debug(
                        "OrgCommandService.stop_reconcile_loop error: %s", exc
                    )
        # Sprint-9 supervisor takeover: close per-org sqlite
        # checkpointers so the file handles are released cleanly. The
        # legacy ``stop_watchdog()`` call is gone with the watchdog
        # loop itself.
        try:
            from openakita.runtime.supervisor_factory import (
                aclose_all_checkpointers,
            )

            await asyncio.wait_for(
                aclose_all_checkpointers(), timeout=stage_timeout
            )
        except TimeoutError:
            logger.warning(
                "[Shutdown] aclose_all_checkpointers exceeded %.1fs, abandoning",
                stage_timeout,
            )
        except Exception:
            logger.debug("Supervisor checkpointer aclose error", exc_info=True)
        if hasattr(app.state, "org_runtime") and app.state.org_runtime:
            try:
                from openakita.core.engine_bridge import to_engine

                await asyncio.wait_for(
                    to_engine(app.state.org_runtime.shutdown()),
                    timeout=stage_timeout,
                )
            except TimeoutError:
                logger.warning(
                    "[Shutdown] OrgRuntime.shutdown exceeded %.1fs, abandoning",
                    stage_timeout,
                )
            except Exception as e:
                logger.warning(f"OrgRuntime shutdown error: {e}")

    @app.on_event("shutdown")
    async def _shutdown_startup_llm_health_check():
        await _cancel_startup_llm_health_check(app.state)

    @app.on_event("shutdown")
    async def _shutdown_policy_hot_reloader():
        try:
            from openakita.core.policy_v2.hot_reload import stop_hot_reloader

            stop_hot_reloader(timeout=2.0)
        except Exception as e:
            logger.debug("[Shutdown] PolicyHotReloader stop skipped: %s", e)

    @app.on_event("shutdown")
    async def _shutdown_memory_storage():
        try:
            from openakita.memory.storage import checkpoint_and_close_all_storages

            await asyncio.wait_for(
                asyncio.to_thread(checkpoint_and_close_all_storages),
                timeout=3.0,
            )
        except TimeoutError:
            logger.warning("[Shutdown] Memory checkpoint timeout (3s), proceeding")
        except Exception as e:
            logger.warning("[Shutdown] Memory checkpoint skipped: %s", e)

    @app.on_event("startup")
    async def _start_async_audit_writer():
        """C22 P3-2 follow-up: wire the async audit writer to the API loop.

        Without this hook the writer is dead code — ``AuditLogger.log()``
        always sees ``get_async_audit_writer(...)`` returning ``None`` and
        falls back to the per-row filelock sync path. After this hook the
        same code path coalesces writes into batches transparently.

        Fail-safe: any exception here logs WARNING and leaves the system
        on the sync path; nothing downstream relies on the async writer
        being up.
        """
        try:
            from openakita.core.audit_logger import DEFAULT_AUDIT_PATH
            from openakita.core.policy_v2.audit_writer import (
                start_global_audit_writer,
            )

            try:
                from openakita.core.policy_v2.global_engine import get_config_v2

                cfg = get_config_v2().audit
                path = cfg.log_path if (cfg and cfg.enabled) else None
            except Exception:
                path = None
            if not path:
                path = DEFAULT_AUDIT_PATH

            await start_global_audit_writer(path)
            logger.info("[Startup] AsyncBatchAuditWriter started for %s", path)
        except Exception as e:
            logger.warning(
                "[Startup] AsyncBatchAuditWriter not started; sync fallback remains active: %s",
                e,
            )

    @app.on_event("shutdown")
    async def _shutdown_async_audit_writer():
        """Drain + stop the async audit writer.

        Bounded by ``stop()``'s internal timeout (split between sentinel
        delivery and worker drain); anything still queued past that
        deadline is logged + dropped rather than blocking shutdown.

        Sprint 14 / v31 Phase A hardening: layer an outer
        ``settings.lifespan_stage_timeout_s`` (default 8s) wait_for so
        even an internal-timeout regression cannot pin the lifespan.
        """
        try:
            from openakita.config import settings as _settings
            from openakita.core.policy_v2.audit_writer import (
                stop_global_audit_writer,
            )

            stage_timeout = float(
                getattr(_settings, "lifespan_stage_timeout_s", 8) or 8
            )
            await asyncio.wait_for(stop_global_audit_writer(), timeout=stage_timeout)
            logger.info("[Shutdown] AsyncBatchAuditWriter stopped")
        except TimeoutError:
            logger.warning(
                "[Shutdown] AsyncBatchAuditWriter stop exceeded %ss, abandoning",
                stage_timeout,
            )
        except Exception as e:
            logger.warning("[Shutdown] AsyncBatchAuditWriter stop error: %s", e)

    # ------------------------------------------------------------
    # P-RC-3 T4: idle StreamBus cleanup (per-org SSE registry).
    # ------------------------------------------------------------
    app.state.stream_cleanup_task = None

    @app.on_event("startup")
    async def _start_stream_cleanup() -> None:
        try:
            from openakita.runtime.stream_registry import (
                cleanup_idle_buses_periodically,
            )

            app.state.stream_cleanup_task = asyncio.create_task(
                cleanup_idle_buses_periodically(),
                name="openakita-stream-registry-cleanup",
            )
            logger.info("[Startup] StreamRegistry cleanup task started")
        except Exception as e:  # noqa: BLE001 -- never block startup
            logger.warning("[Startup] StreamRegistry cleanup not started: %s", e)

    @app.on_event("shutdown")
    async def _stop_stream_cleanup() -> None:
        task = getattr(app.state, "stream_cleanup_task", None)
        if task is None:
            return
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.CancelledError, TimeoutError):
            pass
        except Exception as e:  # noqa: BLE001
            logger.warning("[Shutdown] StreamRegistry cleanup stop error: %s", e)

    # ------------------------------------------------------------
    # Fix-5 / exploratory v10 issue #5b: frontend bundle drift warn.
    # ------------------------------------------------------------
    app.state.frontend_bundle_build_id = None
    app.state.frontend_bundle_outdated = False

    @app.on_event("startup")
    async def _check_frontend_bundle_freshness() -> None:
        try:
            from openakita import __version__ as backend_version
            from openakita.api.routes.build_info import (
                detect_frontend_bundle_build_id,
                is_frontend_bundle_outdated,
            )

            dist = _find_web_dist()
            if dist is None:
                return
            bundle_id = detect_frontend_bundle_build_id(dist)
            app.state.frontend_bundle_build_id = bundle_id
            if bundle_id is None:
                return
            # Shared rule with /api/health frontend_bundle.outdated so the
            # startup warning and the runtime field never disagree
            # (exploratory v12 §10.2 follow-up).
            outdated = is_frontend_bundle_outdated(bundle_id, backend_version)
            app.state.frontend_bundle_outdated = outdated
            if outdated:
                logger.warning(
                    "[Startup] Frontend bundle build_id=%s lags backend "
                    "version=%s; consider rebuilding apps/setup-center",
                    bundle_id,
                    backend_version,
                )
        except Exception as e:  # noqa: BLE001 -- never block startup
            logger.debug("[Startup] Frontend bundle freshness check skipped: %s", e)

    # ------------------------------------------------------------
    # Sprint 16 P0: close aiosqlite connections held by loaded plugins.
    # Forensics: ``_v32_biz_e2e/_diagnostics_analysis.md`` — every
    # PHASEA round left 14 stale ``Thread-NN
    # (_connection_worker_thread)`` alive (non-daemon, because
    # aiosqlite.core.py:90 forgot ``daemon=True``), pinning Python's
    # interpreter teardown for ~13 s and forcing the threading.Timer
    # force-exit to fire 6/6 PHASEA + 8/8 UVICORN runs. Root cause:
    # serve-mode shutdown never invoked ``agent.shutdown()`` /
    # ``pm.unload_plugin(...)``, so plugin ``on_unload`` (which already
    # contains ``await self._tm.close()``) never ran. Closing here
    # joins each aiosqlite worker, dropping shutdown_to_exit_s from
    # ~16.7 s → ≤10 s and demoting force-exit watchdog back to a
    # pure safety net.
    #
    # Registered BEFORE diagnostics so the diagnostics dump can
    # observe a clean thread set; registered AFTER the other
    # @app.on_event("shutdown") handlers because plugins may still
    # call into them during their own ``on_unload``.
    # ------------------------------------------------------------
    @app.on_event("shutdown")
    async def _shutdown_plugin_aiosqlite_workers() -> None:
        try:
            from openakita.config import settings as _settings

            stage_timeout = float(
                getattr(_settings, "lifespan_stage_timeout_s", 8) or 8
            )
        except Exception:
            stage_timeout = 8.0

        agent_ref = getattr(app.state, "agent", None)
        pm = getattr(agent_ref, "_plugin_manager", None) if agent_ref else None
        if pm is None:
            logger.debug(
                "[Shutdown] No PluginManager on app.state.agent; "
                "skipping plugin aiosqlite-worker close",
            )
            return

        loaded_before = getattr(pm, "loaded_count", 0)
        t0 = time.monotonic()
        try:
            await asyncio.wait_for(pm.shutdown(), timeout=stage_timeout)
        except TimeoutError:
            logger.warning(
                "[Shutdown] PluginManager.shutdown exceeded %.1fs; "
                "%d plugin(s) may still hold aiosqlite worker threads "
                "(force-exit watchdog is the final fallback)",
                stage_timeout,
                loaded_before,
            )
        except Exception as exc:  # noqa: BLE001 -- shutdown must never raise
            logger.warning(
                "[Shutdown] PluginManager.shutdown raised: %s "
                "(continuing teardown; force-exit watchdog will bound exit)",
                exc,
            )
        else:
            logger.info(
                "[Shutdown] Plugin aiosqlite workers closed "
                "(loaded_before=%d, elapsed=%.2fs)",
                loaded_before,
                time.monotonic() - t0,
            )

        # Defensive belt-and-suspenders: close the token_stats
        # ``Database`` singleton (a lazy aiosqlite connection minted
        # on first ``/api/stats/tokens/*`` call). Without this hook
        # the singleton survives lifespan teardown and contributes
        # one extra non-daemon ``_connection_worker_thread`` to the
        # interpreter-teardown hang. Module-level helpers
        # ``_reset_db`` / ``_db_instance`` already exist; we just
        # had to start invoking them.
        try:
            from openakita.api.routes import token_stats as _token_stats

            reset_fn = getattr(_token_stats, "_reset_db", None)
            if callable(reset_fn) and getattr(_token_stats, "_db_instance", None) is not None:
                await asyncio.wait_for(reset_fn(), timeout=3.0)
                logger.info("[Shutdown] token_stats.Database singleton closed")
        except TimeoutError:
            logger.warning("[Shutdown] token_stats.Database close exceeded 3s")
        except Exception as exc:  # noqa: BLE001
            logger.debug("[Shutdown] token_stats.Database close error: %s", exc)

        # Also close any storage.Database instance that callers may
        # have pinned to app.state under a conventional attribute
        # name (forward-compat for future routes that own a long-
        # lived Database).
        for attr in ("storage_database", "_storage_database"):
            db = getattr(app.state, attr, None)
            if db is None:
                continue
            close_fn = getattr(db, "close", None)
            if not callable(close_fn):
                continue
            try:
                await asyncio.wait_for(close_fn(), timeout=3.0)
                logger.info("[Shutdown] storage.Database (%s) closed", attr)
            except TimeoutError:
                logger.warning(
                    "[Shutdown] storage.Database (%s) close exceeded 3s", attr
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "[Shutdown] storage.Database (%s) close error: %s", attr, exc
                )

    # ------------------------------------------------------------
    # Sprint 15 / v32 Phase B Task C: lifespan→process-exit hang RCA.
    # Registered LAST so it runs LAST in Starlette's FIFO shutdown
    # order, i.e. after every other shutdown handler has done its
    # cleanup. This is when the unexplained ~13s hang historically
    # begins (see ``_v32_biz/_phase_b_hang_rca.md``).
    # ------------------------------------------------------------
    @app.on_event("shutdown")
    async def _arm_shutdown_diagnostics() -> None:
        try:
            from openakita.config import settings as _settings

            if not bool(getattr(_settings, "shutdown_diagnostics_enabled", True)):
                return
            interval = float(
                getattr(_settings, "shutdown_diagnostics_interval_s", 1.0) or 1.0
            )
        except Exception:
            interval = 1.0

        try:
            from openakita.api._shutdown_diagnostics import arm_shutdown_diagnostics

            try:
                from openakita.config import settings as _settings2

                log_dir = Path(_settings2.project_root) / "data" / "logs"
            except Exception:
                log_dir = Path.cwd() / "data" / "logs"
            arm_shutdown_diagnostics(log_dir, interval_s=interval)
        except Exception as exc:  # noqa: BLE001 -- diagnostics must not block
            logger.warning("[Shutdown] Failed to arm shutdown diagnostics: %s", exc)

    return app


async def start_api_server(
    agent: Any = None,
    shutdown_event: asyncio.Event | None = None,
    session_manager: Any = None,
    gateway: Any = None,
    orchestrator: Any = None,
    agent_pool: Any = None,
    host: str = "127.0.0.1",
    port: int = API_PORT,
    max_retries: int = 5,
) -> asyncio.Task:
    """
    Start the HTTP API server in a **dedicated background thread** with its
    own asyncio event loop ("API loop").

    The calling loop becomes the "engine loop" — it keeps running Agent,
    OrgRuntime, Scheduler, Gateway and all other heavy async work.  The API
    loop only handles HTTP request/response and WebSocket I/O, so it stays
    responsive even when the engine is saturated with LLM calls.

    Returns a proxy ``asyncio.Task`` in the engine loop.  Cancelling this
    task triggers a graceful uvicorn shutdown.

    Raises RuntimeError if the server cannot start after all retries.
    """
    import threading

    import uvicorn

    # 端口预检：如果端口不可用，先等待释放（处理 TIME_WAIT 等场景）
    if not is_port_free(host, port):
        logger.warning(f"Port {port} is currently in use, waiting for it to be released...")
        freed = await asyncio.to_thread(wait_for_port_free, host, port, 30.0)
        if not freed:
            raise RuntimeError(
                f"Port {port} is still in use after waiting 30s. "
                f"Another process may be occupying it."
            )
        logger.info(f"Port {port} is now available")

    engine_loop = asyncio.get_running_loop()

    app = create_app(
        agent=agent,
        shutdown_event=shutdown_event,
        session_manager=session_manager,
        gateway=gateway,
        orchestrator=orchestrator,
        agent_pool=agent_pool,
    )
    app.state.engine_loop = engine_loop
    app.state.actual_bind_host = host
    app.state.actual_bind_port = port

    # Sprint 15 / v32 Phase B Task C hypothesis fix (forensics in
    # ``_v32_biz/_phase_b_hang_rca.md``): uvicorn's default
    # ``timeout_graceful_shutdown`` is ``None`` (= infinite wait for
    # keep-alive HTTP / WebSocket clients to close). Combined with
    # operator browsers / SSE clients / proxy tunnels that hold
    # connections open, this can easily account for the ~13s lifespan
    # → process exit hang v31 forensics observed. ``3.0s`` gives any
    # well-behaved client a clean window to drain and then forces
    # the close. ``0`` disables the cap to recover uvicorn's default.
    try:
        from openakita.config import settings as _serve_settings

        graceful_s = float(
            getattr(_serve_settings, "uvicorn_graceful_shutdown_timeout_s", 3.0)
            or 0.0
        )
    except Exception:
        graceful_s = 3.0

    uvicorn_kwargs: dict[str, Any] = {
        "app": app,
        "host": host,
        "port": port,
        "log_level": "warning",
        "access_log": False,
        "http": "h11",
        "log_config": None,
    }
    if graceful_s > 0:
        uvicorn_kwargs["timeout_graceful_shutdown"] = int(graceful_s)

    config = uvicorn.Config(**uvicorn_kwargs)
    server = uvicorn.Server(config)

    # ── Launch uvicorn in a background thread ────────────────────────
    api_loop_holder: list[asyncio.AbstractEventLoop] = []
    thread_ready = threading.Event()
    thread_error: list[Exception] = []

    def _api_thread() -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            api_loop_holder.append(loop)
            thread_ready.set()
            loop.run_until_complete(server.serve())
        except Exception as exc:
            thread_error.append(exc)
        finally:
            thread_ready.set()
            try:
                loop = api_loop_holder[0] if api_loop_holder else None
                if loop and not loop.is_closed():
                    loop.close()
            except Exception:
                pass

    api_thread = threading.Thread(
        target=_api_thread,
        daemon=True,
        name="openakita-api",
    )
    api_thread.start()

    await asyncio.to_thread(thread_ready.wait)

    if thread_error:
        raise RuntimeError(f"API thread failed to start: {thread_error[0]}")

    api_loop = api_loop_holder[0] if api_loop_holder else None

    # ── Register loops for the cross-loop bridge ─────────────────────
    from openakita.core.engine_bridge import set_api_loop, set_engine_loop

    set_engine_loop(engine_loop)
    if api_loop is not None:
        set_api_loop(api_loop)

    from openakita import get_version_string

    logger.info(
        f"HTTP API server starting on http://{host}:{port} "
        f"(version: {get_version_string()}, dual-loop: {api_loop is not None})"
    )

    # ── Verify server is listening ───────────────────────────────────
    for attempt in range(max_retries):
        await asyncio.sleep(1.5)

        if not api_thread.is_alive():
            err = thread_error[0] if thread_error else RuntimeError("API thread died")
            raise RuntimeError(f"HTTP API server failed to start: {err}")

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1.0)
                s.connect((host, port))
                logger.info(
                    f"HTTP API server confirmed listening on http://{host}:{port} "
                    f"(thread={api_thread.name})"
                )
                break
        except (ConnectionRefusedError, OSError, TimeoutError):
            if attempt < max_retries - 1:
                logger.debug(f"Server not yet listening (attempt {attempt + 1}), waiting...")
                continue

    # ── Proxy task — cancelling it triggers graceful shutdown ─────────
    async def _proxy() -> None:
        try:
            while api_thread.is_alive():
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            logger.info("API proxy task cancelled, shutting down uvicorn...")
            server.should_exit = True
            await asyncio.to_thread(api_thread.join, 5.0)

    proxy_task = asyncio.create_task(_proxy())
    # Keep a handle to the app so the serve process can update late-bound
    # runtime references such as the IM gateway after HTTP is already online.
    proxy_task._openakita_api_app = app
    return proxy_task


def _refresh_node_tool_host(app: FastAPI) -> None:
    """(Re)bind a :class:`NodeToolHost` on the org runtime if possible.

    Sprint-6 P0-1 (RCA ``_v17_p1_rca.md`` §1.5): the host wraps the
    desktop Agent's populated ``handler_registry`` so orgs_v2 node
    tools dispatch to real handlers instead of the empty global
    registry the Sprint-5 commit aimed at. This helper is idempotent:
    multiple lifespan paths (``create_app`` initial bind, ``update_agent``
    late bind, ``update_runtime_refs`` IM-gateway late bind) all
    converge here, and each rebind disposes the previous host so the
    source-agent reference is released for a clean rebuild on hot
    reload.
    """

    agent = getattr(app.state, "agent", None)
    rt = getattr(app.state, "org_runtime", None)
    if rt is None:
        return
    setter = getattr(rt, "set_node_tool_host", None)
    if not callable(setter):
        return
    if agent is None:
        setter(None)
        return
    try:
        from openakita.orgs._runtime_agent_host import build_node_tool_host
    except Exception:  # noqa: BLE001 -- defensive against import-cycle
        logger.debug(
            "Could not import build_node_tool_host; skipping bind", exc_info=True
        )
        return
    host = build_node_tool_host(agent=agent)
    setter(host)


def update_agent(app: FastAPI, agent: Any) -> None:
    """Update the agent reference in the running app (e.g. after initialization)."""
    _attach_agent_to_app(app, agent)
    # Sprint-6 P0-1: rebind the orgs_v2 NodeToolHost to the new agent
    # so any subsequent node activation sees the populated registry.
    _refresh_node_tool_host(app)


def update_runtime_refs(
    api_task: asyncio.Task | None,
    *,
    agent: Any = None,
    session_manager: Any = None,
    gateway: Any = None,
    orchestrator: Any = None,
    agent_pool: Any = None,
    startup_phase: str | None = None,
    readiness: dict[str, Any] | None = None,
) -> bool:
    """Update runtime references on an API server started by start_api_server().

    Some dependencies, especially IM channels, may intentionally start after
    the HTTP API so desktop clients can connect early. This keeps routes that
    read ``request.app.state`` in sync once those late dependencies are ready.
    """
    app = getattr(api_task, "_openakita_api_app", None) if api_task is not None else None
    if app is None:
        return False
    if agent is not None:
        _attach_agent_to_app(app, agent)
        # Sprint-6 P0-1: rebind the orgs_v2 NodeToolHost so the next
        # node activation picks up the freshly-installed agent's
        # populated handler registry instead of the empty global.
        _refresh_node_tool_host(app)
    if session_manager is not None:
        app.state.session_manager = session_manager
        org_command_service = getattr(app.state, "org_command_service", None)
        if org_command_service is not None and hasattr(org_command_service, "_session_manager"):
            org_command_service._session_manager = session_manager
    if gateway is not None:
        app.state.gateway = gateway
    if orchestrator is not None:
        app.state.orchestrator = orchestrator
    if agent_pool is not None:
        app.state.agent_pool = agent_pool
    if startup_phase is not None:
        app.state.startup_phase = startup_phase
    if readiness is not None:
        app.state.readiness = readiness
    return True
