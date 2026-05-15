"""C22 P3-3: Policy V2 performance SLO regression suite (pytest form).

Background
==========

C11 shipped ``scripts/c11_perf_baseline.py`` as a one-shot CLI to
print p50/p95/p99 of ``ApprovalClassifier.classify_full`` and
``PolicyEngineV2.evaluate_tool_call``. That script does the measurement
job but never runs in CI — it requires manual ``python scripts/...``
invocation and outputs to ``.cache/c11_perf_baseline.json`` only.

Plan §13.5.4 + R5-19 explicitly asked for a pytest-based perf
regression suite under ``tests/perf/`` so a future refactor that
silently regresses the engine to 50 ms/decision gets caught at PR
time, not in production telemetry.

C21 二轮架构审计标为 P3-3. 本次落地.

Design
======

1. All tests are marked ``@pytest.mark.perf`` and skipped by default
   (``addopts = "-m 'not perf'"`` in pyproject.toml). CI explicitly
   opts in via ``pytest -m perf tests/perf/``. Reason: perf tests are
   slower (3-5s each) and noisier than unit tests; we don't want them
   to slow down the inner-loop ``pytest`` invocation.

2. Budgets mirror ``scripts/c11_perf_baseline.py`` SLO_BUDGET_MS — any
   change here MUST also update the C11 baseline doc in
   ``docs/policy_v2_research.md``. We deliberately don't import them
   from the script (cross-process module-path pain) but duplicate the
   numbers + assert they match the script's constants via a
   :class:`TestBudgetParity` guard so they can't drift silently.

3. SLOs are **p95**, not p50. p50 is noise-bounded on a busy laptop
   (mean 50µs, easily 200µs on a context switch); p95 is the right
   target for "agent loop latency budget". p99 is captured for
   reporting only — runner variability beyond 95th makes it a flaky
   gate.

4. **Warm-up + N-of-M**: 200 warm-up iters drop JIT / cache cold-start
   outliers; 5K iters is the measurement window (smaller than the
   10K script default so CI total stays <30s). On a healthy laptop
   p95 is ~0.5ms (50× under budget), so even a 5× regression still
   passes — gates the catastrophic regressions, not the µs-level
   bicker.

5. **Targets**:
   * ``ApprovalClassifier.classify_full`` p95 < 1.0 ms (~50× headroom)
   * ``PolicyEngineV2.evaluate_tool_call`` p95 < 5.0 ms (~10× headroom)
   * ``classify_shell_command`` cached / uncached speedup ≥ 10×
     (C22 P3-1 invariant; already covered by
     ``test_c22_shell_risk_lru`` but cross-checked here so a single
     "policy perf SLO" test pass / fail signals everything)
   * Audit writer: 100 enqueued records flushed within 200ms p95
     (C22 P3-2 SLO; ~2ms / record amortized)
"""

from __future__ import annotations

import asyncio
import statistics
import time
from pathlib import Path

import pytest

from openakita.core.policy_v2.classifier import ApprovalClassifier
from openakita.core.policy_v2.context import PolicyContext
from openakita.core.policy_v2.engine import PolicyEngineV2
from openakita.core.policy_v2.enums import ConfirmationMode, SessionRole
from openakita.core.policy_v2.models import ToolCallEvent
from openakita.core.policy_v2.schema import PolicyConfigV2
from openakita.core.policy_v2.shell_risk import classify_shell_command

# ---------------------------------------------------------------------------
# Constants — keep in sync with scripts/c11_perf_baseline.py SLO_BUDGET_MS
# ---------------------------------------------------------------------------

SLO_BUDGETS_MS: dict[str, float] = {
    "classify_full_p95": 1.0,
    "evaluate_tool_call_p95": 5.0,
    # C22 P3-2: audit batch end-to-end latency budget.
    # 100 records → AsyncBatchAuditWriter batched to disk; p95 200ms
    # is generous (real measurement ~30ms on dev laptop, but CI is
    # slower and bursty disk varies).
    "audit_writer_100record_flush_ms_p95": 200.0,
}

SHELL_LRU_SPEEDUP_MIN: float = 10.0

WARMUP_ITERS: int = 200
MEASURE_ITERS: int = 5_000

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


def _ctx() -> PolicyContext:
    return PolicyContext(
        session_id="perf-bench",
        workspace=Path.cwd(),
        channel="cli",
        is_owner=True,
        session_role=SessionRole.AGENT,
        confirmation_mode=ConfirmationMode.DEFAULT,
    )


def _percentile(samples: list[float], pct: float) -> float:
    if not samples:
        return 0.0
    sorted_s = sorted(samples)
    idx = int(len(sorted_s) * pct)
    idx = max(0, min(len(sorted_s) - 1, idx))
    return sorted_s[idx]


def _print_report(name: str, samples_ms: list[float], budget_ms: float) -> None:
    """Print results so ``pytest -s -m perf`` ops dashboards see numbers."""
    if not samples_ms:
        return
    p50 = statistics.median(samples_ms)
    p95 = _percentile(samples_ms, 0.95)
    p99 = _percentile(samples_ms, 0.99)
    mean_ms = statistics.fmean(samples_ms)
    max_ms = max(samples_ms)
    status = "PASS" if p95 <= budget_ms else "FAIL"
    print(
        f"\n[perf] {name:<36} "
        f"p50={p50:6.3f}ms  p95={p95:6.3f}ms  p99={p99:6.3f}ms  "
        f"mean={mean_ms:6.3f}ms  max={max_ms:7.3f}ms  "
        f"[{status} budget {budget_ms:.1f}ms]"
    )


# ---------------------------------------------------------------------------
# Budget parity guard
# ---------------------------------------------------------------------------


class TestBudgetParity:
    """Make sure ``tests/perf`` budgets and ``scripts/c11_perf_baseline.py``
    budgets don't drift silently. If someone bumps one, this test fails
    until the other matches — forcing a conscious decision.

    We compare via *text grep* of the script instead of importing it,
    because ``scripts/c11_perf_baseline.py`` mutates ``sys.path`` at
    import time (``sys.path.insert(0, str(SRC))``) — pulling it in via
    ``importlib`` would pollute every subsequent test's ``sys.path``
    with a duplicate entry. The grep approach also keeps the script
    importable by humans without side-effecting the test session.
    """

    _SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "c11_perf_baseline.py"

    @classmethod
    def _script_budget(cls, key: str) -> float:
        import re

        src = cls._SCRIPT_PATH.read_text(encoding="utf-8")
        m = re.search(rf'["\']?{re.escape(key)}["\']?\s*:\s*([\d.]+)', src)
        if not m:
            pytest.fail(
                f"Couldn't find SLO_BUDGET_MS[{key!r}] in "
                f"{cls._SCRIPT_PATH.name}. Either the script renamed the key "
                "(update grep here) or the budget vanished (re-add it)."
            )
        return float(m.group(1))

    @pytest.mark.perf
    def test_classify_budget_matches_baseline_script(self) -> None:
        script_budget = self._script_budget("classify_full_p95")
        assert SLO_BUDGETS_MS["classify_full_p95"] == script_budget, (
            f"Budget drift: tests/perf says "
            f"{SLO_BUDGETS_MS['classify_full_p95']}ms, "
            f"scripts/c11_perf_baseline.py says {script_budget}ms. "
            "Update both intentionally + docs/policy_v2_research.md."
        )

    @pytest.mark.perf
    def test_evaluate_budget_matches_baseline_script(self) -> None:
        script_budget = self._script_budget("evaluate_tool_call_p95")
        assert SLO_BUDGETS_MS["evaluate_tool_call_p95"] == script_budget


# ---------------------------------------------------------------------------
# Classifier + Engine SLO
# ---------------------------------------------------------------------------


class TestClassifierSlo:
    @pytest.mark.perf
    def test_classify_full_p95_within_budget(self) -> None:
        classifier = ApprovalClassifier()
        ctx = _ctx()

        # Warm up: drop JIT trace + LRU cold misses.
        for i in range(WARMUP_ITERS):
            t, p = TOOL_MIX[i % len(TOOL_MIX)]
            classifier.classify_full(t, p, ctx)

        durations: list[float] = []
        for i in range(MEASURE_ITERS):
            t, p = TOOL_MIX[i % len(TOOL_MIX)]
            t0 = time.perf_counter()
            classifier.classify_full(t, p, ctx)
            durations.append((time.perf_counter() - t0) * 1000.0)

        budget = SLO_BUDGETS_MS["classify_full_p95"]
        _print_report("ApprovalClassifier.classify_full", durations, budget)
        p95 = _percentile(durations, 0.95)
        assert p95 <= budget, (
            f"classify_full p95={p95:.3f}ms exceeds budget "
            f"{budget}ms. Either the engine got slower or the LRU "
            "cache is no longer effective; check shell_risk LRU, "
            "ClassifierResolver cache, and recent classifier.py "
            "changes."
        )


class TestEngineSlo:
    @pytest.mark.perf
    def test_evaluate_tool_call_p95_within_budget(self) -> None:
        engine = PolicyEngineV2(config=PolicyConfigV2())
        ctx = _ctx()

        for i in range(WARMUP_ITERS):
            t, p = TOOL_MIX[i % len(TOOL_MIX)]
            engine.evaluate_tool_call(ToolCallEvent(tool=t, params=p), ctx)

        durations: list[float] = []
        for i in range(MEASURE_ITERS):
            t, p = TOOL_MIX[i % len(TOOL_MIX)]
            evt = ToolCallEvent(tool=t, params=p)
            t0 = time.perf_counter()
            engine.evaluate_tool_call(evt, ctx)
            durations.append((time.perf_counter() - t0) * 1000.0)

        budget = SLO_BUDGETS_MS["evaluate_tool_call_p95"]
        _print_report("PolicyEngineV2.evaluate_tool_call", durations, budget)
        p95 = _percentile(durations, 0.95)
        assert p95 <= budget, (
            f"evaluate_tool_call p95={p95:.3f}ms exceeds budget "
            f"{budget}ms — engine hot path regression."
        )


# ---------------------------------------------------------------------------
# Shell-risk LRU speedup (P3-1 cross-check)
# ---------------------------------------------------------------------------


class TestShellRiskLruSpeedup:
    @pytest.mark.perf
    def test_cached_vs_uncached_speedup(self) -> None:
        """Reaffirm P3-1's LRU win in the perf SLO surface. If
        someone disables / breaks the cache, the unit test in
        ``tests/unit/test_c22_shell_risk_lru.py`` flags it locally; this
        cross-check catches it in CI perf runs even if the unit tests
        get re-organized."""
        cmd = "git status --porcelain --branch"

        # Warm up cache
        classify_shell_command.cache_clear()
        classify_shell_command(cmd)

        t0 = time.perf_counter()
        for _ in range(2000):
            classify_shell_command(cmd)
        cached = time.perf_counter() - t0

        classify_shell_command.cache_clear()
        t0 = time.perf_counter()
        for i in range(2000):
            classify_shell_command(f"{cmd} #{i}")
        uncached = time.perf_counter() - t0

        speedup = uncached / max(cached, 1e-9)
        print(
            f"\n[perf] classify_shell_command uncached={uncached*1000:.1f}ms "
            f"cached={cached*1000:.1f}ms speedup={speedup:.1f}x"
        )
        assert speedup >= SHELL_LRU_SPEEDUP_MIN, (
            f"LRU speedup {speedup:.1f}x below {SHELL_LRU_SPEEDUP_MIN}x "
            "floor — cache likely broken (P3-1 regression)."
        )


# ---------------------------------------------------------------------------
# Audit writer throughput (P3-2 cross-check)
# ---------------------------------------------------------------------------


class TestAuditWriterSlo:
    @pytest.mark.perf
    @pytest.mark.asyncio
    async def test_100_record_batch_flush_p95(self, tmp_path: Path) -> None:
        """Time end-to-end: enqueue 100 records + flush. Repeat 20 times
        and assert p95 < budget. This guards against a regression that
        (a) silently disables batching, (b) makes append_batch O(N²),
        or (c) breaks the producer-thread fast path."""
        from openakita.core.policy_v2.audit_chain import reset_writers_for_testing
        from openakita.core.policy_v2.audit_writer import (
            AsyncBatchAuditWriter,
            reset_for_testing,
        )

        reset_for_testing()
        reset_writers_for_testing()

        path = tmp_path / "perf_audit" / "policy_decisions.jsonl"
        writer = AsyncBatchAuditWriter(
            str(path),
            max_batch_size=64,
            max_batch_delay_ms=20,
            queue_maxsize=4096,
        )
        await writer.start()
        try:
            durations: list[float] = []
            for run in range(20):
                t0 = time.perf_counter()
                for i in range(100):
                    writer.enqueue({"ts": float(run * 1000 + i), "tool": "perf"})
                await writer.flush()
                durations.append((time.perf_counter() - t0) * 1000.0)
                # Brief yield between runs to let the writer settle.
                await asyncio.sleep(0)

            budget = SLO_BUDGETS_MS["audit_writer_100record_flush_ms_p95"]
            _print_report(
                "AsyncBatchAuditWriter 100-rec flush", durations, budget
            )
            p95 = _percentile(durations, 0.95)
            assert p95 <= budget, (
                f"100-record batch flush p95={p95:.1f}ms > budget "
                f"{budget}ms — async writer regression."
            )
        finally:
            await writer.stop()


# ---------------------------------------------------------------------------
# Sanity: marker registration
# ---------------------------------------------------------------------------


def test_perf_marker_registered() -> None:
    """Run on every plain ``pytest`` invocation (no -m filter) so we
    notice immediately if the ``perf`` marker config gets dropped from
    pyproject.toml. Doesn't itself need the marker (it's the meta
    check)."""
    from pathlib import Path

    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    content = pyproject.read_text(encoding="utf-8")
    assert 'markers = [' in content, (
        "pyproject.toml missing [tool.pytest.ini_options] markers list"
    )
    assert '"perf:' in content, (
        "pyproject.toml markers must register 'perf:' or pytest will "
        "warn on every C22 P3-3 run."
    )
