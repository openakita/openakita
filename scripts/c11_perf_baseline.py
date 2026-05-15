"""C11: PolicyEngineV2 + ApprovalClassifier 性能 SLO 基线测量 + budget assert.

为什么要 SLO?
=============

Policy V2 是热路径的一部分——每次 tool_use 之前都要走一遍 12 步决策链.
如果决策本身慢, 用户会感受到"按下回车后 LLM 开始 think 之前那一段"
的延迟. C11 是 Policy V2 主体功能落地的回归验证里程碑, **必须**有数字
化的性能下限. 否则未来某次重构静悄悄把 evaluate 拖到 50ms / call 也
没人发现 (LLM 推理 200-2000ms 一旦把 50ms 隐藏了进去就再也找不出来).

测量方式
========

10000 次循环 / 每个 path:

- ``ApprovalClassifier.classify_full(tool, params, ctx)`` —
  唯一调用, 只走 5 步分类链 (含 LRU cache hit 路径)
- ``PolicyEngineV2.evaluate_tool_call(event, ctx)`` —
  完整 12 步决策, classifier 也包含进去

各自取 p50 / p95 / p99 用 ``statistics.quantiles``.

SLO Budget
==========

参考 v1 RiskGate 的实测延迟约 1-3ms / call (生产 telemetry, 非本仓
publicly available, 但作为安全 baseline 远高于实际值). v2 设计目标
是 "不退步", 因此设:

- ``classify_full`` p95 < 1.0 ms (含 cache miss; cache hit 应 <0.05ms)
- ``evaluate_tool_call`` p95 < 5.0 ms (含 12 步 + 配置查表 + audit hook 路径)

p50 / p99 不强约束, 仅 reporting (等 C11 跑出真实数字后再决定是否
锁紧). 数字记录到 ``.cache/c11_perf_baseline.json`` 以便 CI / 未来
commit 对比.

Budget 是 **soft fail-loud**: 超时打 [WARN] 不退出 (脚本 exit code
反映**测量是否完成**, 不反映 "性能是否达标"). 这避免在 CI runner
负载抖动时把整个 build 拖红.

调用方式
========

    python scripts/c11_perf_baseline.py
    python scripts/c11_perf_baseline.py --json     # 仅打 JSON, 给 CI 解析
    python scripts/c11_perf_baseline.py --strict   # 超 SLO 时 exit 1
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from openakita.core.policy_v2.classifier import ApprovalClassifier  # noqa: E402
from openakita.core.policy_v2.context import PolicyContext  # noqa: E402
from openakita.core.policy_v2.engine import PolicyEngineV2  # noqa: E402
from openakita.core.policy_v2.enums import (  # noqa: E402
    ConfirmationMode,
    SessionRole,
)
from openakita.core.policy_v2.models import ToolCallEvent  # noqa: E402
from openakita.core.policy_v2.schema import PolicyConfigV2  # noqa: E402

# --------------------------------------------------------------------------- SLO

SLO_BUDGET_MS: dict[str, float] = {
    "classify_full_p95": 1.0,
    "evaluate_tool_call_p95": 5.0,
}

# Mix of tools that exercise different classification branches:
# - readonly (cache friendly), mutating, destructive, control_plane,
# - heuristic-only (no explicit handler), and a fresh "unknown"
TOOL_MIX: tuple[tuple[str, dict], ...] = (
    ("list_directory", {"path": "."}),
    ("read_file", {"path": "README.md"}),
    ("write_file", {"path": "tmp.txt", "content": "x"}),
    ("delete_file", {"path": "tmp.txt"}),
    ("install_skill", {"name": "x"}),
    ("run_shell", {"command": "echo hi"}),
    ("web_search", {"query": "policy v2"}),
    ("update_scheduled_task", {"id": "1"}),
    ("a_brand_new_unknown_tool", {}),
    ("ask_user", {"prompt": "y/n?"}),
)

CTX = PolicyContext(
    session_id="perf-bench",
    workspace=Path.cwd(),
    channel="cli",
    is_owner=True,
    session_role=SessionRole.AGENT,
    confirmation_mode=ConfirmationMode.DEFAULT,
)


def _measure_classify(iterations: int) -> list[float]:
    """Wall-clock per-call timing for classifier.classify_full in ms."""
    classifier = ApprovalClassifier()
    durations: list[float] = []
    for i in range(iterations):
        tool, params = TOOL_MIX[i % len(TOOL_MIX)]
        t0 = time.perf_counter()
        classifier.classify_full(tool, params, CTX)
        durations.append((time.perf_counter() - t0) * 1000.0)
    return durations


def _measure_evaluate(iterations: int) -> list[float]:
    """Wall-clock per-call timing for engine.evaluate_tool_call in ms."""
    engine = PolicyEngineV2(config=PolicyConfigV2())
    durations: list[float] = []
    for i in range(iterations):
        tool, params = TOOL_MIX[i % len(TOOL_MIX)]
        evt = ToolCallEvent(tool=tool, params=params)
        t0 = time.perf_counter()
        engine.evaluate_tool_call(evt, CTX)
        durations.append((time.perf_counter() - t0) * 1000.0)
    return durations


def _percentiles(samples: list[float]) -> dict[str, float]:
    """Compute p50/p95/p99 + mean/min/max from a sample list (ms)."""
    if not samples:
        return dict.fromkeys(("p50", "p95", "p99", "mean", "min", "max"), 0.0)
    samples_sorted = sorted(samples)
    return {
        "p50": statistics.median(samples_sorted),
        "p95": samples_sorted[int(len(samples_sorted) * 0.95)],
        "p99": samples_sorted[int(len(samples_sorted) * 0.99)],
        "mean": statistics.fmean(samples_sorted),
        "min": samples_sorted[0],
        "max": samples_sorted[-1],
    }


def _format_table(name: str, stats: dict[str, float], budget_ms: float) -> str:
    """Pretty-print one stat block + SLO compare line."""
    p95 = stats["p95"]
    status = "PASS" if p95 <= budget_ms else "FAIL"
    return (
        f"  {name:<28}  "
        f"p50={stats['p50']:6.3f}ms  "
        f"p95={stats['p95']:6.3f}ms  "
        f"p99={stats['p99']:6.3f}ms  "
        f"mean={stats['mean']:6.3f}ms  "
        f"max={stats['max']:7.3f}ms  "
        f"[{status} budget {budget_ms:.1f}ms]"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="C11 PolicyEngineV2 perf baseline")
    parser.add_argument(
        "--iterations",
        type=int,
        default=10000,
        help="loops per metric (default 10000)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 if any SLO exceeded (default: warn-only)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit JSON summary only (for CI pipeline)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / ".cache" / "c11_perf_baseline.json",
        help="output JSON path (default .cache/c11_perf_baseline.json)",
    )
    args = parser.parse_args()

    if not args.json:
        print("=" * 70)
        print(f"C11 Perf Baseline — {args.iterations} iters per metric")
        print("=" * 70)

    # Warm up (LRU + JIT path tracing) — drop first 200 to avoid cold-cache outliers
    _measure_classify(200)
    _measure_evaluate(200)

    classify_samples = _measure_classify(args.iterations)
    evaluate_samples = _measure_evaluate(args.iterations)

    classify_stats = _percentiles(classify_samples)
    evaluate_stats = _percentiles(evaluate_samples)

    summary = {
        "iterations": args.iterations,
        "classify_full": classify_stats,
        "evaluate_tool_call": evaluate_stats,
        "budget": SLO_BUDGET_MS,
        "ts_unix": time.time(),
    }

    # Persist baseline
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(_format_table(
            "ApprovalClassifier.classify_full",
            classify_stats,
            SLO_BUDGET_MS["classify_full_p95"],
        ))
        print(_format_table(
            "PolicyEngineV2.evaluate_tool_call",
            evaluate_stats,
            SLO_BUDGET_MS["evaluate_tool_call_p95"],
        ))
        print()
        print(f"  baseline saved → {args.out}")

    failed: list[str] = []
    if classify_stats["p95"] > SLO_BUDGET_MS["classify_full_p95"]:
        failed.append(
            f"classify_full p95={classify_stats['p95']:.3f}ms > "
            f"budget {SLO_BUDGET_MS['classify_full_p95']:.1f}ms"
        )
    if evaluate_stats["p95"] > SLO_BUDGET_MS["evaluate_tool_call_p95"]:
        failed.append(
            f"evaluate_tool_call p95={evaluate_stats['p95']:.3f}ms > "
            f"budget {SLO_BUDGET_MS['evaluate_tool_call_p95']:.1f}ms"
        )

    if failed:
        prefix = "[FAIL]" if args.strict else "[WARN]"
        for line in failed:
            print(f"{prefix} {line}", file=sys.stderr)
        if args.strict:
            return 1

    if not args.json:
        print("[PASS] perf baseline within SLO budgets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
