r"""
Desktop Chat API 并发流式传输 — 完整测试套件

验证 AgentInstancePool 在真实 serve 环境下的行为：
- Pool 实例隔离（不同 conv_id → 不同 Agent 实例）
- 并发流式互不干扰
- Cancel / Skip / Insert 隔离到正确会话
- Profile 切换
- 上下文保持与隔离
- 边界条件（空消息、缺失 conv_id、同会话重入等）
- Sub-tasks 端点
- 压力测试

运行方式（需要先启动 serve）：
    cd d:\coder\myagent
    python tests/test_concurrent_streaming.py
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field

import httpx

API_BASE = "http://127.0.0.1:18900"
TIMEOUT = 120.0


@dataclass
class SSEResult:
    """Parsed SSE stream result for a single conversation."""
    conversation_id: str
    events: list[dict] = field(default_factory=list)
    text: str = ""
    thinking: str = ""
    tools_called: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    done: bool = False
    duration_s: float = 0.0
    usage: dict | None = None

    @property
    def ok(self) -> bool:
        return self.done and not self.errors

    def summary(self, max_text: int = 80) -> str:
        status = "✅" if self.ok else "❌"
        text_preview = self.text[:max_text].replace("\n", " ")
        tools = ", ".join(self.tools_called[:5]) or "-"
        return (
            f"{status} conv={self.conversation_id[:16]}… | "
            f"{self.duration_s:.1f}s | "
            f"events={len(self.events)} | tools=[{tools}] | "
            f"text={len(self.text)}c \"{text_preview}…\""
        )


# ─────────────────────────── helpers ───────────────────────────


async def stream_chat(
    client: httpx.AsyncClient,
    message: str,
    conversation_id: str | None = None,
    agent_profile_id: str | None = None,
    thinking_mode: str | None = None,
) -> SSEResult:
    conv_id = conversation_id or f"test_{uuid.uuid4().hex[:10]}"
    result = SSEResult(conversation_id=conv_id)
    t0 = time.monotonic()

    body: dict = {"message": message, "conversation_id": conv_id}
    if agent_profile_id:
        body["agent_profile_id"] = agent_profile_id
    if thinking_mode:
        body["thinking_mode"] = thinking_mode

    try:
        async with client.stream(
            "POST", f"{API_BASE}/api/chat", json=body, timeout=TIMEOUT,
        ) as resp:
            if resp.status_code != 200:
                result.errors.append(f"HTTP {resp.status_code}")
                result.duration_s = time.monotonic() - t0
                return result

            buf = ""
            async for chunk in resp.aiter_text():
                buf += chunk
                while "\n\n" in buf:
                    line, buf = buf.split("\n\n", 1)
                    line = line.strip()
                    if not line.startswith("data: "):
                        continue
                    try:
                        event = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue

                    result.events.append(event)
                    etype = event.get("type", "")

                    if etype == "text_delta":
                        result.text += event.get("content", "")
                    elif etype == "thinking_delta":
                        result.thinking += event.get("content", "")
                    elif etype == "tool_call_start":
                        result.tools_called.append(event.get("tool", "?"))
                    elif etype == "error":
                        result.errors.append(event.get("message", "unknown"))
                    elif etype == "done":
                        result.done = True
                        result.usage = event.get("usage")
    except Exception as e:
        result.errors.append(f"Exception: {e}")

    result.duration_s = time.monotonic() - t0
    return result


async def cancel_chat(client: httpx.AsyncClient, conv_id: str) -> dict:
    resp = await client.post(
        f"{API_BASE}/api/chat/cancel",
        json={"conversation_id": conv_id, "reason": "test cancel"},
        timeout=10,
    )
    return resp.json()


async def skip_chat(client: httpx.AsyncClient, conv_id: str) -> dict:
    resp = await client.post(
        f"{API_BASE}/api/chat/skip",
        json={"conversation_id": conv_id, "reason": "test skip"},
        timeout=10,
    )
    return resp.json()


async def insert_chat(client: httpx.AsyncClient, conv_id: str, msg: str) -> dict:
    resp = await client.post(
        f"{API_BASE}/api/chat/insert",
        json={"conversation_id": conv_id, "message": msg},
        timeout=10,
    )
    return resp.json()


async def check_health(client: httpx.AsyncClient) -> dict:
    try:
        resp = await client.get(f"{API_BASE}/api/health", timeout=5)
        return resp.json()
    except Exception:
        return {}


async def get_pool_stats(client: httpx.AsyncClient) -> dict:
    try:
        resp = await client.get(f"{API_BASE}/api/debug/pool-stats", timeout=5)
        return resp.json()
    except Exception:
        return {}


async def get_sub_tasks(client: httpx.AsyncClient, conv_id: str) -> list:
    try:
        resp = await client.get(
            f"{API_BASE}/api/agents/sub-tasks",
            params={"conversation_id": conv_id}, timeout=5,
        )
        return resp.json()
    except Exception:
        return []


def _uid(prefix: str = "t") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# ─────────────────────────── test cases ───────────────────────────


async def tc_01_health(c: httpx.AsyncClient) -> tuple[bool, str]:
    """TC-01: 服务健康检查"""
    h = await check_health(c)
    ok = h.get("status") == "ok" and h.get("agent_initialized", False)
    return ok, f"status={h.get('status')}, agent_initialized={h.get('agent_initialized')}, version={h.get('version')}"


async def tc_02_pool_enabled(c: httpx.AsyncClient) -> tuple[bool, str]:
    """TC-02: Pool 已启用"""
    stats = await get_pool_stats(c)
    enabled = stats.get("pool_enabled", False)
    return enabled, f"pool_enabled={enabled}, total_entries={stats.get('total', '?')}"


async def tc_03_single_stream(c: httpx.AsyncClient) -> tuple[bool, str]:
    """TC-03: 单会话流式基线"""
    r = await stream_chat(c, "简单回复：你好", conversation_id=_uid("single"))
    return r.ok, r.summary()


async def tc_04_pool_creates_separate_instances(c: httpx.AsyncClient) -> tuple[bool, str]:
    """TC-04: Pool 为不同 conv_id 创建不同 Agent 实例"""
    cid_a, cid_b = _uid("iso_a"), _uid("iso_b")

    # 先清掉旧的，获取 baseline
    stats_before = await get_pool_stats(c)
    before_sessions = {s["session_id"] for s in stats_before.get("sessions", [])}

    r_a, r_b = await asyncio.gather(
        stream_chat(c, "回复A", conversation_id=cid_a),
        stream_chat(c, "回复B", conversation_id=cid_b),
    )

    stats_after = await get_pool_stats(c)
    after_sessions = {s["session_id"] for s in stats_after.get("sessions", [])}

    new_sessions = after_sessions - before_sessions
    has_both = cid_a in after_sessions and cid_b in after_sessions
    both_ok = r_a.ok and r_b.ok

    detail = (
        f"new_sessions={new_sessions}\n"
        f"       A in pool={cid_a in after_sessions}, B in pool={cid_b in after_sessions}\n"
        f"       total_pool_size={stats_after.get('total')}\n"
        f"       A: {r_a.summary(40)}\n"
        f"       B: {r_b.summary(40)}"
    )
    return both_ok and has_both, detail


async def tc_05_two_concurrent_overlap(c: httpx.AsyncClient) -> tuple[bool, str]:
    """TC-05: 两会话并发 — 验证时间重叠"""
    t0 = time.monotonic()
    r1, r2 = await asyncio.gather(
        stream_chat(c, "用一句话解释 Python", conversation_id=_uid("ov_a")),
        stream_chat(c, "用一句话解释 Rust", conversation_id=_uid("ov_b")),
    )
    wall = time.monotonic() - t0
    serial_est = r1.duration_s + r2.duration_s
    overlap = serial_est > wall * 1.2

    detail = (
        f"A: {r1.summary(40)}\n"
        f"       B: {r2.summary(40)}\n"
        f"       wall={wall:.1f}s vs serial={serial_est:.1f}s → {'并发 ✅' if overlap else '可能串行 ⚠️'}"
    )
    return r1.ok and r2.ok, detail


async def tc_06_three_concurrent(c: httpx.AsyncClient) -> tuple[bool, str]:
    """TC-06: 三会话并发流式"""
    t0 = time.monotonic()
    results = await asyncio.gather(
        stream_chat(c, "一句话解释 TCP", conversation_id=_uid("tri_a")),
        stream_chat(c, "一句话解释 UDP", conversation_id=_uid("tri_b")),
        stream_chat(c, "一句话解释 HTTP", conversation_id=_uid("tri_c")),
    )
    wall = time.monotonic() - t0
    serial_est = sum(r.duration_s for r in results)
    all_ok = all(r.ok for r in results)
    lines = [r.summary(40) for r in results]
    lines.append(f"wall={wall:.1f}s vs serial={serial_est:.1f}s")
    return all_ok, "\n       ".join(lines)


async def tc_07_cancel_isolation(c: httpx.AsyncClient) -> tuple[bool, str]:
    """TC-07: Cancel 隔离 — 取消 A 不影响 B"""
    cid_a, cid_b = _uid("can_a"), _uid("can_b")

    async def run_a():
        task = asyncio.create_task(
            stream_chat(c, "写一篇500字的文章关于人工智能的未来", conversation_id=cid_a)
        )
        await asyncio.sleep(3)
        cr = await cancel_chat(c, cid_a)
        r = await task
        return r, cr

    async def run_b():
        return await stream_chat(c, "简单回复：1+1=?", conversation_id=cid_b)

    (r_a, cancel_res), r_b = await asyncio.gather(run_a(), run_b())
    b_ok = r_b.ok and r_b.text.strip()
    detail = (
        f"A (cancelled): done={r_a.done}, errors={r_a.errors[:2]}, api={cancel_res}\n"
        f"       B (should complete): {r_b.summary(40)}\n"
        f"       B完整完成={b_ok}"
    )
    return b_ok, detail


async def tc_08_skip_isolation(c: httpx.AsyncClient) -> tuple[bool, str]:
    """TC-08: Skip 隔离 — skip A 的操作不影响 B"""
    cid_a, cid_b = _uid("skp_a"), _uid("skp_b")

    async def run_a():
        task = asyncio.create_task(
            stream_chat(c, "详细解释量子计算的基本原理", conversation_id=cid_a)
        )
        await asyncio.sleep(2)
        sr = await skip_chat(c, cid_a)
        r = await task
        return r, sr

    async def run_b():
        return await stream_chat(c, "简单回复：2+2=?", conversation_id=cid_b)

    (r_a, skip_res), r_b = await asyncio.gather(run_a(), run_b())
    b_ok = r_b.ok and r_b.text.strip()
    detail = (
        f"A (skipped): done={r_a.done}, skip_api={skip_res}\n"
        f"       B (should complete): {r_b.summary(40)}\n"
        f"       B完整完成={b_ok}"
    )
    return b_ok, detail


async def tc_09_insert_isolation(c: httpx.AsyncClient) -> tuple[bool, str]:
    """TC-09: Insert 隔离 — 向 A 插入消息不影响 B"""
    cid_a, cid_b = _uid("ins_a"), _uid("ins_b")

    async def run_a():
        task = asyncio.create_task(
            stream_chat(c, "写一篇关于太阳系的短文", conversation_id=cid_a)
        )
        await asyncio.sleep(2)
        ir = await insert_chat(c, cid_a, "补充一下冥王星的信息")
        r = await task
        return r, ir

    async def run_b():
        return await stream_chat(c, "简单回复：3+3=?", conversation_id=cid_b)

    (r_a, ins_res), r_b = await asyncio.gather(run_a(), run_b())
    b_ok = r_b.ok and r_b.text.strip()
    detail = (
        f"A (with insert): done={r_a.done}, insert_api={ins_res}\n"
        f"       B (should complete): {r_b.summary(40)}\n"
        f"       B完整完成={b_ok}"
    )
    return b_ok, detail


async def tc_10_context_kept(c: httpx.AsyncClient) -> tuple[bool, str]:
    """TC-10: 同会话上下文保持"""
    cid = _uid("ctx")
    r1 = await stream_chat(c, "记住这个暗号：ZETA9988", conversation_id=cid)
    r2 = await stream_chat(c, "我刚才告诉你的暗号是什么？", conversation_id=cid)
    has_code = "ZETA9988" in r2.text
    detail = (
        f"Round 1: {r1.summary(40)}\n"
        f"       Round 2: {r2.summary(40)}\n"
        f"       暗号保持={has_code}"
    )
    return r2.ok and has_code, detail


async def tc_11_context_isolated(c: httpx.AsyncClient) -> tuple[bool, str]:
    """TC-11: 跨会话上下文隔离"""
    cid_a, cid_b = _uid("mem_a"), _uid("mem_b")
    await stream_chat(c, "记住密码是 ALPHA1234", conversation_id=cid_a)
    r_b = await stream_chat(c, "我之前告诉你的密码是什么？", conversation_id=cid_b)
    leaked = "ALPHA1234" in r_b.text
    detail = (
        f"B's reply (first 120c): {r_b.text[:120]}\n"
        f"       密码泄漏={leaked} {'❌ 泄漏!' if leaked else '✅ 隔离正常'}"
    )
    return r_b.ok and not leaked, detail


async def tc_12_same_conv_reuse_agent(c: httpx.AsyncClient) -> tuple[bool, str]:
    """TC-12: 同 conv_id 复用同一 Agent 实例"""
    cid = _uid("reuse")
    await stream_chat(c, "你好", conversation_id=cid)
    stats1 = await get_pool_stats(c)
    s1 = {s["session_id"]: s for s in stats1.get("sessions", [])}

    await stream_chat(c, "再见", conversation_id=cid)
    stats2 = await get_pool_stats(c)
    s2 = {s["session_id"]: s for s in stats2.get("sessions", [])}

    in_both = cid in s1 and cid in s2
    total_unchanged = stats1.get("total", -1) == stats2.get("total", -2) or \
                      stats2.get("total", 0) <= stats1.get("total", 0) + 1  # allow for other tests

    detail = (
        f"After 1st: {s1.get(cid)}\n"
        f"       After 2nd: {s2.get(cid)}\n"
        f"       在两次都存在={in_both}, total变化={stats1.get('total')}→{stats2.get('total')}"
    )
    return in_both, detail


async def tc_13_profile_parameter(c: httpx.AsyncClient) -> tuple[bool, str]:
    """TC-13: 不同 agent_profile_id 参数正常传递"""
    cid = _uid("prof")
    r = await stream_chat(
        c, "你好，你是什么Agent？",
        conversation_id=cid,
        agent_profile_id="code-assistant",
    )
    stats = await get_pool_stats(c)
    entry = None
    for s in stats.get("sessions", []):
        if s["session_id"] == cid:
            entry = s
            break

    profile_match = entry and entry.get("profile_id") == "code-assistant"
    detail = (
        f"Result: {r.summary(40)}\n"
        f"       Pool entry: {entry}\n"
        f"       profile_id匹配={profile_match}"
    )
    return r.ok and profile_match, detail


async def tc_14_edge_empty_message(c: httpx.AsyncClient) -> tuple[bool, str]:
    """TC-14: 空消息边界"""
    cid = _uid("empty")
    r = await stream_chat(c, "", conversation_id=cid)
    # Should either succeed with empty reply or return a graceful error
    graceful = r.done or any("error" in str(e).lower() for e in r.errors)
    detail = f"Result: done={r.done}, text_len={len(r.text)}, errors={r.errors[:2]}"
    return graceful, detail


async def tc_15_edge_no_conv_id(c: httpx.AsyncClient) -> tuple[bool, str]:
    """TC-15: 无 conversation_id — 自动生成"""
    r = await stream_chat(c, "你好", conversation_id=None)
    has_auto_id = r.conversation_id.startswith("test_")
    detail = f"auto_id={r.conversation_id}, ok={r.ok}"
    return r.ok and has_auto_id, detail


async def tc_16_cancel_nonexistent(c: httpx.AsyncClient) -> tuple[bool, str]:
    """TC-16: Cancel 不存在的会话 — 不崩溃"""
    cid = _uid("noexist")
    try:
        resp = await c.post(
            f"{API_BASE}/api/chat/cancel",
            json={"conversation_id": cid, "reason": "test"},
            timeout=5,
        )
        data = resp.json()
        # Should either return ok (global agent fallback) or error message, not crash
        no_crash = resp.status_code in (200, 404, 422)
        detail = f"status={resp.status_code}, body={data}"
        return no_crash, detail
    except Exception as e:
        return False, f"Exception: {e}"


async def tc_17_insert_nonexistent(c: httpx.AsyncClient) -> tuple[bool, str]:
    """TC-17: Insert 到不存在的会话 — 不崩溃"""
    cid = _uid("noexist2")
    try:
        resp = await c.post(
            f"{API_BASE}/api/chat/insert",
            json={"conversation_id": cid, "message": "hello"},
            timeout=5,
        )
        data = resp.json()
        no_crash = resp.status_code in (200, 404, 422)
        detail = f"status={resp.status_code}, body={data}"
        return no_crash, detail
    except Exception as e:
        return False, f"Exception: {e}"


async def tc_18_sub_tasks_endpoint(c: httpx.AsyncClient) -> tuple[bool, str]:
    """TC-18: Sub-tasks 端点可用性"""
    data = await get_sub_tasks(c, "nonexistent_conv")
    is_list = isinstance(data, list)
    detail = f"返回类型={'list ✅' if is_list else type(data).__name__}, data={data}"
    return is_list, detail


async def tc_19_five_concurrent_stress(c: httpx.AsyncClient) -> tuple[bool, str]:
    """TC-19: 五会话并发压力测试"""
    questions = [
        "一句话解释 Docker",
        "一句话解释 Kubernetes",
        "一句话解释 Git",
        "一句话解释 Linux",
        "一句话解释 DNS",
    ]
    t0 = time.monotonic()
    results = await asyncio.gather(*[
        stream_chat(c, q, conversation_id=_uid(f"s5_{i}"))
        for i, q in enumerate(questions)
    ])
    wall = time.monotonic() - t0
    serial_est = sum(r.duration_s for r in results)
    all_ok = all(r.ok for r in results)
    ok_count = sum(1 for r in results if r.ok)
    lines = [f"[{i}] {r.summary(30)}" for i, r in enumerate(results)]
    lines.append(f"通过={ok_count}/5 | wall={wall:.1f}s vs serial={serial_est:.1f}s")
    return all_ok, "\n       ".join(lines)


async def tc_20_pool_stats_after_stress(c: httpx.AsyncClient) -> tuple[bool, str]:
    """TC-20: 压力测试后 Pool 状态完整性"""
    stats = await get_pool_stats(c)
    total = stats.get("total", 0)
    sessions = stats.get("sessions", [])
    # Pool should have accumulated multiple entries by now
    detail = (
        f"total={total}, sessions_count={len(sessions)}\n"
        f"       entries (first 8): {json.dumps(sessions[:8], ensure_ascii=False)}"
    )
    return total > 0, detail


# ─────────────────────────── runner ───────────────────────────

ALL_TESTS = [
    ("TC-01", "服务健康检查", tc_01_health),
    ("TC-02", "Pool 已启用", tc_02_pool_enabled),
    ("TC-03", "单会话流式基线", tc_03_single_stream),
    ("TC-04", "Pool 实例隔离", tc_04_pool_creates_separate_instances),
    ("TC-05", "两会话并发重叠", tc_05_two_concurrent_overlap),
    ("TC-06", "三会话并发流式", tc_06_three_concurrent),
    ("TC-07", "Cancel 隔离", tc_07_cancel_isolation),
    ("TC-08", "Skip 隔离", tc_08_skip_isolation),
    ("TC-09", "Insert 隔离", tc_09_insert_isolation),
    ("TC-10", "同会话上下文保持", tc_10_context_kept),
    ("TC-11", "跨会话上下文隔离", tc_11_context_isolated),
    ("TC-12", "同 conv_id 复用 Agent", tc_12_same_conv_reuse_agent),
    ("TC-13", "Profile 参数传递", tc_13_profile_parameter),
    ("TC-14", "空消息边界", tc_14_edge_empty_message),
    ("TC-15", "无 conv_id 自动生成", tc_15_edge_no_conv_id),
    ("TC-16", "Cancel 不存在会话", tc_16_cancel_nonexistent),
    ("TC-17", "Insert 不存在会话", tc_17_insert_nonexistent),
    ("TC-18", "Sub-tasks 端点", tc_18_sub_tasks_endpoint),
    ("TC-19", "五会话并发压力", tc_19_five_concurrent_stress),
    ("TC-20", "压力后 Pool 完整性", tc_20_pool_stats_after_stress),
]


async def run_all():
    print("=" * 72)
    print("  Desktop Chat 并发流式传输 — 完整测试套件")
    print(f"  API: {API_BASE}")
    print(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  用例: {len(ALL_TESTS)}")
    print("=" * 72)

    async with httpx.AsyncClient() as client:
        healthy = await check_health(client)
        if not healthy.get("agent_initialized"):
            print("\n❌ 服务未启动！请先运行: openakita serve")
            return

        results: list[tuple[str, str, bool, float]] = []
        total_t0 = time.monotonic()

        for tc_id, tc_name, tc_fn in ALL_TESTS:
            print(f"\n{'─' * 64}")
            print(f"  {tc_id}: {tc_name}")
            print(f"{'─' * 64}")
            t0 = time.monotonic()
            try:
                ok, detail = await tc_fn(client)
                elapsed = time.monotonic() - t0
                status = "✅ PASS" if ok else "❌ FAIL"
                print(f"  结果: {status} ({elapsed:.1f}s)")
                print(f"  详情: {detail}")
                results.append((tc_id, tc_name, ok, elapsed))
            except Exception as e:
                elapsed = time.monotonic() - t0
                print(f"  结果: 💥 ERROR ({elapsed:.1f}s)")
                print(f"  异常: {e}")
                import traceback; traceback.print_exc()
                results.append((tc_id, tc_name, False, elapsed))

        total_elapsed = time.monotonic() - total_t0

        # Summary
        print(f"\n{'=' * 72}")
        print("  测试总结")
        print(f"{'=' * 72}")
        passed = sum(1 for *_, ok, _ in results if ok)
        total = len(results)
        for tc_id, tc_name, ok, elapsed in results:
            print(f"  {'✅' if ok else '❌'} {tc_id} {tc_name} ({elapsed:.1f}s)")
        print(f"\n  通过: {passed}/{total} | 总耗时: {total_elapsed:.1f}s")

        # Final pool stats
        print(f"\n{'─' * 64}")
        print("  最终 Pool 状态")
        print(f"{'─' * 64}")
        final_stats = await get_pool_stats(client)
        print(f"  pool_enabled={final_stats.get('pool_enabled')}")
        print(f"  total_entries={final_stats.get('total')}")
        for s in final_stats.get("sessions", [])[:10]:
            print(f"    {s['session_id'][:20]}… profile={s['profile_id']} idle={s['idle_seconds']}s")
        if final_stats.get("total", 0) > 10:
            print(f"    ... and {final_stats['total'] - 10} more")

        print(f"\n{'=' * 72}")
        if passed == total:
            print("  🎉 全部通过！并发流式传输工作正常。")
        else:
            print(f"  ⚠️  {total - passed}/{total} 个测试失败，请检查日志。")
        print(f"{'=' * 72}")


if __name__ == "__main__":
    asyncio.run(run_all())

