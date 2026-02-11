"""
Setup Center Bridge

该模块用于给 Setup Center（Tauri App）提供一个稳定的 Python 入口：

- `python -m openakita.setup_center.bridge list-providers`
- `python -m openakita.setup_center.bridge list-models --api-type ... --base-url ... [--provider-slug ...]`
- `python -m openakita.setup_center.bridge list-skills --workspace-dir ...`

输出均为 JSON（stdout），错误输出到 stderr 并以非 0 退出码返回。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
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

    url = base_url.rstrip("/") + "/models"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {api_key}"})
        resp.raise_for_status()
        data = resp.json()

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

    b = base_url.rstrip("/")
    url = b + "/models" if b.endswith("/v1") else b + "/v1/models"

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            url,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        resp.raise_for_status()
        data = resp.json()

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
    if not api_key.strip():
        raise ValueError("缺少 API Key（Setup Center 会通过环境变量 SETUPCENTER_API_KEY 传入）")

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
    from openakita.llm.config import load_config

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

    config = load_config(str(config_path))
    client = LLMClient(config=config, config_path=config_path)

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

    # 持久化 cooldown 状态
    client.save_cooldown_state()
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
            "id": "qq",
            "name": "QQ (OneBot)",
            "enabled_key": "QQ_ENABLED",
            "required_keys": ["QQ_ONEBOT_WS_URL"],
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
                        if not corp_id: missing.append("WEWORK_CORP_ID")
                        if not token: missing.append("WEWORK_TOKEN")
                        if not aes_key: missing.append("WEWORK_ENCODING_AES_KEY")
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
                elif ch["id"] == "qq":
                    # OneBot WebSocket: 仅验证 URL 格式，不实际连接
                    ws_url = env.get("QQ_ONEBOT_WS_URL", "")
                    if not ws_url.startswith(("ws://", "wss://")):
                        raise Exception(f"无效的 WebSocket URL: {ws_url}")
                    # 尝试 HTTP 连接到 OneBot
                    http_url = ws_url.replace("ws://", "http://").replace("wss://", "https://")
                    resp = await client.get(http_url, timeout=5)
                    # OneBot 即使返回非 200 也算可达

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
    out = [
        {
            "name": s.name,
            "description": s.description,
            "system": bool(getattr(s, "system", False)),
            "enabled": bool(getattr(s, "system", False)) or (external_allowlist is None) or (s.name in external_allowlist),
            "tool_name": getattr(s, "tool_name", None),
            "category": getattr(s, "category", None),
            "path": getattr(s, "skill_path", None),
            "config": getattr(s, "config", None) or getattr(s, "config_schema", None),
        }
        for s in skills
    ]
    _json_print({"count": len(out), "skills": out})


def install_skill(workspace_dir: str, url: str) -> None:
    """安装技能（从 Git URL 或目录）"""
    wd = Path(workspace_dir).expanduser().resolve()
    skills_dir = wd / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)

    import subprocess

    if url.startswith("github:"):
        # github:user/repo/path -> clone from GitHub
        parts = url.replace("github:", "").split("/")
        if len(parts) < 2:
            raise ValueError(f"无效的 GitHub URL: {url}")
        repo = f"https://github.com/{parts[0]}/{parts[1]}.git"
        skill_name = parts[-1] if len(parts) > 2 else parts[1]
        target = skills_dir / skill_name

        if target.exists():
            raise ValueError(f"技能目录已存在: {target}")

        # Sparse checkout or clone
        subprocess.run(
            ["git", "clone", "--depth", "1", repo, str(target)],
            check=True,
            capture_output=True,
            text=True,
        )
    elif url.startswith("http://") or url.startswith("https://"):
        skill_name = url.rstrip("/").split("/")[-1].replace(".git", "")
        target = skills_dir / skill_name
        if target.exists():
            raise ValueError(f"技能目录已存在: {target}")
        subprocess.run(
            ["git", "clone", "--depth", "1", url, str(target)],
            check=True,
            capture_output=True,
            text=True,
        )
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

    _json_print({"status": "ok", "skill_dir": str(target)})


def uninstall_skill(workspace_dir: str, skill_name: str) -> None:
    """卸载技能"""
    import shutil

    wd = Path(workspace_dir).expanduser().resolve()
    skills_dir = wd / "skills"
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
    try:
        main()
    except Exception as e:
        sys.stderr.write(str(e))
        sys.stderr.write("\n")
        raise

