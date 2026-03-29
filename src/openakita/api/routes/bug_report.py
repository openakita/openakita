"""
Feedback routes: GET /api/system-info, POST /api/bug-report, POST /api/feature-request

用户反馈收集端点（错误报告 + 需求建议）。打包为 zip 上传到云端。
"""

from __future__ import annotations

import io
import json
import logging
import platform
import shutil
import time
import uuid
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)

router = APIRouter()

FEEDBACK_TEMP_DIR: Path | None = None

_BUG_REPORT_ENDPOINT: str = ""

KEY_PACKAGES = [
    "anthropic",
    "openai",
    "httpx",
    "fastapi",
    "uvicorn",
    "pydantic",
    "mcp",
    "playwright",
    "chromadb",
    "sentence-transformers",
    "lark-oapi",
    "python-telegram-bot",
    "dingtalk-stream",
]

LOG_TAIL_BYTES = 1 * 1024 * 1024  # last 1 MB of main log
FRONTEND_LOG_TAIL_BYTES = 512 * 1024  # last 512 KB of frontend log
MAX_ZIP_SIZE = 30 * 1024 * 1024  # 30 MB
RECENT_DAYS = 3  # how far back to collect dated files
DIR_MAX_BYTES = 5 * 1024 * 1024  # default per-directory byte budget


def _get_bug_report_endpoint() -> str:
    global _BUG_REPORT_ENDPOINT
    if not _BUG_REPORT_ENDPOINT:
        try:
            from openakita.config import settings
            _BUG_REPORT_ENDPOINT = getattr(settings, "bug_report_endpoint", "")
        except Exception:
            pass
    return _BUG_REPORT_ENDPOINT


def _collect_system_info() -> dict:
    """Collect system environment information."""
    import sys

    info: dict = {
        "os": f"{platform.system()} {platform.release()} {platform.machine()}",
        "os_detail": platform.platform(),
        "python": platform.python_version(),
        "python_impl": platform.python_implementation(),
        "python_executable": sys.executable,
        "arch": platform.machine(),
    }

    # OpenAkita version
    try:
        from openakita import get_version_string
        info["openakita_version"] = get_version_string()
    except Exception:
        info["openakita_version"] = "unknown"

    # Key package versions
    packages: dict[str, str] = {}
    try:
        from importlib.metadata import version as get_pkg_version
        for pkg in KEY_PACKAGES:
            try:
                packages[pkg] = get_pkg_version(pkg)
            except Exception:
                pass
    except ImportError:
        pass
    info["packages"] = packages

    # pip list (all installed packages for full reproducibility)
    try:
        from importlib.metadata import distributions
        info["pip_packages"] = {
            d.metadata["Name"]: d.metadata["Version"]
            for d in distributions()
            if d.metadata["Name"]
        }
    except Exception:
        pass

    # Memory
    try:
        import psutil
        mem = psutil.virtual_memory()
        info["memory_total_gb"] = round(mem.total / (1024 ** 3), 1)
        info["memory_available_gb"] = round(mem.available / (1024 ** 3), 1)
    except ImportError:
        pass

    # Disk
    try:
        from openakita.config import settings
        usage = shutil.disk_usage(settings.project_root)
        info["disk_free_gb"] = round(usage.free / (1024 ** 3), 1)
    except Exception:
        try:
            usage = shutil.disk_usage(Path.cwd())
            info["disk_free_gb"] = round(usage.free / (1024 ** 3), 1)
        except Exception:
            pass

    # subprocess flags: hide console window on Windows
    import subprocess
    _sp_kwargs: dict = {}
    if platform.system() == "Windows":
        _sp_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    # Git availability (common cause of [WinError 2])
    try:
        result = subprocess.run(
            ["git", "--version"], capture_output=True, text=True, timeout=5,
            **_sp_kwargs,
        )
        info["git_version"] = result.stdout.strip() if result.returncode == 0 else f"error: {result.stderr.strip()}"
    except FileNotFoundError:
        info["git_version"] = "NOT FOUND (git not in PATH)"
    except Exception as e:
        info["git_version"] = f"error: {e}"

    # Node/npm availability
    for cmd in ["node", "npm"]:
        try:
            result = subprocess.run(
                [cmd, "--version"], capture_output=True, text=True, timeout=5,
                **_sp_kwargs,
            )
            info[f"{cmd}_version"] = result.stdout.strip() if result.returncode == 0 else "error"
        except FileNotFoundError:
            info[f"{cmd}_version"] = "NOT FOUND"
        except Exception:
            info[f"{cmd}_version"] = "unknown"

    # Configured endpoints count
    try:
        from openakita.llm.config import get_default_config_path, load_endpoints_config
        config_path = get_default_config_path()
        if config_path.exists():
            eps, compiler_eps, stt_eps, _ = load_endpoints_config(config_path)
            info["endpoints_count"] = len(eps)
            info["compiler_endpoints_count"] = len(compiler_eps)
            info["stt_endpoints_count"] = len(stt_eps)
        else:
            info["endpoints_count"] = 0
    except Exception:
        pass

    # Project root path
    try:
        from openakita.config import settings
        info["project_root"] = str(settings.project_root)
    except Exception:
        pass

    # IM channels
    try:
        from openakita.config import settings
        channels = []
        if getattr(settings, "telegram_enabled", False):
            channels.append("telegram")
        if getattr(settings, "feishu_enabled", False):
            channels.append("feishu")
        if getattr(settings, "wework_enabled", False):
            channels.append("wework")
        if getattr(settings, "dingtalk_enabled", False):
            channels.append("dingtalk")
        if getattr(settings, "onebot_enabled", False):
            channels.append("onebot")
        if getattr(settings, "qqbot_enabled", False):
            channels.append("qqbot")
        if getattr(settings, "wechat_enabled", False):
            channels.append("wechat")
        info["im_channels"] = channels
    except Exception:
        pass

    # PATH environment variable (useful for diagnosing "command not found")
    import os
    info["path_env"] = os.environ.get("PATH", "")

    return info


def _tail_file(filepath: Path, max_bytes: int) -> bytes:
    """Read the tail of a file up to max_bytes."""
    if not filepath.exists() or not filepath.is_file():
        return b""
    size = filepath.stat().st_size
    with open(filepath, "rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
        return f.read()


def _get_recent_llm_debug_files(count: int = 20) -> list[Path]:
    """Get the most recent llm_debug files sorted by modification time."""
    try:
        from openakita.config import settings
        debug_dir = settings.project_root / "data" / "llm_debug"
    except Exception:
        debug_dir = Path.cwd() / "data" / "llm_debug"

    if not debug_dir.exists():
        return []

    files = sorted(debug_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:count]


def _resolve_data_dir() -> Path:
    """Return the workspace data/ directory."""
    try:
        from openakita.config import settings
        return settings.data_dir
    except Exception:
        return Path.cwd() / "data"


def _resolve_global_logs_dir() -> Path:
    """Return the global logs directory (Tauri-managed, under openakita_home).

    Respects custom root via OPENAKITA_ROOT env var or settings.openakita_home."""
    try:
        from openakita.config import settings
        return settings.openakita_home / "logs"
    except Exception:
        import os
        root = os.environ.get("OPENAKITA_ROOT", "").strip()
        return Path(root) / "logs" if root else Path.home() / ".openakita" / "logs"


def _recent_files(
    directory: Path,
    days: int = RECENT_DAYS,
    patterns: tuple[str, ...] = ("*",),
    max_total_bytes: int = DIR_MAX_BYTES,
) -> list[Path]:
    """Collect the most recent files from *directory* within *days*,
    respecting a cumulative byte budget. Files sorted newest-first."""
    if not directory.exists():
        return []
    cutoff = time.time() - days * 86400
    files: list[Path] = []
    for pat in patterns:
        for p in directory.rglob(pat):
            if p.is_file():
                try:
                    if p.stat().st_mtime >= cutoff:
                        files.append(p)
                except OSError:
                    pass
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    selected: list[Path] = []
    total = 0
    for f in files:
        sz = f.stat().st_size
        if total + sz > max_total_bytes:
            continue
        selected.append(f)
        total += sz
    return selected


def _add_dir_recent(
    zf: zipfile.ZipFile,
    directory: Path,
    zip_prefix: str,
    *,
    days: int = RECENT_DAYS,
    patterns: tuple[str, ...] = ("*",),
    max_total_bytes: int = DIR_MAX_BYTES,
) -> None:
    """Add recent files from a directory into the zip under *zip_prefix*."""
    for f in _recent_files(directory, days, patterns, max_total_bytes):
        try:
            rel = f.relative_to(directory)
            zf.write(f, f"{zip_prefix}/{rel.as_posix()}")
        except Exception:
            pass


def _add_file(zf: zipfile.ZipFile, path: Path, zip_name: str) -> None:
    """Add a single file to the zip if it exists."""
    if path.exists() and path.is_file():
        try:
            zf.write(path, zip_name)
        except Exception:
            pass


_SENSITIVE_KEY_RE = None


def _collect_sanitized_config() -> dict:
    """Collect non-sensitive .env config and runtime state for diagnostics.

    Keys whose names match common secret patterns are redacted."""
    import os
    import re

    global _SENSITIVE_KEY_RE
    if _SENSITIVE_KEY_RE is None:
        _SENSITIVE_KEY_RE = re.compile(
            r"(key|secret|token|password|credential|auth|apikey|api_key)", re.IGNORECASE,
        )

    sanitized: dict = {}
    for k, v in sorted(os.environ.items()):
        if not k.startswith(("OPENAKITA", "ANTHROPIC", "OPENAI", "FEISHU",
                             "TELEGRAM", "DINGTALK", "WEWORK", "WECHAT",
                             "ONEBOT", "QQ")):
            continue
        sanitized[k] = "***" if _SENSITIVE_KEY_RE.search(k) else v

    runtime_path = _resolve_data_dir() / "runtime_state.json"
    if runtime_path.exists():
        try:
            sanitized["_runtime_state"] = json.loads(runtime_path.read_text("utf-8"))
        except Exception:
            pass

    return sanitized


@router.get("/api/system-info")
async def get_system_info():
    """Return system environment information for display in the bug report form."""
    return _collect_system_info()


@router.get("/api/feedback-config")
async def get_feedback_config():
    """Return public-facing feedback configuration (CAPTCHA identifiers etc.).

    These are NOT secrets — they're deployment-specific public identifiers that
    the frontend needs at runtime. Served via API so nothing is hardcoded in
    the open-source frontend bundle.
    """
    try:
        from openakita.config import settings
        return {
            "captcha_scene_id": getattr(settings, "captcha_scene_id", ""),
            "captcha_prefix": getattr(settings, "captcha_prefix", ""),
        }
    except Exception:
        return {"captcha_scene_id": "", "captcha_prefix": ""}


async def _upload_to_worker(
    *,
    report_id: str,
    report_type: str,
    title: str,
    summary: str,
    extra_info: str,
    captcha_verify_param: str,
    zip_bytes: bytes,
) -> dict:
    """Upload feedback via pre-signed URL direct upload (three-phase flow).

    Phase 1: POST /prepare  → FC validates captcha, rate-limits, returns pre-signed URL
    Phase 2: PUT <url>      → Upload ZIP directly to OSS (bypasses FC)
    Phase 3: POST /complete → FC verifies upload, creates GitHub Issue
    """
    endpoint = _get_bug_report_endpoint()
    if not endpoint:
        raise HTTPException(status_code=503, detail="Bug report endpoint not configured")

    if len(zip_bytes) > MAX_ZIP_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"Package too large: {len(zip_bytes) / 1024 / 1024:.1f} MB (max 30 MB)",
        )

    import httpx

    base = endpoint.rstrip("/")

    try:
        async with httpx.AsyncClient() as client:
            # Phase 1: request pre-signed upload URL from FC
            prepare_resp = await client.post(
                f"{base}/prepare",
                json={
                    "report_id": report_id,
                    "title": title[:200],
                    "type": report_type,
                    "summary": summary[:2000],
                    "system_info": extra_info[:2000],
                    "captcha_verify_param": captcha_verify_param,
                },
                timeout=15,
            )

            if prepare_resp.status_code == 429:
                raise HTTPException(status_code=429, detail="Rate limit reached, please try later")
            if prepare_resp.status_code == 403:
                raise HTTPException(status_code=403, detail="Verification failed")
            if prepare_resp.status_code >= 400:
                try:
                    detail = prepare_resp.json().get("error", prepare_resp.text[:200])
                except Exception:
                    detail = prepare_resp.text[:200]
                logger.error("Prepare failed: %s %s", prepare_resp.status_code, detail)
                raise HTTPException(status_code=502, detail=f"Cloud service error: {detail}")

            prepare_data = prepare_resp.json()
            upload_url = prepare_data["upload_url"]
            report_date = prepare_data["report_date"]

            # Phase 2: upload ZIP directly to OSS via pre-signed URL
            #   No Content-Type header — must match the (empty) Content-Type
            #   used during pre-signed URL signing, otherwise OSS returns 403.
            oss_resp = await client.put(
                upload_url,
                content=zip_bytes,
                timeout=120,
            )
            if oss_resp.status_code >= 400:
                oss_err = oss_resp.text[:1500]
                logger.error(
                    "OSS direct upload failed: status=%s body=%s url=%s",
                    oss_resp.status_code, oss_err, upload_url[:120],
                )
                raise HTTPException(
                    status_code=502,
                    detail=f"Direct upload to storage failed ({oss_resp.status_code})",
                )

            # Phase 3: notify FC that upload is complete → creates GitHub Issue
            complete_resp = await client.post(
                f"{base}/complete/{report_id}",
                json={"report_date": report_date},
                timeout=30,
            )
            issue_url = None
            if complete_resp.status_code == 200:
                issue_url = complete_resp.json().get("issue_url")
            else:
                logger.warning(
                    "Complete phase returned %s (non-fatal)", complete_resp.status_code,
                )

        return {
            "status": "ok",
            "report_id": report_id,
            "size_bytes": len(zip_bytes),
            "issue_url": issue_url,
        }

    except httpx.HTTPError as e:
        logger.error("Report upload error: %s", e)
        raise HTTPException(status_code=502, detail=f"Upload failed: {e}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Report unexpected error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


def _feedback_temp_dir() -> Path:
    global FEEDBACK_TEMP_DIR
    if FEEDBACK_TEMP_DIR is None:
        try:
            from openakita.config import settings
            FEEDBACK_TEMP_DIR = settings.project_root / "temp-feedback"
        except Exception:
            FEEDBACK_TEMP_DIR = Path.cwd() / "temp-feedback"
    FEEDBACK_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    return FEEDBACK_TEMP_DIR


def _save_zip_locally(report_id: str, zip_bytes: bytes) -> Path:
    """Save a feedback zip to a local temp directory and return the file path."""
    out = _feedback_temp_dir() / f"{report_id}.zip"
    out.write_bytes(zip_bytes)
    return out


async def _try_upload_or_save(
    *,
    report_id: str,
    report_type: str,
    title: str,
    summary: str,
    extra_info: str,
    captcha_verify_param: str,
    zip_bytes: bytes,
) -> dict:
    """Try uploading to the cloud function. On failure, save locally and return
    a response that tells the frontend about the local fallback."""
    try:
        result = await _upload_to_worker(
            report_id=report_id,
            report_type=report_type,
            title=title,
            summary=summary,
            extra_info=extra_info,
            captcha_verify_param=captcha_verify_param,
            zip_bytes=zip_bytes,
        )
        return result
    except HTTPException as exc:
        local_path = _save_zip_locally(report_id, zip_bytes)
        logger.warning(
            "Cloud upload failed (%s %s), saved locally: %s",
            exc.status_code, exc.detail, local_path,
        )
        return {
            "status": "upload_failed",
            "report_id": report_id,
            "error": f"{exc.status_code}: {exc.detail}",
            "local_path": str(local_path),
            "download_url": f"/api/feedback-download/{report_id}",
        }
    except Exception as exc:
        local_path = _save_zip_locally(report_id, zip_bytes)
        logger.warning(
            "Cloud upload failed (%s), saved locally: %s", exc, local_path,
        )
        return {
            "status": "upload_failed",
            "report_id": report_id,
            "error": str(exc),
            "local_path": str(local_path),
            "download_url": f"/api/feedback-download/{report_id}",
        }


@router.get("/api/feedback-download/{report_id}")
async def download_feedback_package(report_id: str):
    """Download a locally saved feedback zip package."""
    safe_id = "".join(c for c in report_id if c.isalnum())
    path = _feedback_temp_dir() / f"{safe_id}.zip"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"openakita-feedback-{safe_id}.zip",
    )


async def _pack_images(zf: zipfile.ZipFile, images: list[UploadFile] | None) -> None:
    """Write uploaded images into a zip file."""
    if not images:
        return
    for i, img in enumerate(images[:10]):
        content = await img.read()
        ext = Path(img.filename or "image").suffix or ".png"
        zf.writestr(f"images/{i:02d}_{img.filename or f'image{ext}'}", content)


@router.post("/api/bug-report")
async def submit_bug_report(
    title: str = Form(...),
    description: str = Form(...),
    captcha_verify_param: str = Form("none"),
    steps: str = Form(""),
    upload_logs: bool = Form(True),
    upload_debug: bool = Form(True),
    contact_email: str = Form(""),
    contact_wechat: str = Form(""),
    images: list[UploadFile] | None = File(None),  # noqa: B008
):
    """Submit a bug report with system info, logs, and LLM debug files."""
    if len(title) < 2 or len(title) > 200:
        raise HTTPException(status_code=400, detail="标题需要 2-200 个字符")
    if len(description) < 2:
        raise HTTPException(status_code=400, detail="请填写「错误描述」字段（标题下方的文本框）")

    report_id = uuid.uuid4().hex[:12]
    sys_info = _collect_system_info()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        metadata: dict = {
            "report_id": report_id,
            "type": "bug",
            "title": title,
            "description": description,
            "steps": steps,
            "system_info": sys_info,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        if contact_email or contact_wechat:
            metadata["contact"] = {
                "email": contact_email,
                "wechat": contact_wechat,
            }
        zf.writestr("metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2))

        await _pack_images(zf, images)

        if upload_logs:
            try:
                from openakita.config import settings
                main_log = settings.log_file_path
                error_log = settings.error_log_path
                logs_dir = settings.log_dir_path
            except Exception:
                logs_dir = Path.cwd() / "logs"
                main_log = logs_dir / "openakita.log"
                error_log = logs_dir / "error.log"

            log_data = _tail_file(main_log, LOG_TAIL_BYTES)
            if log_data:
                zf.writestr("logs/openakita.log", log_data)
            err_data = _tail_file(error_log, LOG_TAIL_BYTES)
            if err_data:
                zf.writestr("logs/error.log", err_data)
            serve_data = _tail_file(logs_dir / "openakita-serve.log", LOG_TAIL_BYTES)
            if serve_data:
                zf.writestr("logs/openakita-serve.log", serve_data)

            # frontend.log lives in the global ~/.openakita/logs/ dir (Tauri-managed)
            global_logs = _resolve_global_logs_dir()
            fe_data = _tail_file(global_logs / "frontend.log", FRONTEND_LOG_TAIL_BYTES)
            if fe_data:
                zf.writestr("logs/frontend.log", fe_data)
            crash_data = _tail_file(global_logs / "crash.log", FRONTEND_LOG_TAIL_BYTES)
            if crash_data:
                zf.writestr("logs/crash.log", crash_data)

            # Multi-agent delegation logs (recent 3 days, max 2 MB)
            data_dir = _resolve_data_dir()
            _add_dir_recent(
                zf, data_dir / "delegation_logs", "delegation_logs",
                patterns=("*.jsonl",), max_total_bytes=2 * 1024 * 1024,
            )

        if upload_debug:
            data_dir = _resolve_data_dir()
            for df in _get_recent_llm_debug_files(50):
                try:
                    zf.write(df, f"llm_debug/{df.name}")
                except Exception:
                    pass
            # ReAct reasoning traces (recent 3 days, max 5 MB)
            _add_dir_recent(
                zf, data_dir / "react_traces", "react_traces",
                patterns=("*.json",), max_total_bytes=5 * 1024 * 1024,
            )
            # Agent traces (recent 3 days, max 2 MB)
            _add_dir_recent(
                zf, data_dir / "traces", "traces",
                patterns=("*.json",), max_total_bytes=2 * 1024 * 1024,
            )
            # Orchestration org events (recent 3 days, max 2 MB)
            _add_dir_recent(
                zf, data_dir / "orgs", "orgs",
                patterns=("*.jsonl", "*.md"), max_total_bytes=2 * 1024 * 1024,
            )
            # Tool output overflow (recent 3 days, max 2 MB)
            _add_dir_recent(
                zf, data_dir / "tool_overflow", "tool_overflow",
                patterns=("*.txt",), max_total_bytes=2 * 1024 * 1024,
            )
            # Failure analysis reports (recent 3 days, max 1 MB)
            _add_dir_recent(
                zf, data_dir / "failure_analysis", "failure_analysis",
                max_total_bytes=1 * 1024 * 1024,
            )
            # Task retrospects (recent 3 days, max 1 MB)
            _add_dir_recent(
                zf, data_dir / "retrospects", "retrospects",
                patterns=("*.jsonl",), max_total_bytes=1 * 1024 * 1024,
            )
            # Small state files — always include
            _add_file(zf, data_dir / "runtime_state.json", "state/runtime_state.json")
            _add_file(zf, data_dir / "sub_agent_states.json", "state/sub_agent_states.json")
            _add_file(zf, data_dir / "backend.heartbeat", "state/backend.heartbeat")
            _add_file(zf, data_dir / "sessions" / "sessions.json", "state/sessions.json")
            _add_file(
                zf, data_dir / "sessions" / "channel_registry.json",
                "state/channel_registry.json",
            )
            _add_file(zf, data_dir / "scheduler" / "tasks.json", "state/scheduler_tasks.json")
            _add_file(
                zf, data_dir / "scheduler" / "executions.json",
                "state/scheduler_executions.json",
            )
            # Sanitized config snapshot
            try:
                config_snapshot = _collect_sanitized_config()
                if config_snapshot:
                    zf.writestr(
                        "state/sanitized_config.json",
                        json.dumps(config_snapshot, ensure_ascii=False, indent=2),
                    )
            except Exception:
                pass

    sys_info_brief = f"OS: {sys_info.get('os', '?')} | Python: {sys_info.get('python', '?')} | OpenAkita: {sys_info.get('openakita_version', '?')}"
    return await _try_upload_or_save(
        report_id=report_id,
        report_type="bug",
        title=title,
        summary=description,
        extra_info=sys_info_brief,
        captcha_verify_param=captcha_verify_param,
        zip_bytes=buf.getvalue(),
    )


@router.post("/api/feature-request")
async def submit_feature_request(
    title: str = Form(...),
    description: str = Form(...),
    captcha_verify_param: str = Form("none"),
    contact_email: str = Form(""),
    contact_wechat: str = Form(""),
    images: list[UploadFile] | None = File(None),  # noqa: B008
):
    """Submit a feature/requirement request with optional contact info and attachments."""
    if len(title) < 2 or len(title) > 200:
        raise HTTPException(status_code=400, detail="需求名称需要 2-200 个字符")
    if len(description) < 2:
        raise HTTPException(status_code=400, detail="请填写「需求描述」字段")

    report_id = uuid.uuid4().hex[:12]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        metadata = {
            "report_id": report_id,
            "type": "feature",
            "title": title,
            "description": description,
            "contact": {
                "email": contact_email,
                "wechat": contact_wechat,
            },
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        zf.writestr("metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2))
        await _pack_images(zf, images)

    contact_brief = " | ".join(
        f for f in [
            f"Email: {contact_email}" if contact_email else "",
            f"WeChat: {contact_wechat}" if contact_wechat else "",
        ] if f
    ) or "(no contact)"

    return await _try_upload_or_save(
        report_id=report_id,
        report_type="feature",
        title=title,
        summary=description,
        extra_info=contact_brief,
        captcha_verify_param=captcha_verify_param,
        zip_bytes=buf.getvalue(),
    )
