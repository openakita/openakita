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
        }
        for s in skills
    ]
    _json_print({"count": len(out), "skills": out})


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

    raise SystemExit(2)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        sys.stderr.write(str(e))
        sys.stderr.write("\n")
        raise

