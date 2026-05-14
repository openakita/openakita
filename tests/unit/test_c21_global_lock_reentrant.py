"""C21 P0-1: 验证 ``global_engine._lock`` 是 RLock（可重入）。

历史背景
========

``global_engine._lock`` 过去是 ``threading.Lock``（非可重入）。
``rebuild_engine_v2`` 在持锁状态下会触发若干子系统的 lazy init / reset：

- ``_audit_env_overrides`` → ``get_audit_logger`` 单例首次构造 →
  ``get_config_v2`` → 尝试再次 acquire ``_lock`` → **死锁**。
  （C18 二轮 audit BUG-C2，已在 ``test_c18_second_pass_audit.py`` 用
  逐点防御代码绕开：``_audit_env_overrides`` 不再走 ``get_audit_logger``
  单例路径而是用传入的 ``cfg`` 直接构造一次性 ``AuditLogger``。）

- ``ChainedJsonlWriter._get_rotation_config`` → 早期实现走
  ``get_config_v2()`` → 重入 → **死锁**。
  （C20 Phase A P-A.1，已用"直读 ``global_engine._config`` 模块属性"绕开，
  并加 AST 静态守卫禁止 ``get_config_v2`` 调用。）

这两次都是 patch 修复，没有解决"锁本身设计就不容重入"这一根因。
C21 把 ``_lock`` 换成 ``threading.RLock``，从源头根除整类隐患——同线程
重入合法，已有的逐点防御依然保留，是双层保险。

本测试集做两件事
================

1. **结构守卫**：断言 ``_lock`` 是 RLock。这条会拦下"未来某次重构把
   ``_lock`` 改回 ``threading.Lock``"的回退。
2. **功能守卫**：在持锁状态下从同一线程再次 acquire / 调
   ``get_config_v2``，必须在合理超时内完成而不是死锁。
"""

from __future__ import annotations

import threading

import pytest

from openakita.core.policy_v2 import global_engine


@pytest.fixture(autouse=True)
def _isolated_engine():
    """每个 case 跑前 reset 全局引擎，避免相互污染。"""
    global_engine.reset_engine_v2(clear_explicit_lookup=False)
    yield
    global_engine.reset_engine_v2(clear_explicit_lookup=False)


def test_global_lock_is_rlock_structurally() -> None:
    """结构守卫：``_lock`` 必须是 RLock 实例。

    threading.RLock() / threading.Lock() 返回的对象 type 实际是
    ``_thread.RLock`` / ``_thread.lock``。最稳的判别法是看类型名而不
    是 isinstance（因为 RLock 的 factory 是 function 不是 class）。
    """
    lock_type_name = type(global_engine._lock).__name__
    # CPython 实现：RLock 在 _thread 模块叫 RLock，在 threading 层 wrapper
    # 叫 _RLock。两者都可接受；普通 Lock 叫 ``lock``（小写）则不接受。
    assert "RLock" in lock_type_name, (
        f"global_engine._lock must be RLock, got {lock_type_name}. "
        "Reverting to threading.Lock will reintroduce BUG-C2 / C20 P-A.1 "
        "style deadlocks when rebuild_engine_v2 触发子系统 lazy-init。"
    )


def test_global_lock_allows_same_thread_reentry() -> None:
    """功能守卫：同线程重入 acquire 必须立即返回 True 而不是阻塞。"""
    lock = global_engine._lock
    acquired1 = lock.acquire(timeout=1.0)
    assert acquired1, "首次 acquire 失败（不应发生）"
    try:
        acquired2 = lock.acquire(timeout=1.0)
        assert acquired2, (
            "同线程二次 acquire 阻塞——说明 _lock 不是 RLock。"
            "这正是 BUG-C2 / C20 P-A.1 死锁的根因模式。"
        )
        if acquired2:
            lock.release()
    finally:
        lock.release()


def test_get_config_under_held_lock_does_not_deadlock() -> None:
    """端到端守卫：复现历史死锁路径。

    持有 ``_lock`` 再调 ``get_config_v2()``——后者内部会再次 acquire
    同一把锁。RLock 下不死锁、普通 Lock 下卡死。用 timeout 保护，
    超时即判定 fail。
    """
    done = threading.Event()
    cfg_holder: dict[str, object] = {}

    def _worker() -> None:
        with global_engine._lock:
            cfg = global_engine.get_config_v2()
            cfg_holder["cfg"] = cfg
        done.set()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    finished = done.wait(timeout=3.0)
    assert finished, (
        "持有 _lock 调 get_config_v2() 超时——_lock 非可重入。"
        "RLock 修复（C21 P0-1）应让这条路径秒回。"
    )
    assert "cfg" in cfg_holder, "get_config_v2() 应返回 PolicyConfigV2 实例"


def test_rebuild_engine_under_held_lock_does_not_deadlock() -> None:
    """端到端守卫：``rebuild_engine_v2`` 本身就持 ``_lock``。

    在测试线程已经持锁的状态下调 rebuild，等价于"嵌套调用"。RLock
    必须容许。这条 case 防止未来有人把 ``with _lock`` 包到
    ``rebuild_engine_v2`` 外层调用方时立刻翻车。
    """
    done = threading.Event()
    err: list[BaseException] = []

    def _worker() -> None:
        try:
            with global_engine._lock:
                global_engine.rebuild_engine_v2()
        except BaseException as exc:  # noqa: BLE001
            err.append(exc)
        finally:
            done.set()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    finished = done.wait(timeout=5.0)
    assert finished, "rebuild_engine_v2 在持锁线程内部死锁"
    assert not err, f"rebuild 抛错: {err[0]!r}"


def test_concurrent_get_engine_still_serializes() -> None:
    """RLock 不削弱跨线程互斥：另一线程要 acquire 仍需等待持锁者释放。

    这是为了证明换成 RLock 不会顺手破坏"防止并发首次调用产生两个引
    擎"这个原始设计目标。
    """
    global_engine.reset_engine_v2(clear_explicit_lookup=False)
    held = threading.Event()
    release = threading.Event()
    other_done = threading.Event()
    timing: dict[str, float] = {}

    def _holder() -> None:
        with global_engine._lock:
            held.set()
            # 持锁 0.3s 让另一线程明显阻塞
            release.wait(timeout=1.0)

    def _other() -> None:
        import time

        held.wait(timeout=1.0)
        t0 = time.monotonic()
        global_engine.get_engine_v2()
        timing["elapsed"] = time.monotonic() - t0
        other_done.set()

    t1 = threading.Thread(target=_holder, daemon=True)
    t2 = threading.Thread(target=_other, daemon=True)
    t1.start()
    t2.start()
    import time as _time

    _time.sleep(0.3)
    release.set()
    assert other_done.wait(timeout=2.0), "其他线程 get_engine_v2 卡死"
    # 应该等到 release.set() 之后才完成，即至少阻塞过 ~0.3s
    assert timing.get("elapsed", 0.0) >= 0.2, (
        f"跨线程互斥被削弱：elapsed={timing.get('elapsed')}s "
        "（RLock 仅同线程重入合法，跨线程仍应排队）"
    )
