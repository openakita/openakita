"""
Setup Center Bridge

该模块用于给 Setup Center（Tauri App）提供一个稳定的 Python 入口：

- `python -m openakita.setup_center.bridge list-providers`
- `python -m openakita.setup_center.bridge list-models --api-type ... --base-url ... [--provider-slug ...]`
- `python -m openakita.setup_center.bridge list-skills --workspace-dir ...`

输出均为 JSON（stdout），错误输出到 stderr 并以非 0 退出码返回。
"""

from __future__ import annotations

import openakita._ensure_utf8  # noqa: F401  # isort: skip

import argparse
import asyncio
import json
import os
import re
import sys
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

    def _minimax_fallback_models() -> list[dict]:
        # MiniMax Anthropic/OpenAI 兼容文档仅列出固定模型，且未提供 /models 列表接口。
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

    # MiniMax 兼容接口无模型列表端点，直接返回文档内置候选，避免无效探测和误报。
    if _is_minimax_provider():
        return _minimax_fallback_models()

    url = base_url.rstrip("/") + "/models"
    # 本地服务（Ollama/LM Studio 等）不需要真实 API Key，使用 placeholder
    effective_key = api_key.strip() or "local"
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(url, headers={"Authorization": f"Bearer {effective_key}"})
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
                "name": mid,
                "capabilities": infer_capabilities(mid, provider_slug=provider_slug),
            }
        )
    out.sort(key=lambda x: x["id"])
    return out


async def _list_models_anthropic(api_key: str, base_url: str, provider_slug: str | None) -> list[dict]:
    import httpx

    from openakita.llm.capabilities import infer_capabilities

    def _is_minimax_provider() -> bool:
        slug = (provider_slug or "").strip().lower()
        b = (base_url or "").strip().lower()
        return slug in {"minimax", "minimax-cn", "minimax-int"} or "minimax" in b or "minimaxi" in b

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

    # MiniMax 兼容接口无模型列表端点，直接返回文档内置候选，避免无效探测和误报。
    if _is_minimax_provider():
        return _minimax_fallback_models()

    b = base_url.rstrip("/")
    url = b + "/models" if b.endswith("/v1") else b + "/v1/models"

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(
                url,
                headers={
                    "x-api-key": api_key,
                    # 部分 Anthropic 兼容网关仅识别 Bearer。
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


async def list_models(api_type: str, base_url: str, provider_slug: str | None, api_key: str) -> None:
    api_type = (api_type or "").strip().lower()
    base_url = (base_url or "").strip()
    if not api_type:
        raise ValueError("--api-type 不能为空")
    if not base_url:
        raise ValueError("--base-url 不能为空")
    # 本地服务商（Ollama/LM Studio 等）不需要 API Key，允许空值
    # 前端会传入 placeholder key，但也兼容完全为空的情况

    if api_type == "openai":
        _json_print(await _list_models_openai(api_key, base_url, provider_slug))
        return
    if api_type == "anthropic":
        _json_print(await _list_models_anthropic(api_key, base_url, provider_slug))
        return

    raise ValueError(f"不支持的 api-type: {api_type}")


async def health_check_endpoint(workspace_dir: str, endpoint_name: str | None) -> None:
    """检测 LLM 端点连通性，同时更新业务状态（cooldown/mark_healthy）"""
    import time

    from openakita.llm.client import LLMClient

    wd = Path(workspace_dir).expanduser().resolve()
    config_path = wd / "data" / "llm_endpoints.json"
    if not config_path.exists():
        raise ValueError(f"端点配置文件不存在: {config_path}")

    # 加载 .env 以获取 API key
    env_path = wd / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            eq = line.find("=")
            if eq > 0:
                os.environ.setdefault(line[:eq].strip(), line[eq + 1:])

    client = LLMClient(config_path=config_path)

    results = []
    targets = list(client._providers.items())
    if endpoint_name:
        targets = [(n, p) for n, p in targets if n == endpoint_name]
        if not targets:
            raise ValueError(f"未找到端点: {endpoint_name}")

    for name, provider in targets:
        t0 = time.time()
        try:
            await provider.health_check()
            latency = round((time.time() - t0) * 1000)
            results.append({
                "name": name,
                "status": "healthy",
                "latency_ms": latency,
                "error": None,
                "error_category": None,
                "consecutive_failures": 0,
                "cooldown_remaining": 0,
                "is_extended_cooldown": False,
                "last_checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })
        except Exception as e:
            latency = round((time.time() - t0) * 1000)
            results.append({
                "name": name,
                "status": "unhealthy" if provider.consecutive_cooldowns >= 3 else "degraded",
                "latency_ms": latency,
                "error": str(e)[:500],
                "error_category": provider.error_category,
                "consecutive_failures": provider.consecutive_cooldowns,
                "cooldown_remaining": round(provider.cooldown_remaining),
                "is_extended_cooldown": provider.is_extended_cooldown,
                "last_checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })

    _json_print(results)


async def health_check_im(workspace_dir: str, channel: str | None) -> None:
    """检测 IM 通道连通性"""
    import httpx

    wd = Path(workspace_dir).expanduser().resolve()

    # 加载 .env
    env: dict[str, str] = {}
    env_path = wd / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            eq = line.find("=")
            if eq > 0:
                env[line[:eq].strip()] = line[eq + 1:]

    channels_def = [
        {
            "id": "telegram",
            "name": "Telegram",
            "enabled_key": "TELEGRAM_ENABLED",
            "required_keys": ["TELEGRAM_BOT_TOKEN"],
        },
        {
            "id": "feishu",
            "name": "飞书",
            "enabled_key": "FEISHU_ENABLED",
            "required_keys": ["FEISHU_APP_ID", "FEISHU_APP_SECRET"],
        },
        {
            "id": "wework",
            "name": "企业微信",
            "enabled_key": "WEWORK_ENABLED",
            "required_keys": ["WEWORK_CORP_ID", "WEWORK_TOKEN", "WEWORK_ENCODING_AES_KEY"],
        },
        {
            "id": "dingtalk",
            "name": "钉钉",
            "enabled_key": "DINGTALK_ENABLED",
            "required_keys": ["DINGTALK_CLIENT_ID", "DINGTALK_CLIENT_SECRET"],
        },
        {
            "id": "onebot",
            "name": "OneBot",
            "enabled_key": "ONEBOT_ENABLED",
            "required_keys": ["ONEBOT_WS_URL"],
        },
        {
            "id": "qqbot",
            "name": "QQ 官方机器人",
            "enabled_key": "QQBOT_ENABLED",
            "required_keys": ["QQBOT_APP_ID", "QQBOT_APP_SECRET"],
        },
    ]

    import time

    targets = channels_def
    if channel:
        targets = [c for c in targets if c["id"] == channel]
        if not targets:
            raise ValueError(f"未知 IM 通道: {channel}")

    results = []
    for ch in targets:
        enabled = env.get(ch["enabled_key"], "").strip().lower() in ("true", "1", "yes")
        if not enabled:
            results.append({
                "channel": ch["id"],
                "name": ch["name"],
                "status": "disabled",
                "error": None,
                "last_checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })
            continue

        missing = [k for k in ch["required_keys"] if not env.get(k, "").strip()]
        if missing:
            results.append({
                "channel": ch["id"],
                "name": ch["name"],
                "status": "unhealthy",
                "error": f"缺少配置: {', '.join(missing)}",
                "last_checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })
            continue

        # 实际连通性测试
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                if ch["id"] == "telegram":
                    token = env["TELEGRAM_BOT_TOKEN"]
                    resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
                    resp.raise_for_status()
                    data = resp.json()
                    if not data.get("ok"):
                        raise Exception(data.get("description", "Telegram API 返回错误"))
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
                        raise Exception(data.get("msg", "飞书验证失败"))
                elif ch["id"] == "wework":
                    # 智能机器人模式不需要 secret/access_token，无法通过 API 验证
                    # 只检查必填参数是否完整
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
                        raise Exception(f"缺少必填参数: {', '.join(missing)}")
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
                        raise Exception(data.get("message", "钉钉验证失败"))
                elif ch["id"] == "onebot":
                    # OneBot WebSocket: 验证 URL 格式并尝试连接
                    ws_url = env.get("ONEBOT_WS_URL", "")
                    if not ws_url.startswith(("ws://", "wss://")):
                        raise Exception(f"无效的 WebSocket URL: {ws_url}")
                    # 尝试 HTTP 连接到 OneBot
                    http_url = ws_url.replace("ws://", "http://").replace("wss://", "https://")
                    resp = await client.get(http_url, timeout=5)
                    # OneBot 即使返回非 200 也算可达
                elif ch["id"] == "qqbot":
                    # QQ 官方机器人：验证 AppID/AppSecret 能获取 Access Token
                    app_id = env["QQBOT_APP_ID"]
                    app_secret = env["QQBOT_APP_SECRET"]
                    resp = await client.post(
                        "https://bots.qq.com/app/getAppAccessToken",
                        json={"appId": app_id, "clientSecret": app_secret},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    if not data.get("access_token"):
                        raise Exception(data.get("message", "QQ 机器人验证失败"))

            results.append({
                "channel": ch["id"],
                "name": ch["name"],
                "status": "healthy",
                "error": None,
                "last_checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })
        except Exception as e:
            results.append({
                "channel": ch["id"],
                "name": ch["name"],
                "status": "unhealthy",
                "error": str(e)[:500],
                "last_checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })

    _json_print(results)


def ensure_channel_deps(workspace_dir: str) -> None:
    """检查已启用 IM 通道的 Python 依赖，缺失的自动 pip install。"""
    import importlib
    import subprocess

    wd = Path(workspace_dir).expanduser().resolve()

    # 读取 .env
    env: dict[str, str] = {}
    env_path = wd / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            eq = line.find("=")
            if eq > 0:
                env[line[:eq].strip()] = line[eq + 1 :].strip()

    # 通道 → [(import_name, pip_package), ...]
    channel_deps: dict[str, list[tuple[str, str]]] = {
        "feishu": [("lark_oapi", "lark-oapi")],
        "dingtalk": [("dingtalk_stream", "dingtalk-stream")],
        "wework": [("aiohttp", "aiohttp"), ("Crypto", "pycryptodome")],
        "onebot": [("websockets", "websockets")],
        "qqbot": [("botpy", "qq-botpy"), ("pilk", "pilk")],
    }

    enabled_key_map = {
        "feishu": "FEISHU_ENABLED",
        "dingtalk": "DINGTALK_ENABLED",
        "wework": "WEWORK_ENABLED",
        "onebot": "ONEBOT_ENABLED",
        "qqbot": "QQBOT_ENABLED",
    }

    missing: list[str] = []
    for channel, enabled_key in enabled_key_map.items():
        if env.get(enabled_key, "").strip().lower() not in ("true", "1", "yes"):
            continue
        for import_name, pip_name in channel_deps.get(channel, []):
            try:
                importlib.import_module(import_name)
            except ImportError:
                if pip_name not in missing:
                    missing.append(pip_name)

    if not missing:
        _json_print({"status": "ok", "installed": [], "message": "所有依赖已就绪"})
        return

    # 执行安装 (PyInstaller 兼容: 使用 runtime_env 获取正确的 Python 解释器)
    from openakita.runtime_env import get_pip_command
    pip_cmd = get_pip_command(missing)
    if not pip_cmd:
        _json_print({
            "status": "error",
            "installed": [],
            "missing": missing,
            "message": "当前环境不支持自动安装依赖，请通过设置中心的模块管理安装",
        })
        return

    try:
        extra: dict = {}
        if sys.platform == "win32":
            extra["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(
            pip_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            **extra,
        )
        if result.returncode == 0:
            importlib.invalidate_caches()
            _json_print({
                "status": "ok",
                "installed": missing,
                "message": f"已安装: {', '.join(missing)}",
            })
        else:
            err = (result.stderr or result.stdout or "").strip()[-500:]
            _json_print({
                "status": "error",
                "installed": [],
                "missing": missing,
                "message": f"安装失败: {err}",
            })
    except subprocess.TimeoutExpired:
        _json_print({
            "status": "error",
            "installed": [],
            "missing": missing,
            "message": "安装超时（180s）",
        })
    except Exception as e:
        _json_print({
            "status": "error",
            "installed": [],
            "missing": missing,
            "message": str(e),
        })


def list_skills(workspace_dir: str) -> None:
    from openakita.skills.loader import SkillLoader

    wd = Path(workspace_dir).expanduser().resolve()
    if not wd.exists() or not wd.is_dir():
        raise ValueError(f"--workspace-dir 不存在或不是目录: {workspace_dir}")

    # 外部技能启用状态（Setup Center 用于展示“可启用/禁用”的开关）
    # 文件：<workspace>/data/skills.json
    # - 不存在 / 无 external_allowlist => 外部技能全部启用（兼容历史行为）
    # - external_allowlist: [] => 禁用所有外部技能
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
        out.append({
            "name": s.name,
            "description": s.description,
            "system": bool(getattr(s, "system", False)),
            "enabled": bool(getattr(s, "system", False)) or (external_allowlist is None) or (s.name in external_allowlist),
            "tool_name": getattr(s, "tool_name", None),
            "category": getattr(s, "category", None),
            "path": skill_path,
            "source_url": source_url,
            "config": getattr(s, "config", None) or getattr(s, "config_schema", None),
        })
    _json_print({"count": len(out), "skills": out})


def _looks_like_github_shorthand(url: str) -> bool:
    """判断 URL 是否为 GitHub 简写格式，如 'owner/repo' 或 'owner/repo@skill'。

    排除本地路径（包含反斜杠、以 . 或 / 开头、包含盘符如 C:）。
    """
    if url.startswith((".", "/", "~")) or "\\" in url:
        return False
    if len(url) > 1 and url[1] == ":":
        return False  # Windows 盘符路径，如 C:\\...
    # 至少包含一个 / 分隔 owner/repo
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
    """计算技能安装目录。

    优先使用 Tauri 传入的 workspace_dir（支持多工作区），
    若参数为空则回退到 ~/.openakita/workspaces/default/skills。
    """
    if workspace_dir and workspace_dir.strip():
        return Path(workspace_dir).expanduser().resolve() / "skills"
    return Path.home() / ".openakita" / "workspaces" / "default" / "skills"


def _has_git() -> bool:
    """检查系统是否安装了 git。"""
    import shutil

    return shutil.which("git") is not None


_GITHUB_ZIP_MIRRORS: list[str] = [
    "https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip",
    "https://gh-proxy.com/https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip",
    "https://mirror.ghproxy.com/https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip",
    "https://ghproxy.net/https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip",
]


def _download_github_zip(repo_owner: str, repo_name: str, dest_dir: Path) -> None:
    """通过 GitHub Archive API 下载仓库 ZIP 并解压到 dest_dir（不依赖 git）。

    自动尝试 main/master 分支，并在直连失败时回退到国内 CDN 镜像。
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
            f"无法下载仓库 {repo_owner}/{repo_name}，请检查网络或安装 Git。"
            f"（最后错误: {last_err}）"
        )

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        tmp_extract = Path(tempfile.mkdtemp(prefix="openakita_zip_"))
        try:
            zf.extractall(tmp_extract)
            children = list(tmp_extract.iterdir())
            src = children[0] if len(children) == 1 and children[0].is_dir() else tmp_extract
            shutil.copytree(str(src), str(dest_dir))
        finally:
            shutil.rmtree(str(tmp_extract), ignore_errors=True)


def _git_clone(args: list[str]) -> None:
    """执行 git clone，git 不可用时抛出友好错误。"""
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
            "未找到 git 命令。请安装 Git (https://git-scm.com) 或使用 GitHub 简写格式安装技能"
        )


def _parse_github_url(url: str) -> tuple[str, str] | None:
    """从 HTTPS GitHub URL 中提取 (owner, repo)，非 GitHub URL 返回 None。"""
    import re

    m = re.match(r"https?://github\.com/([^/]+)/([^/.]+)", url)
    if m:
        return m.group(1), m.group(2)
    return None


def _parse_gitee_url(url: str) -> tuple[str, str] | None:
    """从 HTTPS Gitee URL 中提取 (owner, repo)，非 Gitee URL 返回 None。"""
    import re

    m = re.match(r"https?://gitee\.com/([^/]+)/([^/.]+)", url)
    if m:
        return m.group(1), m.group(2)
    return None


def _download_gitee_zip(repo_owner: str, repo_name: str, dest_dir: Path) -> None:
    """通过 Gitee Archive API 下载仓库 ZIP 并解压到 dest_dir（不依赖 git）。"""
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
            f"无法下载 Gitee 仓库 {repo_owner}/{repo_name}，请检查网络。"
            f"（最后错误: {last_err}）"
        )

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        tmp_extract = Path(tempfile.mkdtemp(prefix="openakita_gitee_"))
        try:
            zf.extractall(tmp_extract)
            children = list(tmp_extract.iterdir())
            src = children[0] if len(children) == 1 and children[0].is_dir() else tmp_extract
            shutil.copytree(str(src), str(dest_dir))
        finally:
            shutil.rmtree(str(tmp_extract), ignore_errors=True)


def install_skill(workspace_dir: str, url: str) -> None:
    """安装技能（从 Git URL、GitHub 简写或本地目录）"""
    skills_dir = _resolve_skills_dir(workspace_dir)
    skills_dir.mkdir(parents=True, exist_ok=True)

    if url.startswith("github:"):
        # github:user/repo/path -> clone from GitHub
        parts = url.replace("github:", "").split("/")
        if len(parts) < 2:
            raise ValueError(f"无效的 GitHub URL: {url}")
        owner, repo = parts[0], parts[1]
        skill_name = parts[-1] if len(parts) > 2 else repo
        target = skills_dir / skill_name

        if target.exists():
            raise ValueError(f"技能目录已存在: {target}")

        if _has_git():
            git_url = f"https://github.com/{owner}/{repo}.git"
            _git_clone(["git", "clone", "--depth", "1", git_url, str(target)])
        else:
            _download_github_zip(owner, repo, target)

    elif url.startswith("http://") or url.startswith("https://"):
        skill_name = url.rstrip("/").split("/")[-1].replace(".git", "")
        target = skills_dir / skill_name
        if target.exists():
            raise ValueError(f"技能目录已存在: {target}")

        gh = _parse_github_url(url)
        ge = _parse_gitee_url(url)
        if ge:
            if _has_git():
                _git_clone(["git", "clone", "--depth", "1", url, str(target)])
            else:
                _download_gitee_zip(ge[0], ge[1], target)
        elif gh and not _has_git():
            _download_github_zip(gh[0], gh[1], target)
        else:
            _git_clone(["git", "clone", "--depth", "1", url, str(target)])

    elif _looks_like_github_shorthand(url):
        # GitHub 简写格式: "owner/repo@skill-name" 或 "owner/repo"
        import shutil
        import tempfile

        if "@" in url:
            repo_part, requested_skill = url.split("@", 1)
            requested_skill = requested_skill.strip().replace("\\", "/").strip("/")
            if not requested_skill:
                # "owner/repo@" → 使用仓库名作为技能名
                requested_skill = repo_part.split("/")[-1]
        else:
            repo_part = url
            requested_skill = repo_part.split("/")[-1]

        owner, repo = repo_part.split("/", 1)
        skill_name = _sanitize_skill_dir_name(requested_skill)
        target = skills_dir / skill_name

        if target.exists():
            raise ValueError(f"技能目录已存在: {target}")

        # 克隆/下载到临时目录，然后提取目标技能子目录
        tmp_parent = Path(tempfile.mkdtemp(prefix="openakita_skill_"))
        tmp_dir = tmp_parent / "repo"
        try:
            if _has_git():
                repo_url = f"https://github.com/{repo_part}.git"
                _git_clone(["git", "clone", "--depth", "1", repo_url, str(tmp_dir)])
            else:
                _download_github_zip(owner, repo, tmp_dir)

            # 支持 skillId 为子路径（如 "skills/web-search"）
            preferred_rel_paths: list[str] = []
            if requested_skill:
                preferred_rel_paths.append(requested_skill)
                if requested_skill.startswith("skills/"):
                    stripped = requested_skill[len("skills/"):]
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

            # 若未命中子目录，则按“整个仓库就是一个技能”处理
            source_dir = source_dir or tmp_dir
            shutil.copytree(str(source_dir), str(target))
            if source_dir == tmp_dir:
                # 清理克隆产生的 .git 目录
                git_dir = target / ".git"
                if git_dir.exists():
                    shutil.rmtree(str(git_dir), ignore_errors=True)
        finally:
            shutil.rmtree(str(tmp_parent), ignore_errors=True)
    else:
        # Local path
        src = Path(url).expanduser().resolve()
        if not src.exists():
            raise ValueError(f"源路径不存在: {url}")
        import shutil
        target = skills_dir / src.name
        if target.exists():
            raise ValueError(f"技能目录已存在: {target}")
        shutil.copytree(str(src), str(target))

    # Record install origin for marketplace matching (Issue #15)
    try:
        origin_file = target / ".openakita-source"
        origin_file.write_text(url, encoding="utf-8")
    except Exception:
        pass

    _json_print({"status": "ok", "skill_dir": str(target)})


def uninstall_skill(workspace_dir: str, skill_name: str) -> None:
    """卸载技能"""
    import shutil

    skills_dir = _resolve_skills_dir(workspace_dir)
    target = (skills_dir / skill_name).resolve()

    if not target.exists():
        raise ValueError(f"技能不存在: {skill_name}")

    # 防止路径穿越：确保解析后的路径仍在 skills_dir 下
    # 使用 relative_to 而不是 str.startswith（避免前缀碰撞，如 skills_evil/）
    try:
        target.relative_to(skills_dir.resolve())
    except ValueError:
        raise ValueError(f"不允许删除非工作区技能: {target}")

    # 检查是否为系统技能（SKILL.md 中 system: true）
    skill_md = target / "SKILL.md"
    if skill_md.exists():
        content = skill_md.read_text(encoding="utf-8", errors="replace")
        if "system: true" in content.lower()[:500]:
            raise ValueError(f"不允许删除系统技能: {skill_name}")

    shutil.rmtree(str(target))
    _json_print({"status": "ok", "removed": skill_name})


def list_marketplace() -> None:
    """列出市场可用技能（从注册表或 GitHub）"""
    # TODO: 从真实的注册表 API 获取
    # 暂返回硬编码的示例列表
    marketplace = [
        {
            "name": "web-search",
            "description": "使用 Serper/Google 进行网络搜索",
            "author": "openakita",
            "url": "github:openakita/skills/web-search",
            "stars": 42,
            "tags": ["搜索", "网络"],
        },
        {
            "name": "code-interpreter",
            "description": "Python 代码解释器，支持数据分析和可视化",
            "author": "openakita",
            "url": "github:openakita/skills/code-interpreter",
            "stars": 38,
            "tags": ["代码", "数据分析"],
        },
        {
            "name": "browser-use",
            "description": "浏览器自动化，支持网页操作和数据抓取",
            "author": "openakita",
            "url": "github:openakita/skills/browser-use",
            "stars": 25,
            "tags": ["浏览器", "自动化"],
        },
        {
            "name": "image-gen",
            "description": "AI 图片生成，支持 DALL-E / Stable Diffusion",
            "author": "openakita",
            "url": "github:openakita/skills/image-gen",
            "stars": 19,
            "tags": ["图片", "生成"],
        },
    ]
    _json_print(marketplace)


def get_skill_config(workspace_dir: str, skill_name: str) -> None:
    """获取技能的配置 schema"""
    from openakita.skills.loader import SkillLoader

    wd = Path(workspace_dir).expanduser().resolve()
    loader = SkillLoader()
    loader.load_all(base_path=wd)

    skills = loader.registry.list_all()
    for s in skills:
        if s.name == skill_name:
            config = getattr(s, "config", None) or getattr(s, "config_schema", None) or []
            _json_print({
                "name": s.name,
                "config": config,
            })
            return

    raise ValueError(f"技能未找到: {skill_name}")


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)

    p = argparse.ArgumentParser(prog="openakita.setup_center.bridge")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list-providers", help="列出服务商（JSON）")

    pm = sub.add_parser("list-models", help="拉取模型列表（JSON）")
    pm.add_argument("--api-type", required=True, help="openai | anthropic")
    pm.add_argument("--base-url", required=True, help="API Base URL（openai 通常是 .../v1）")
    pm.add_argument("--provider-slug", default="", help="可选：用于能力推断与注册表命中")

    ps = sub.add_parser("list-skills", help="列出技能（JSON）")
    ps.add_argument("--workspace-dir", required=True, help="工作区目录（用于扫描 skills/.cursor/skills 等）")

    ph = sub.add_parser("health-check-endpoint", help="检测 LLM 端点健康度（JSON）")
    ph.add_argument("--workspace-dir", required=True, help="工作区目录")
    ph.add_argument("--endpoint-name", default="", help="可选：仅检测指定端点（为空=全部）")

    pi = sub.add_parser("health-check-im", help="检测 IM 通道连通性（JSON）")
    pi.add_argument("--workspace-dir", required=True, help="工作区目录")
    pi.add_argument("--channel", default="", help="可选：仅检测指定通道 ID（为空=全部）")

    p_ecd = sub.add_parser("ensure-channel-deps", help="检查并自动安装已启用 IM 通道的依赖（JSON）")
    p_ecd.add_argument("--workspace-dir", required=True, help="工作区目录")

    p_inst = sub.add_parser("install-skill", help="安装技能（从 URL/路径）")
    p_inst.add_argument("--workspace-dir", required=True, help="工作区目录")
    p_inst.add_argument("--url", required=True, help="技能来源 URL 或路径")

    p_uninst = sub.add_parser("uninstall-skill", help="卸载技能")
    p_uninst.add_argument("--workspace-dir", required=True, help="工作区目录")
    p_uninst.add_argument("--skill-name", required=True, help="技能名称")

    sub.add_parser("list-marketplace", help="列出市场可用技能（JSON）")

    p_cfg = sub.add_parser("get-skill-config", help="获取技能配置 schema（JSON）")
    p_cfg.add_argument("--workspace-dir", required=True, help="工作区目录")
    p_cfg.add_argument("--skill-name", required=True, help="技能名称")

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

    raise SystemExit(2)


if __name__ == "__main__":
    from openakita.runtime_env import IS_FROZEN, ensure_ssl_certs

    if IS_FROZEN:
        ensure_ssl_certs()

    try:
        main()
    except Exception as e:
        sys.stderr.write(str(e))
        sys.stderr.write("\n")
        raise

