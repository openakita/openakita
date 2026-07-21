"""LLM latency benchmark — stage-by-stage timing for B4 root-cause analysis.

Measures the cost of each stage of a single LLM round-trip against the
currently configured primary endpoint:

  1. System-prompt assembly (build_system_prompt)
  2. LLMClient.chat_stream() total wall-time
  3. TTFT (time to first content/thinking delta)
  4. Decode time (total - TTFT)
  5. Output size (chars + estimated tokens)

Does NOT read .env or any secret file directly; reuses the existing config
loader. Does NOT mutate Settings or provider configuration.

Usage:
    python scripts/llm_latency_benchmark.py
    python scripts/llm_latency_benchmark.py --no-stream --repeats 2
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import openakita.agent  # noqa: E402,F401  - resolve circular import (agent <-> llm.client)
from openakita.llm.client import LLMClient  # noqa: E402
from openakita.llm.types import Message  # noqa: E402
from openakita.prompt.builder import (  # noqa: E402
    PromptMode,
    PromptProfile,
    build_system_prompt,
)

PROMPTS = [
    "今天日期是?",
    "你好",
    "1+1=?",
    "今天天气如何?",
    "讲一个简短的笑话",
]


def _est_tokens(text: str) -> int:
    return max(1, len(text) // 2)


def _assemble(identity_dir: Path, *, full: bool) -> tuple[str, float]:
    t0 = time.perf_counter()
    if full:
        sp = build_system_prompt(
            identity_dir=identity_dir,
            tools_enabled=False,
            prompt_mode=PromptMode.FULL,
            prompt_profile=PromptProfile.LOCAL_AGENT,
            session_type="cli",
            mode="agent",
        )
    else:
        sp = build_system_prompt(
            identity_dir=identity_dir,
            tools_enabled=False,
            prompt_mode=PromptMode.MINIMAL,
            prompt_profile=PromptProfile.CONSUMER_CHAT,
            session_type="cli",
            mode="agent",
        )
    return sp, time.perf_counter() - t0


def _delta_text(event: dict) -> str:
    if not isinstance(event, dict) or event.get("type") != "content_block_delta":
        return ""
    d = event.get("delta") or {}
    return d.get("text") or "" if d.get("type") in ("text", "thinking") else ""


async def _bench_once(client: LLMClient, sp: str, prompt: str, stream: bool) -> dict:
    msgs = [Message(role="user", content=prompt)]
    t0 = time.perf_counter()
    ttft = None
    out_text = ""
    in_tok = out_tok = 0
    if stream:
        async for ev in client.chat_stream(messages=msgs, system=sp, max_tokens=512):
            txt = _delta_text(ev)
            if txt:
                if ttft is None:
                    ttft = time.perf_counter() - t0
                out_text += txt
            usage = ev.get("usage") if isinstance(ev, dict) else None
            if usage:
                in_tok = usage.get("input_tokens") or in_tok
                out_tok = usage.get("output_tokens") or out_tok
        total = time.perf_counter() - t0
    else:
        resp = await client.chat(messages=msgs, system=sp, max_tokens=512)
        total = time.perf_counter() - t0
        for blk in resp.content or []:
            if hasattr(blk, "text"):
                out_text += blk.text or ""
        in_tok = resp.usage.input_tokens
        out_tok = resp.usage.output_tokens
    return {
        "prompt": prompt,
        "total_s": round(total, 3),
        "ttft_s": round(ttft, 3) if ttft is not None else None,
        "decode_s": round(total - ttft, 3) if ttft is not None else None,
        "out_chars": len(out_text),
        "est_out_tokens": _est_tokens(out_text),
        "input_tokens": in_tok,
        "output_tokens": out_tok,
    }


def _stats(xs: list[float]) -> dict:
    if not xs:
        return {}
    sx = sorted(xs)
    n = len(sx)
    p95 = sx[max(0, min(n - 1, int(round(n * 0.95)) - 1))]
    return {
        "n": n,
        "avg": round(statistics.fmean(sx), 3),
        "median": round(statistics.median(sx), 3),
        "p95": round(p95, 3),
        "min": round(sx[0], 3),
        "max": round(sx[-1], 3),
    }


async def main_async(stream: bool, repeats: int) -> dict:
    client = LLMClient()
    if not client.endpoints:
        raise SystemExit("No LLM endpoints configured.")
    primary = client.endpoints[0]
    identity_dir = REPO_ROOT / "identity"
    sp_full, t_full = _assemble(identity_dir, full=True)
    sp_min, t_min = _assemble(identity_dir, full=False)
    _, t_full2 = _assemble(identity_dir, full=True)

    print(f"Endpoint:  {primary.name}  api={primary.api_type}  model={primary.model}")
    print(f"Base URL:  {primary.base_url}")
    print(
        f"SysPrompt: FULL/LOCAL_AGENT={len(sp_full)}c (~{_est_tokens(sp_full)} tok), "
        f"build1={t_full:.3f}s build2={t_full2:.3f}s"
    )
    print(
        f"           MINIMAL/CONSUMER_CHAT={len(sp_min)}c (~{_est_tokens(sp_min)} tok), "
        f"build={t_min:.3f}s"
    )
    print(f"Streaming: {stream}, repeats per prompt: {repeats}")
    print("-" * 88)

    print("[warmup] (excluded)")
    try:
        await _bench_once(client, sp_full, "ok", stream)
    except Exception as e:
        print(f"  warmup failed: {e}")

    results = []
    for p in PROMPTS:
        for r in range(repeats):
            try:
                row = await _bench_once(client, sp_full, p, stream)
                row["error"] = None
            except Exception as e:
                row = {"prompt": p, "error": f"{type(e).__name__}: {str(e)[:200]}"}
            row["repeat"] = r + 1
            results.append(row)
            print(
                "{:<18} run={} total={:>6}s ttft={:>5} decode={:>5} out={}c "
                "in/out_tok={}/{} {}".format(
                    p[:18],
                    row.get("repeat"),
                    row.get("total_s", "?"),
                    str(row.get("ttft_s", "-")),
                    str(row.get("decode_s", "-")),
                    row.get("out_chars", 0),
                    row.get("input_tokens", 0),
                    row.get("output_tokens", 0),
                    f"ERR: {row['error']}" if row.get("error") else "",
                )
            )

    totals = [r["total_s"] for r in results if r.get("total_s") is not None]
    ttfts = [r["ttft_s"] for r in results if r.get("ttft_s") is not None]
    decodes = [r["decode_s"] for r in results if r.get("decode_s") is not None]
    summary = {
        "endpoint": primary.name,
        "api_type": primary.api_type,
        "model": primary.model,
        "base_url": primary.base_url,
        "streaming": stream,
        "system_prompt_full_chars": len(sp_full),
        "system_prompt_full_est_tokens": _est_tokens(sp_full),
        "system_prompt_min_chars": len(sp_min),
        "system_prompt_min_est_tokens": _est_tokens(sp_min),
        "prompt_assembly_full_first_s": round(t_full, 4),
        "prompt_assembly_full_cached_s": round(t_full2, 4),
        "prompt_assembly_min_s": round(t_min, 4),
        "stats_total": _stats(totals),
        "stats_ttft": _stats(ttfts),
        "stats_decode": _stats(decodes),
        "runs": results,
    }
    print("-" * 88)
    for name, s in (
        ("Total", summary["stats_total"]),
        ("TTFT", summary["stats_ttft"]),
        ("Decode", summary["stats_decode"]),
    ):
        if s:
            print(
                f"{name:<8} avg={s['avg']}s median={s['median']}s p95={s['p95']}s "
                f"min={s['min']}s max={s['max']}s n={s['n']}"
            )
    await client.close()
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="LLM latency benchmark (B4 root cause)")
    ap.add_argument("--no-stream", action="store_true")
    ap.add_argument("--repeats", type=int, default=1)
    args = ap.parse_args()
    summary = asyncio.run(main_async(stream=not args.no_stream, repeats=args.repeats))
    safe = (summary["endpoint"] + "_" + summary["model"]).replace("/", "_").replace("\\", "_")
    out = REPO_ROOT / "tmp_p10" / f"_llm_bench_{safe}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved JSON: {out}")


if __name__ == "__main__":
    main()
