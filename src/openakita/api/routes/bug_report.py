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
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

logger = logging.getLogger(__name__)

router = APIRouter()

# Cloudflare Worker endpoint (overridden by config)
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
    "browser-use",
    "chromadb",
    "sentence-transformers",
    "lark-oapi",
    "python-telegram-bot",
    "dingtalk-stream",
]

LOG_TAIL_BYTES = 1 * 1024 * 1024  # last 1 MB of main log
MAX_ZIP_SIZE = 30 * 1024 * 1024  # 30 MB


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

    # Git availability (common cause of [WinError 2])
    try:
        import subprocess
        result = subprocess.run(
            ["git", "--version"], capture_output=True, text=True, timeout=5,
        )
        info["git_version"] = result.stdout.strip() if result.returncode == 0 else f"error: {result.stderr.strip()}"
    except FileNotFoundError:
        info["git_version"] = "NOT FOUND (git not in PATH)"
    except Exception as e:
        info["git_version"] = f"error: {e}"

    # Node/npm availability
    for cmd in ["node", "npm"]:
        try:
            import subprocess
            result = subprocess.run(
                [cmd, "--version"], capture_output=True, text=True, timeout=5,
            )
            info[f"{cmd}_version"] = result.stdout.strip() if result.returncode == 0 else "error"
        except FileNotFoundError:
            info[f"{cmd}_version"] = "NOT FOUND"
        except Exception:
            info[f"{cmd}_version"] = "unknown"

    # Configured endpoints count
    try:
        from openakita.config import settings
        from openakita.llm.client import LLMClient
        info["endpoints_count"] = len(getattr(LLMClient, "_endpoints", []))
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


@router.get("/api/system-info")
async def get_system_info():
    """Return system environment information for display in the bug report form."""
    return _collect_system_info()


async def _upload_to_worker(
    *,
    report_id: str,
    report_type: str,
    title: str,
    summary: str,
    extra_info: str,
    turnstile_token: str,
    zip_bytes: bytes,
) -> dict:
    """Upload a zip package to the Cloudflare Worker. Shared by bug report and feature request."""
    endpoint = _get_bug_report_endpoint()
    if not endpoint:
        raise HTTPException(status_code=503, detail="Bug report endpoint not configured")

    if len(zip_bytes) > MAX_ZIP_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"Package too large: {len(zip_bytes) / 1024 / 1024:.1f} MB (max 30 MB)",
        )

    try:
        import httpx

        upload_url = f"{endpoint.rstrip('/')}/report/{report_id}"

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.put(
                upload_url,
                content=zip_bytes,
                headers={
                    "Content-Type": "application/zip",
                    "X-Report-Title": quote(title[:200], safe=""),
                    "X-Report-Type": report_type,
                    "X-Report-Summary": quote(summary[:2000], safe=""),
                    "X-Report-System-Info": quote(extra_info[:2000], safe=""),
                    "X-Turnstile-Token": turnstile_token,
                },
            )

        if resp.status_code == 429:
            raise HTTPException(status_code=429, detail="Rate limit reached, please try again later")
        if resp.status_code == 403:
            raise HTTPException(status_code=403, detail="Verification failed")
        if resp.status_code >= 400:
            logger.error(f"Report upload failed: {resp.status_code} {resp.text}")
            raise HTTPException(status_code=502, detail=f"Cloud service error: {resp.status_code}")

        return {"status": "ok", "report_id": report_id, "size_bytes": len(zip_bytes)}

    except httpx.HTTPError as e:
        logger.error(f"Report upload error: {e}")
        raise HTTPException(status_code=502, detail=f"Upload failed: {e}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Report unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


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
    turnstile_token: str = Form(...),
    steps: str = Form(""),
    upload_logs: bool = Form(True),
    upload_debug: bool = Form(True),
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
        metadata = {
            "report_id": report_id,
            "type": "bug",
            "title": title,
            "description": description,
            "steps": steps,
            "system_info": sys_info,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        zf.writestr("metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2))

        await _pack_images(zf, images)

        if upload_logs:
            try:
                from openakita.config import settings
                main_log = settings.log_file_path
                error_log = settings.error_log_path
            except Exception:
                main_log = Path.cwd() / "logs" / "openakita.log"
                error_log = Path.cwd() / "logs" / "error.log"

            log_data = _tail_file(main_log, LOG_TAIL_BYTES)
            if log_data:
                zf.writestr("logs/openakita.log", log_data)
            err_data = _tail_file(error_log, LOG_TAIL_BYTES)
            if err_data:
                zf.writestr("logs/error.log", err_data)

        if upload_debug:
            for df in _get_recent_llm_debug_files(50):
                try:
                    zf.write(df, f"llm_debug/{df.name}")
                except Exception:
                    pass

    sys_info_brief = f"OS: {sys_info.get('os', '?')} | Python: {sys_info.get('python', '?')} | OpenAkita: {sys_info.get('openakita_version', '?')}"
    return await _upload_to_worker(
        report_id=report_id,
        report_type="bug",
        title=title,
        summary=description,
        extra_info=sys_info_brief,
        turnstile_token=turnstile_token,
        zip_bytes=buf.getvalue(),
    )


@router.post("/api/feature-request")
async def submit_feature_request(
    title: str = Form(...),
    description: str = Form(...),
    turnstile_token: str = Form(...),
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

    return await _upload_to_worker(
        report_id=report_id,
        report_type="feature",
        title=title,
        summary=description,
        extra_info=contact_brief,
        turnstile_token=turnstile_token,
        zip_bytes=buf.getvalue(),
    )
