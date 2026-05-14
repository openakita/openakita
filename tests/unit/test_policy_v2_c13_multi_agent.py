"""C13 — Multi-agent confirm bubble + delegate_chain propagation.

Coverage:

1. ``build_policy_context(parent_ctx=...)`` returns a derive_child copy that
   preserves root_user_id / delegate_chain / safety_immune / replay /
   trusted_paths, while letting local user_message / channel / metadata
   layer on top.
2. sub-agent inheritance is automatic: when called without parent_ctx,
   build_policy_context still goes the legacy session-based path
   (regression-safe for top-level agents).
3. ``UIConfirmBus.find_dedup_leader`` returns existing leader's confirm_id
   when (session, dedup_key) matches; returns None for fresh keys.
4. follower wait_for_resolution sees the same decision as the leader,
   even when the leader's caller calls ``cleanup`` immediately after
   ``resolve`` (the race that motivated ``_pending_cleanup``).
5. Two parallel followers and one leader all read the same decision
   exactly once each, and cleanup happens only after the last follower
   deregisters.
6. ``_compute_confirm_dedup_key`` is stable + collision-safe for
   permutations of the same dict.
7. R4-3 spawn unattended: when parent ctx has ``is_unattended=True``,
   derive_child propagates it to the sub-agent ctx (so step 1.5 of the
   engine treats sub-agent's CONFIRM as deferred-to-owner, not human ask).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from openakita.core.policy_v2 import PolicyContext
from openakita.core.policy_v2.adapter import build_policy_context
from openakita.core.reasoning_engine import _compute_confirm_dedup_key
from openakita.core.ui_confirm_bus import UIConfirmBus


# ---------------------------------------------------------------------------
# Phase A: parent_ctx inheritance in build_policy_context
# ---------------------------------------------------------------------------


def test_build_policy_context_inherits_from_parent_ctx() -> None:
    parent = PolicyContext(
        session_id="root-session",
        workspace=Path("/wsp/root"),
        is_owner=True,
        root_user_id="root-uid",
        delegate_chain=["root"],
        is_unattended=False,
        unattended_strategy="",
        safety_immune_paths=("/etc/passwd",),
    )
    child = build_policy_context(
        session=None,
        session_id="child-session",
        parent_ctx=parent,
        child_agent_name="specialist_a",
        user_message="local task message",
    )
    assert child.session_id == "child-session"
    assert child.root_user_id == "root-uid"
    assert child.delegate_chain == ["root", "specialist_a"]
    assert child.workspace == Path("/wsp/root"), "workspace 默认继承父"
    assert child.user_message == "local task message", "user_message 本地覆盖"
    assert child.safety_immune_paths == ("/etc/passwd",), "immune paths 继承"


def test_build_policy_context_parent_ctx_overrides_session_path() -> None:
    """parent_ctx 存在时不走 session metadata 推断路径。"""
    parent = PolicyContext(
        session_id="root-session",
        workspace=Path("."),
        is_owner=False,  # parent 是非 owner
        root_user_id="root-uid",
        delegate_chain=["root"],
    )
    # 即使我们传入 is_owner=True，也以父继承为准（child 不能 escalate）
    child = build_policy_context(
        session=None,
        parent_ctx=parent,
        child_agent_name="specialist",
        is_owner=True,
    )
    assert child.is_owner is False, "child 不能从 is_owner=True 入参覆盖父的 False"


# ---------------------------------------------------------------------------
# 二轮 audit Fix #1: session_role 必须取 caller mode（sub-agent profile.role）
# ---------------------------------------------------------------------------


def test_build_policy_context_child_session_role_honors_caller_mode() -> None:
    """二轮 audit Fix #1：sub-agent 自己的 profile.role 不能被父 ctx 覆盖。

    orchestrator._call_agent 根据 sub-agent 的 profile.role 计算 _mode 并
    显式传 chat_with_session(mode=_mode)；agent.py 再传给 build_policy_context。
    若 child.session_role 直接继承父 → coordinator 子 agent 会按 agent 矩阵
    决策，是真 bug。
    """
    from openakita.core.policy_v2 import SessionRole

    # 父是 plan 模式（read-only 意图），子是 agent 工作者
    parent = PolicyContext(
        session_id="root",
        workspace=Path("."),
        session_role=SessionRole.PLAN,
    )
    child = build_policy_context(
        parent_ctx=parent,
        child_agent_name="worker",
        mode="agent",
    )
    assert child.session_role == SessionRole.AGENT, "caller mode 必须胜过父继承"

    # 反向：父是 agent，子是 coordinator 协调者
    parent2 = PolicyContext(
        session_id="root2",
        workspace=Path("."),
        session_role=SessionRole.AGENT,
    )
    child2 = build_policy_context(
        parent_ctx=parent2,
        child_agent_name="coord",
        mode="coordinator",
    )
    assert child2.session_role == SessionRole.COORDINATOR


# ---------------------------------------------------------------------------
# 二轮 audit Fix #2: parent_ctx 路径忽略 channel 入参，总是用父的
# ---------------------------------------------------------------------------


def test_build_policy_context_parent_ctx_uses_parent_channel() -> None:
    """二轮 audit Fix #2：channel 是 session-level 字段，sub-agent 与父共享
    session，channel 必然相同；不应通过参数额外 override。"""
    parent = PolicyContext(
        session_id="root",
        workspace=Path("."),
        channel="im:telegram",
    )
    # caller 显式传"desktop"也不该让 child 偏离父
    child = build_policy_context(
        parent_ctx=parent,
        child_agent_name="x",
        channel="desktop",
    )
    assert child.channel == "im:telegram"
    # 反向：caller 传"webhook"，child 仍跟父走
    child2 = build_policy_context(
        parent_ctx=parent,
        child_agent_name="x",
        channel="webhook",
    )
    assert child2.channel == "im:telegram"


def test_build_policy_context_unattended_propagates_to_child() -> None:
    """R4-3: 父 ctx is_unattended=True → 子 ctx 自动继承（spawn_agent 路径）。"""
    parent = PolicyContext(
        session_id="task-sched-1",
        workspace=Path("."),
        is_unattended=True,
        unattended_strategy="defer_to_owner",
        delegate_chain=["scheduler_root"],
        root_user_id="owner-uid",
    )
    child = build_policy_context(
        session=None,
        parent_ctx=parent,
        child_agent_name="spawned_worker",
    )
    assert child.is_unattended is True
    assert child.unattended_strategy == "defer_to_owner"
    assert child.delegate_chain == ["scheduler_root", "spawned_worker"]
    assert child.root_user_id == "owner-uid"


def test_build_policy_context_without_parent_ctx_keeps_legacy_path() -> None:
    """顶层 agent 不传 parent_ctx 时，走传统 session metadata 推断路径。"""
    ctx = build_policy_context(
        session=None,
        session_id="top-level-session",
        user_message="hi",
    )
    assert ctx.root_user_id is None
    assert ctx.delegate_chain == []
    assert ctx.session_id == "top-level-session"


# ---------------------------------------------------------------------------
# Phase C: UIConfirmBus dedup coalescer
# ---------------------------------------------------------------------------


def test_bus_find_dedup_leader_returns_none_on_empty() -> None:
    bus = UIConfirmBus()
    assert bus.find_dedup_leader(session_id="s1", dedup_key="key-a") is None


def test_bus_find_dedup_leader_matches_session_and_key() -> None:
    bus = UIConfirmBus()
    bus.store_pending(
        "leader-id-1",
        "write_file",
        {"path": "a.txt"},
        session_id="s1",
        dedup_key="key-a",
    )
    assert (
        bus.find_dedup_leader(session_id="s1", dedup_key="key-a")
        == "leader-id-1"
    )
    assert bus.find_dedup_leader(session_id="s2", dedup_key="key-a") is None
    assert bus.find_dedup_leader(session_id="s1", dedup_key="key-b") is None


def test_bus_find_dedup_leader_empty_key_returns_none() -> None:
    """空 dedup_key 兜底为 None（opt-out path）。"""
    bus = UIConfirmBus()
    bus.store_pending(
        "leader-id",
        "tool",
        {},
        session_id="s1",
        dedup_key=None,
    )
    assert bus.find_dedup_leader(session_id="s1", dedup_key="") is None


@pytest.mark.asyncio
async def test_bus_follower_reads_decision_when_leader_cleanup_eager() -> None:
    """R4-2 核心：leader 调 cleanup 不能让 follower 读到 'deny'。

    模拟 delegate_parallel 场景：leader 和 follower 都 wait 在同一
    confirm_id，外部 resolve("allow_session") → ev.set() 唤醒两者。
    leader 唤醒后立即 cleanup（生产代码路径），follower 才被调度。
    没有 _pending_cleanup defer：follower 会读到空 _decisions → "deny"。
    """
    bus = UIConfirmBus()
    bus.store_pending(
        "leader-1",
        "write_file",
        {"path": "/tmp/x"},
        session_id="s1",
        dedup_key="key-x",
    )
    bus.prepare("leader-1")
    bus.register_follower("leader-1")

    async def leader_path() -> str:
        decision = await bus.wait_for_resolution("leader-1", timeout=5.0)
        bus.cleanup("leader-1")  # 立刻清，模拟生产路径
        return decision

    async def follower_path() -> str:
        try:
            return await bus.wait_for_resolution("leader-1", timeout=5.0)
        finally:
            bus.deregister_follower("leader-1")

    leader_task = asyncio.create_task(leader_path())
    follower_task = asyncio.create_task(follower_path())
    await asyncio.sleep(0.01)  # 让两者都进 wait
    # 模拟用户在 UI 点 allow_session
    bus.resolve("leader-1", "allow_session")
    leader_dec, follower_dec = await asyncio.gather(leader_task, follower_task)
    assert leader_dec == "allow_session"
    assert follower_dec == "allow_session", "follower 必须读到与 leader 一致的决定"


@pytest.mark.asyncio
async def test_bus_cleanup_flushed_after_last_follower_deregisters() -> None:
    bus = UIConfirmBus()
    bus.store_pending(
        "L",
        "write_file",
        {"path": "p"},
        session_id="s1",
        dedup_key="k",
    )
    bus.prepare("L")
    bus.register_follower("L")
    bus.register_follower("L")
    bus.resolve("L", "allow_once")
    # leader 调 cleanup，但 followers 还没 deregister → 真清被 defer
    bus.cleanup("L")
    assert "L" in bus._events, "应 defer：still have followers"
    assert "L" in bus._pending_cleanup
    bus.deregister_follower("L")
    assert "L" in bus._events, "还剩 1 个 follower"
    bus.deregister_follower("L")
    assert "L" not in bus._events, "最后一个 follower 离开 → 真清生效"
    assert "L" not in bus._pending_cleanup


def test_bus_cleanup_immediate_when_no_followers() -> None:
    """无 followers 时 cleanup 行为不回归。"""
    bus = UIConfirmBus()
    bus.store_pending("X", "t", {}, session_id="s", dedup_key="k")
    bus.prepare("X")
    bus.resolve("X", "allow_once")
    bus.cleanup("X")
    assert "X" not in bus._events
    assert "X" not in bus._decisions
    assert "X" not in bus._pending_cleanup


# ---------------------------------------------------------------------------
# Phase C: dedup_key fingerprint stability
# ---------------------------------------------------------------------------


def test_compute_confirm_dedup_key_stable_across_dict_order() -> None:
    """不同 key 顺序的等价 dict 应产生同一 dedup_key。"""
    k1 = _compute_confirm_dedup_key(
        "write_file", {"path": "/a", "content": "hello"}
    )
    k2 = _compute_confirm_dedup_key(
        "write_file", {"content": "hello", "path": "/a"}
    )
    assert k1 == k2 != ""


def test_compute_confirm_dedup_key_diff_on_tool_name() -> None:
    k1 = _compute_confirm_dedup_key("write_file", {"path": "/a"})
    k2 = _compute_confirm_dedup_key("delete_file", {"path": "/a"})
    assert k1 != k2


def test_compute_confirm_dedup_key_diff_on_params() -> None:
    k1 = _compute_confirm_dedup_key("write_file", {"path": "/a"})
    k2 = _compute_confirm_dedup_key("write_file", {"path": "/b"})
    assert k1 != k2


def test_compute_confirm_dedup_key_empty_tool_name() -> None:
    assert _compute_confirm_dedup_key("", {"a": 1}) == ""


def test_compute_confirm_dedup_key_nondict_params() -> None:
    """非 dict 参数走 str fallback，仍可哈希。"""
    k1 = _compute_confirm_dedup_key("tool", "raw string")
    k2 = _compute_confirm_dedup_key("tool", "raw string")
    assert k1 == k2 != ""


# ---------------------------------------------------------------------------
# Phase A: derive_child boundary case — empty child_agent_name
# ---------------------------------------------------------------------------


def test_build_policy_context_empty_child_name_falls_back() -> None:
    parent = PolicyContext(session_id="r", workspace=Path("."))
    child = build_policy_context(parent_ctx=parent, child_agent_name="")
    assert child.delegate_chain == ["sub_agent"]


def test_build_policy_context_no_session_id_inherits_parent() -> None:
    parent = PolicyContext(session_id="root", workspace=Path("."))
    child = build_policy_context(parent_ctx=parent, child_agent_name="x")
    assert child.session_id == "root", "session_id 缺省继承父"


def test_build_policy_context_extra_metadata_merges_with_parent() -> None:
    parent = PolicyContext(
        session_id="r",
        workspace=Path("."),
        metadata={"a": 1, "b": 2},
    )
    child = build_policy_context(
        parent_ctx=parent,
        child_agent_name="x",
        extra_metadata={"b": 99, "c": 3},
    )
    assert child.metadata == {"a": 1, "b": 99, "c": 3}


# ---------------------------------------------------------------------------
# 二轮 audit Fix #8: cleanup_session 同步清 dedup 状态
# ---------------------------------------------------------------------------


def test_bus_cleanup_session_purges_dedup_state() -> None:
    """二轮 audit Fix #8：cleanup_session 必须同步清 _events / _decisions /
    _dedup_followers / _pending_cleanup —— 否则一个长生命进程频繁 spawn +
    teardown session 会累积 orphan follower counter。"""
    bus = UIConfirmBus()
    bus.store_pending(
        "L",
        "write_file",
        {"path": "p"},
        session_id="s_to_purge",
        dedup_key="k",
    )
    bus.prepare("L")
    bus.register_follower("L")
    bus.cleanup("L")  # 由于有 follower → 进入 _pending_cleanup
    assert "L" in bus._pending_cleanup, "pre-condition: cleanup deferred"
    assert "L" in bus._events
    assert bus._dedup_followers.get("L", 0) == 1
    # session 被外部回收（disconnect / cleanup endpoint 等）
    bus.cleanup_session("s_to_purge")
    assert "L" not in bus._pending
    assert "L" not in bus._events
    assert "L" not in bus._decisions
    assert "L" not in bus._dedup_followers
    assert "L" not in bus._pending_cleanup


def test_bus_cleanup_session_only_affects_target_session() -> None:
    """跨 session 隔离：清 A session 不应影响 B session 的 dedup 状态。"""
    bus = UIConfirmBus()
    bus.store_pending(
        "LA", "t", {}, session_id="sA", dedup_key="ka"
    )
    bus.prepare("LA")
    bus.register_follower("LA")
    bus.store_pending(
        "LB", "t", {}, session_id="sB", dedup_key="kb"
    )
    bus.prepare("LB")
    bus.register_follower("LB")

    bus.cleanup_session("sA")
    assert "LA" not in bus._pending
    assert "LA" not in bus._dedup_followers
    assert "LB" in bus._pending, "session sB 不应被波及"
    assert bus._dedup_followers["LB"] == 1


# ---------------------------------------------------------------------------
# 死代码 sanity: tool_executor _security_confirm marker 不携带 C13 字段
# （Fix #5：删除给死代码喂数据的拷贝）
# ---------------------------------------------------------------------------


def test_tool_executor_security_confirm_marker_has_no_c13_fields() -> None:
    """二轮 audit Fix #5：``_security_confirm`` marker 在 docs §2.1 中标注
    为 lying-bug 残留，无下游消费者。C13 一轮误把 delegate_chain / root_user_id
    塞进去是"给死代码喂数据"，本测试钉死该 marker 不带 C13 字段，未来
    contributor 不会重蹈覆辙。"""
    import inspect

    import openakita.core.tool_executor as te

    src = inspect.getsource(te)
    # 找到 _security_confirm 块
    block_start = src.find('"_security_confirm": {')
    assert block_start != -1, "marker 块应存在（保留 schema 向后兼容）"
    block_end = src.find("},", block_start)
    block_body = src[block_start:block_end]
    assert "delegate_chain" not in block_body, (
        "C13 字段不应注入到无消费者的 dead marker"
    )
    assert "root_user_id" not in block_body, (
        "C13 字段不应注入到无消费者的 dead marker"
    )
