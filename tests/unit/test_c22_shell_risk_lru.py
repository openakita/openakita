"""C22 P3-1: ``classify_shell_command`` LRU cache regression + perf bench.

Background
==========

Plan §13.5.2 B mandated an LRU cache around the shell classification
hot path. Pre-C22 ``classify_shell_command`` was a plain regex scan over
~50 compiled patterns per call — fine in isolation but the engine hot
path runs it per ``run_shell`` / ``run_powershell`` invocation, and a
typical dev loop repeats the same 10–20 commands hundreds of times in
a session.

C22 P3-1 wraps an inner pure helper with ``functools.lru_cache``,
converting the list-typed kwargs (``extra_critical``, ``blocked_tokens``,
``excluded_patterns``, ...) to tuples so they become hashable cache
keys. The public API stays list-based (no caller churn).

Test scope
==========

- Cache wiring sanity (``cache_info`` exposed, hits/misses/maxsize work)
- Same command + same patterns → cache hit
- Different command → cache miss
- Different patterns → cache miss (config change invalidates implicitly)
- Empty / whitespace command → bypasses cache (preserves pre-C22 behaviour
  + avoids wasting cache slot on the trivial case)
- ``[]`` vs ``None`` semantic distinction preserved (blocked_tokens=[]
  must NOT re-enable defaults via tuple collapse)
- ``cache_clear`` works
- Bench: cached call should be measurably faster than uncached
"""

from __future__ import annotations

import time

import pytest

from openakita.core.policy_v2.shell_risk import (
    DEFAULT_BLOCKED_COMMANDS,
    ShellRiskLevel,
    classify_shell_command,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Each test gets a clean cache so hit/miss counts are deterministic."""
    classify_shell_command.cache_clear()
    yield
    classify_shell_command.cache_clear()


def test_cache_info_exposed() -> None:
    """Public API exposes cache_info / cache_clear for ops introspection."""
    info = classify_shell_command.cache_info()
    assert hasattr(info, "hits")
    assert hasattr(info, "misses")
    assert hasattr(info, "maxsize")
    assert info.maxsize >= 64, (
        f"LRU maxsize too small ({info.maxsize}); plan §13.5.2 B expects "
        "enough headroom for a typical agent session command diversity."
    )


def test_repeated_call_hits_cache() -> None:
    """Same (command, default patterns) twice → 1 miss + 1 hit."""
    classify_shell_command("ls -la")
    classify_shell_command("ls -la")
    info = classify_shell_command.cache_info()
    assert info.hits == 1
    assert info.misses == 1


def test_different_commands_separate_entries() -> None:
    """Distinct commands populate distinct cache slots."""
    classify_shell_command("ls")
    classify_shell_command("cat README.md")
    classify_shell_command("echo hello")
    info = classify_shell_command.cache_info()
    assert info.misses == 3
    assert info.hits == 0
    assert info.currsize == 3


def test_different_patterns_separate_entries() -> None:
    """Same command but different extra_critical / blocked_tokens → distinct
    cache keys (which is what hot-reload relies on for implicit
    invalidation)."""
    classify_shell_command("custom-cmd --flag", extra_critical=["custom-cmd"])
    classify_shell_command("custom-cmd --flag", extra_critical=["other-cmd"])
    classify_shell_command("custom-cmd --flag")  # default patterns
    info = classify_shell_command.cache_info()
    assert info.misses == 3, (
        "Same command with different pattern config must be separate cache "
        "entries — otherwise hot-reload of shell_risk.custom_critical would "
        "return stale results from before the config change."
    )


def test_empty_command_bypasses_cache() -> None:
    """Empty/whitespace input returns LOW without touching the cache, so the
    cache doesn't waste a slot on the trivial case (and the same null check
    runs even on cache hit for free)."""
    classify_shell_command("")
    classify_shell_command("   ")
    classify_shell_command(None)  # type: ignore[arg-type]
    info = classify_shell_command.cache_info()
    assert info.currsize == 0, (
        "Empty command must not enter the cache. Currsize > 0 means we're "
        "wasting LRU slots on trivial null inputs."
    )


class TestNoneVsEmptyListSemantics:
    """Regression for the C22 implementation: the ``[]`` vs ``None``
    distinction for ``blocked_tokens`` is load-bearing — ``[]`` means
    "explicitly opt out of blocked-token check", ``None`` means "use
    defaults". Collapsing both to ``None`` (a naive
    ``not value -> return None`` in ``_coerce_tuple``) was the first-try
    bug; this test pins the correct semantics."""

    def test_blocked_tokens_none_uses_defaults(self) -> None:
        """blocked_tokens=None → DEFAULT_BLOCKED_COMMANDS applied."""
        assert "regedit" in DEFAULT_BLOCKED_COMMANDS
        result = classify_shell_command("regedit /s f.reg")
        assert result == ShellRiskLevel.BLOCKED

    def test_blocked_tokens_empty_list_disables_check(self) -> None:
        """blocked_tokens=[] → skip the blocked check entirely; regedit
        falls through to LOW (not in any critical/high/medium pattern)."""
        result = classify_shell_command("regedit /s f.reg", blocked_tokens=[])
        assert result == ShellRiskLevel.LOW

    def test_empty_and_none_are_different_cache_keys(self) -> None:
        """Verify the cache key distinguishes them so the previous
        assertion couldn't accidentally be cached from a prior call."""
        classify_shell_command("regedit /s f.reg")  # None → BLOCKED
        classify_shell_command("regedit /s f.reg", blocked_tokens=[])  # [] → LOW
        info = classify_shell_command.cache_info()
        assert info.misses == 2, "() and None must be distinct cache keys"


def test_cache_clear_resets() -> None:
    """``classify_shell_command.cache_clear()`` is the supported way to
    force eviction (e.g. test fixtures, ops scripts that just reloaded
    POLICIES.yaml and want a deterministic state)."""
    classify_shell_command("ls")
    classify_shell_command("pwd")
    assert classify_shell_command.cache_info().currsize == 2

    classify_shell_command.cache_clear()
    info = classify_shell_command.cache_info()
    assert info.currsize == 0
    assert info.hits == 0
    assert info.misses == 0


def test_lru_eviction_under_pressure() -> None:
    """Filling beyond maxsize must evict oldest, not crash."""
    maxsize = classify_shell_command.cache_info().maxsize
    # Generate maxsize + 50 distinct commands.
    for i in range(maxsize + 50):
        classify_shell_command(f"echo run_{i}")
    info = classify_shell_command.cache_info()
    assert info.currsize == maxsize, (
        f"LRU should hold exactly maxsize entries under pressure, "
        f"got currsize={info.currsize}, maxsize={maxsize}"
    )


# ---------------------------------------------------------------------------
# Perf bench — not a strict SLO assertion (CI variance), but a regression
# warning. C22 numbers on dev laptop:
#   uncached: ~150-300µs / call
#   cached:   ~0.5-1.5µs / call
# Speedup ratio is what we lock in (>20×); absolute timings drift with hw.
# ---------------------------------------------------------------------------


def test_cached_call_is_significantly_faster_than_uncached() -> None:
    """Bench: 1000 calls of the same command vs 1000 calls of distinct
    commands. Cached path should be ≥10× faster — covers a wide range of
    machines and avoids flake from absolute timing on slow CI."""
    cmd = "git status --porcelain --branch"

    # Warm up: ensure 1 cache miss then 999 hits.
    classify_shell_command(cmd)
    t0 = time.perf_counter()
    for _ in range(1000):
        classify_shell_command(cmd)
    cached_elapsed = time.perf_counter() - t0

    classify_shell_command.cache_clear()
    t0 = time.perf_counter()
    for i in range(1000):
        classify_shell_command(f"{cmd} #{i}")  # unique suffix → cache miss every call
    uncached_elapsed = time.perf_counter() - t0

    speedup = uncached_elapsed / max(cached_elapsed, 1e-9)
    # Print so it shows in ``pytest -s`` for ops monitoring.
    print(
        f"\n[bench] shell_risk LRU: uncached={uncached_elapsed*1000:.1f}ms, "
        f"cached={cached_elapsed*1000:.1f}ms, speedup={speedup:.1f}x"
    )
    # Conservative bound; on dev laptop typical speedup is 100-300×.
    assert speedup >= 10.0, (
        f"Cached path is only {speedup:.1f}× faster than uncached. "
        "Either the cache isn't wired or pattern scan got cheaper than "
        "expected — investigate before assuming LRU is still valuable."
    )


def test_custom_pattern_lookup_still_works_after_caching() -> None:
    """Make sure caching didn't break the custom-pattern path. Same input
    twice with same custom patterns must hit cache and return same answer."""
    extras = ["my-dangerous-cmd"]
    r1 = classify_shell_command("my-dangerous-cmd --rm", extra_critical=extras)
    r2 = classify_shell_command("my-dangerous-cmd --rm", extra_critical=extras)
    assert r1 == r2 == ShellRiskLevel.CRITICAL
    info = classify_shell_command.cache_info()
    assert info.hits >= 1, "Cache should have served the second call"


def test_env_override_for_lru_size_documented() -> None:
    """``OPENAKITA_SHELL_LRU_SIZE`` env is the documented knob; verify the
    default name resolution path exists. (We don't reload the module here
    — that would require complex import gymnastics — just sanity-check
    the helper function accepts/rejects values correctly.)"""
    import os

    from openakita.core.policy_v2.shell_risk import _shell_lru_size

    # Unset → fallback default
    os.environ.pop("OPENAKITA_SHELL_LRU_SIZE", None)
    assert _shell_lru_size() == 512

    os.environ["OPENAKITA_SHELL_LRU_SIZE"] = "256"
    try:
        assert _shell_lru_size() == 256
    finally:
        os.environ.pop("OPENAKITA_SHELL_LRU_SIZE", None)

    # Bad values → fallback default (defensive)
    os.environ["OPENAKITA_SHELL_LRU_SIZE"] = "abc"
    try:
        assert _shell_lru_size() == 512
    finally:
        os.environ.pop("OPENAKITA_SHELL_LRU_SIZE", None)
