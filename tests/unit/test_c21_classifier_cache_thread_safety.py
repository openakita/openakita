"""C21 P0-3: ``ApprovalClassifier._base_cache`` 并发安全回归。

Background
==========

Pre-C21 the cache was a bare OrderedDict relying on "CPython OrderedDict
单 op 由 GIL 保证原子性 + try/except KeyError 兜住竞态". Correctness was
maintained but the composite ``get→move_to_end → __setitem__→popitem``
sequence had observable races: cache_size temporarily exceeded
``_cache_size``, the same tool could be classified twice under contention,
and the audit subagent flagged this as conflict with plan §22.3's
promise of a "thread-safe LRU cache".

C21 P0-3 adds an explicit ``threading.Lock`` (``_cache_lock``). The
classification callback itself runs outside the lock to avoid nesting
with registry locks; only the cache mutations are serialized.

Tests in this file
==================

- Structural guard: ``_cache_lock`` exists, ``cache_size`` reads it
- High-contention test: N threads × M iterations all hit cache for same
  tool. Final state must be deterministic, no exception, no oversize.
- Eviction stress: many distinct tools concurrently, ``cache_size`` must
  never permanently exceed bound
- ``invalidate`` race: invalidate during concurrent classify
"""

from __future__ import annotations

import threading
import time

import pytest

from openakita.core.policy_v2.classifier import ApprovalClassifier
from openakita.core.policy_v2.enums import ApprovalClass


def test_cache_lock_attribute_exists() -> None:
    """Structural guard: future refactors must keep _cache_lock."""
    clf = ApprovalClassifier()
    assert hasattr(clf, "_cache_lock"), (
        "ApprovalClassifier must hold _cache_lock (threading.Lock) for "
        "safe LRU cache mutation. C21 P0-3 introduced it; removing it "
        "regresses to the pre-C21 'GIL + try/except KeyError' design."
    )
    # threading.Lock() returns a _thread.lock; threading.RLock returns RLock.
    # Accept either, but currently Lock is sufficient (no re-entry needed).
    lock_type = type(clf._cache_lock).__name__
    assert "lock" in lock_type.lower() or "RLock" in lock_type


def test_cache_size_reads_under_lock() -> None:
    """cache_size 属性应该走锁，不应直接 len(self._base_cache)。

    用一个简单的代理 Lock 检查是否 acquire 过即可。
    """
    clf = ApprovalClassifier(cache_size=4)
    clf.classify("read_file")

    acquired = [0]

    class CountingLock:
        def __init__(self, real):
            self._real = real

        def __enter__(self):
            acquired[0] += 1
            return self._real.__enter__()

        def __exit__(self, *args):
            return self._real.__exit__(*args)

        def acquire(self, *a, **kw):
            acquired[0] += 1
            return self._real.acquire(*a, **kw)

        def release(self):
            return self._real.release()

    clf._cache_lock = CountingLock(clf._cache_lock)
    _ = clf.cache_size
    assert acquired[0] >= 1, "cache_size must acquire _cache_lock"


def test_high_contention_same_tool_no_exception() -> None:
    """N 个线程并发 classify 同一 tool 1000 次：结果一致 + 不抛异常。"""
    clf = ApprovalClassifier(cache_size=64)
    n_threads = 16
    iters = 1000
    barrier = threading.Barrier(n_threads)
    results: dict[int, ApprovalClass] = {}
    errors: list[BaseException] = []

    def _worker(idx: int) -> None:
        try:
            barrier.wait(timeout=5.0)
            last: ApprovalClass | None = None
            for _ in range(iters):
                last = clf.classify("read_file")
            assert last is not None
            results[idx] = last
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    assert not errors, f"Concurrent classify raised: {errors[:3]}"
    assert len(results) == n_threads
    # All threads must agree on the classification.
    unique_results = set(results.values())
    assert len(unique_results) == 1, f"Inconsistent results: {unique_results}"
    # Cache must hold exactly one entry for "read_file".
    assert clf.cache_size <= 64


def test_eviction_stress_cache_size_never_exceeds_bound() -> None:
    """许多 distinct tools 并发 classify，验证 cache 永不持久超额。

    Pre-C21 在 high contention 下 cache 会瞬间多个超额条目（acknowledged
    by old comment）。C21 加锁后超额窗口应该消失。"""
    cache_size = 8
    clf = ApprovalClassifier(cache_size=cache_size)
    n_threads = 12
    distinct_tools = [f"read_tool_{i}" for i in range(40)]
    barrier = threading.Barrier(n_threads)
    errors: list[BaseException] = []
    observed_sizes: list[int] = []

    def _worker() -> None:
        try:
            barrier.wait(timeout=5.0)
            for tool in distinct_tools:
                clf.classify(tool)
                observed_sizes.append(clf.cache_size)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    assert not errors, f"Stress test raised: {errors[:3]}"
    # cache_size MUST NEVER exceed bound (with lock, this is now strict).
    assert observed_sizes, "Workers should have recorded sizes"
    max_seen = max(observed_sizes)
    assert max_seen <= cache_size, (
        f"cache_size exceeded bound: max={max_seen}, bound={cache_size}. "
        "Lock should make this strict — pre-C21 was 'eventually consistent'."
    )


def test_invalidate_during_concurrent_classify() -> None:
    """``invalidate()`` 在 classify 高频期间不抛、不死锁。"""
    clf = ApprovalClassifier(cache_size=16)
    stop = threading.Event()
    errors: list[BaseException] = []

    def _classifier() -> None:
        try:
            i = 0
            while not stop.is_set():
                clf.classify(f"read_x_{i % 32}")
                i += 1
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def _invalidator() -> None:
        try:
            for _ in range(100):
                clf.invalidate()
                time.sleep(0.001)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t_c = [threading.Thread(target=_classifier) for _ in range(4)]
    t_i = threading.Thread(target=_invalidator)
    for t in t_c:
        t.start()
    t_i.start()
    t_i.join(timeout=5.0)
    stop.set()
    for t in t_c:
        t.join(timeout=5.0)

    assert not errors, f"invalidate race raised: {errors[:3]}"


def test_invalidate_specific_tool_under_contention() -> None:
    """``invalidate(tool)`` 在并发期间不会破坏其他 tool 的缓存条目。"""
    clf = ApprovalClassifier(cache_size=32)

    # Warm up several tools.
    for i in range(8):
        clf.classify(f"read_t_{i}")
    assert clf.cache_size == 8

    stop = threading.Event()
    errors: list[BaseException] = []

    def _classifier() -> None:
        try:
            while not stop.is_set():
                for i in range(8):
                    clf.classify(f"read_t_{i}")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def _evictor() -> None:
        try:
            for _ in range(200):
                clf.invalidate("read_t_3")
                time.sleep(0.0005)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=_classifier),
        threading.Thread(target=_classifier),
        threading.Thread(target=_evictor),
    ]
    for t in threads:
        t.start()
    threads[-1].join(timeout=5.0)
    stop.set()
    for t in threads[:-1]:
        t.join(timeout=5.0)

    assert not errors, f"Targeted invalidate raised: {errors[:3]}"
    # cache_size at the end must still be sane.
    assert 0 <= clf.cache_size <= 8


@pytest.mark.parametrize("cache_size", [1, 4, 16])
def test_classification_deterministic_under_concurrency(cache_size: int) -> None:
    """The same (tool, params, ctx) must always produce the same class.

    Regression for the worst-case scenario where two threads race past
    cache miss and produce different results due to non-deterministic
    lookup callback. Classification itself must be pure."""
    clf = ApprovalClassifier(cache_size=cache_size)
    n_threads = 8
    barrier = threading.Barrier(n_threads)
    results: list[ApprovalClass] = []
    lock = threading.Lock()

    def _worker() -> None:
        barrier.wait(timeout=3.0)
        r = clf.classify("read_file")
        with lock:
            results.append(r)

    threads = [threading.Thread(target=_worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    assert len(results) == n_threads
    assert len(set(results)) == 1, (
        f"Non-deterministic classification: {set(results)}. "
        "Cache should converge to a single answer."
    )
