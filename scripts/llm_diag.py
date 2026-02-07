"""
LLM 端点连通性诊断脚本（Windows/国内网络常用）

用途：
- 快速判断超时是代理污染、IPv6 黑洞、还是端点本身慢/不可达
- 对每个端点做一个极小请求（max_tokens=10），并在四种网络模式下对比结果

运行：
    python scripts/llm_diag.py
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import asdict

from openakita.llm.client import LLMClient
from openakita.llm.config import load_endpoints_config
from openakita.llm.types import LLMRequest, Message


def _reset_env(keys: list[str]) -> None:
    for k in keys:
        os.environ.pop(k, None)


def _clone_endpoints_with_timeout(seconds: int) -> list:
    endpoints, _ = load_endpoints_config()
    cloned = []
    for ep in endpoints:
        # EndpointConfig 是 dataclass，这里用 asdict/构造器克隆，避免修改全局对象
        data = asdict(ep)
        data["timeout"] = seconds
        cloned.append(type(ep)(**data))
    return cloned


async def _probe_endpoints(timeout_seconds: int) -> list[tuple[str, str, bool, float, str | None]]:
    endpoints = _clone_endpoints_with_timeout(timeout_seconds)
    client = LLMClient(endpoints=endpoints)

    results: list[tuple[str, str, bool, float, str | None]] = []
    for ep in client.endpoints:
        print(f"  probing: {ep.name} ({ep.provider})...", flush=True)
        p = client.get_provider(ep.name)
        if p is None:
            results.append((ep.name, ep.provider, False, 0.0, "provider_not_created"))
            continue

        t0 = time.time()
        ok = False
        err: str | None = None
        try:
            req = LLMRequest(messages=[Message(role="user", content="ping")], max_tokens=10)
            await asyncio.wait_for(p.chat(req), timeout=timeout_seconds + 5)
            ok = True
        except Exception as e:  # noqa: BLE001 - diagnostic only
            err = str(e)
        dt = time.time() - t0
        results.append((ep.name, ep.provider, ok, dt, err))

    await client.close()
    return results


def _print_env_snapshot() -> None:
    keys = [
        "ALL_PROXY",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "NO_PROXY",
        "FORCE_IPV4",
        "LLM_DISABLE_PROXY",
        "OPENAKITA_DISABLE_PROXY",
        "DISABLE_PROXY",
    ]
    snapshot = {k: os.environ.get(k) for k in keys if os.environ.get(k) is not None}
    print("env(proxy/ipv4) =", snapshot if snapshot else "{}", flush=True)


def _fmt_results(results: list[tuple[str, str, bool, float, str | None]]) -> str:
    lines: list[str] = []
    for name, prov, ok, dt, err in results:
        if ok:
            lines.append(f"- {name} ({prov}) OK  {dt:.2f}s")
        else:
            lines.append(f"- {name} ({prov}) FAIL {dt:.2f}s :: {err}")
    return "\n".join(lines)


async def main() -> None:
    base_env_keys = ["LLM_DISABLE_PROXY", "FORCE_IPV4"]
    modes = [
        ("default", {}),
        ("no_proxy", {"LLM_DISABLE_PROXY": "1"}),
        ("ipv4", {"FORCE_IPV4": "true"}),
        ("no_proxy+ipv4", {"LLM_DISABLE_PROXY": "1", "FORCE_IPV4": "true"}),
    ]

    # 诊断用短超时：先判断“能不能连上”。如果短超时 OK 而实际仍经常超时，再增大 read timeout。
    diag_timeout_seconds = int(os.environ.get("LLM_DIAG_TIMEOUT", "25"))

    for label, env in modes:
        print(
            "\n=== MODE:",
            label,
            "env=",
            env,
            "timeout=",
            diag_timeout_seconds,
            "s ===",
            flush=True,
        )
        _reset_env(base_env_keys)
        os.environ.update(env)
        _print_env_snapshot()
        try:
            # 对整个模式也加总超时，防止极端情况下卡死
            results = await asyncio.wait_for(
                _probe_endpoints(timeout_seconds=diag_timeout_seconds),
                timeout=diag_timeout_seconds * 3,
            )
            print(_fmt_results(results), flush=True)
        except TimeoutError:
            print("MODE TIMEOUT (script-level): stuck beyond expected time", flush=True)


if __name__ == "__main__":
    asyncio.run(main())

