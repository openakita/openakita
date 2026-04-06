"""
Comprehensive API E2E Test Suite — 25 test groups (httpx version)
Hits POST /api/chat (SSE) and validates responses, errors, and edge cases.
Multi-agent scenarios are heavily weighted.
"""

import asyncio
import httpx
import json
import time
import uuid
import os
import sys
from dataclasses import dataclass, field
from typing import Any

BASE = "http://127.0.0.1:18900"

@dataclass
class TestCase:
    id: int
    name: str
    message: str
    agent_profile_id: str | None = None
    conversation_id: str | None = None
    plan_mode: bool = False
    thinking_mode: str | None = None
    expect_tool: bool = False
    expect_multi_agent: bool = False
    group: str = "basic"

@dataclass
class TestResult:
    test_id: int
    name: str
    group: str
    success: bool
    duration_ms: float
    text_response: str = ""
    events: list[dict] = field(default_factory=list)
    event_types: list[str] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    has_thinking: bool = False
    has_agent_switch: bool = False
    has_plan: bool = False
    bug_notes: list[str] = field(default_factory=list)


def build_tests() -> list[TestCase]:
    """Build 25 test cases across categories."""
    tests = []
    t = lambda **kw: tests.append(TestCase(id=len(tests)+1, **kw))

    # Group 1: Chat / Casual
    t(name="简单问候", message="你好呀", group="chat")
    t(name="闲聊天气", message="今天天气真不错，你觉得呢？", group="chat")
    t(name="知识问答", message="量子计算的基本原理是什么？用一段话概括", group="chat")

    # Group 2: Query
    t(name="代码解释", message="解释一下Python的装饰器模式，给个简单例子", group="query")
    t(name="对比分析", message="FastAPI和Flask的主要区别是什么？列出5点", group="query")

    # Group 3: Single tool usage
    t(name="文件读取", message="读一下当前目录的 pyproject.toml 文件，告诉我项目版本号", group="tool", expect_tool=True)
    t(name="Shell命令", message="执行 python --version，告诉我Python版本", group="tool", expect_tool=True)
    t(name="记忆搜索", message="搜索一下我的记忆里有没有关于'部署'的内容", group="tool", expect_tool=True)

    # Group 4: Multi-turn conversation
    conv_multi = f"test_multi_{uuid.uuid4().hex[:8]}"
    t(name="多轮对话-第1轮", message="我叫测试员小王，请记住我的名字", conversation_id=conv_multi, group="multi-turn")
    t(name="多轮对话-第2轮", message="我刚才告诉你我叫什么名字？", conversation_id=conv_multi, group="multi-turn")
    t(name="多轮对话-第3轮", message="帮我写一个Python的hello world，然后解释每一行", conversation_id=conv_multi, group="multi-turn")

    # Group 5: Plan mode
    t(name="计划模式", message="帮我规划一个3天的上海旅行计划", plan_mode=True, group="plan", expect_tool=False)

    # Group 6: Specific agent profiles
    t(name="代码助手", message="写一个快速排序算法，用Python实现", agent_profile_id="code-assistant", group="agent-profile")
    t(name="内容创作", message="帮我写一段小红书风格的咖啡推荐文案，100字左右", agent_profile_id="content-creator", group="agent-profile")
    t(name="法务顾问", message="劳动合同到期不续签，公司需要支付经济补偿金吗？", agent_profile_id="legal-advisor", group="agent-profile")
    t(name="数据分析师", message="如何用pandas分析一个CSV文件的基本统计特征？给出代码", agent_profile_id="data-analyst", group="agent-profile")

    # Group 7: Multi-agent delegation
    t(name="多Agent-调研任务", message="请用2个分身Agent，分别调研FastAPI和Django的优缺点，然后汇总给我", group="multi-agent", expect_multi_agent=True, expect_tool=True)
    t(name="多Agent-并行写作", message="请派出3个分身，分别帮我写一段关于AI、区块链、量子计算的100字介绍，最后汇总", group="multi-agent", expect_multi_agent=True, expect_tool=True)
    t(name="多Agent-单委派", message="委派一个Agent帮我查一下当前项目的README.md内容，总结要点", group="multi-agent", expect_multi_agent=True, expect_tool=True)

    # Group 8: Edge cases
    t(name="超短消息", message="?", group="edge")
    t(name="纯标点", message="！！！？？？。。。", group="edge")
    t(name="中英混合", message="Help me write a function that calculates 斐波那契数列，要求支持大数", group="edge")
    t(name="Markdown输入", message="请解析这段markdown: # Title\n- item1\n- item2\n```python\nprint('hello')\n```", group="edge")

    # Group 9: Tool + thinking mode
    t(name="Thinking+工具", message="仔细想想，然后列出当前项目src目录下所有的Python文件数量", thinking_mode="on", group="thinking", expect_tool=True)
    t(name="复杂推理", message="一个房间里有3只猫，每只猫看到2只其他猫，请问房间里总共有几只猫？请一步步推理", thinking_mode="on", group="thinking")

    return tests


async def wait_until_not_busy(client: httpx.AsyncClient, max_wait: int = 120):
    start = time.time()
    while time.time() - start < max_wait:
        try:
            resp = await client.get(f"{BASE}/api/chat/busy")
            data = resp.json()
            busy = data.get("busy_conversations", [])
            if not busy:
                return True
        except Exception:
            pass
        await asyncio.sleep(3)
    return False


async def send_chat(client: httpx.AsyncClient, tc: TestCase) -> TestResult:
    result = TestResult(
        test_id=tc.id, name=tc.name, group=tc.group, success=False, duration_ms=0
    )
    body: dict[str, Any] = {
        "message": tc.message,
        "conversation_id": tc.conversation_id or f"test_{tc.id}_{uuid.uuid4().hex[:8]}",
    }
    if tc.agent_profile_id:
        body["agent_profile_id"] = tc.agent_profile_id
    if tc.plan_mode:
        body["plan_mode"] = True
    if tc.thinking_mode:
        body["thinking_mode"] = tc.thinking_mode

    start = time.time()
    try:
        async with client.stream("POST", f"{BASE}/api/chat", json=body, timeout=180.0) as resp:
            if resp.status_code != 200:
                text = await resp.aread()
                result.errors.append(f"HTTP {resp.status_code}: {text.decode()[:300]}")
                result.duration_ms = (time.time() - start) * 1000
                return result

            text_parts = []
            buffer = ""
            async for chunk in resp.aiter_text():
                buffer += chunk
                while "\n" in buffer:
                    line_str, buffer = buffer.split("\n", 1)
                    line_str = line_str.strip()
                    if not line_str.startswith("data:"):
                        continue
                    raw = line_str[5:].strip()
                    if not raw:
                        continue
                    try:
                        evt = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    evt_type = evt.get("type", "")
                    result.event_types.append(evt_type)
                    result.events.append(evt)

                    if evt_type == "text_delta":
                        text_parts.append(evt.get("content", ""))
                    elif evt_type == "error":
                        result.errors.append(evt.get("message", str(evt)))
                    elif evt_type == "tool_call_start":
                        result.tool_calls.append(evt.get("name", "unknown"))
                    elif evt_type in ("thinking_start", "thinking_delta"):
                        result.has_thinking = True
                    elif evt_type in ("agent_switch", "agent_handoff"):
                        result.has_agent_switch = True
                    elif evt_type in ("todo_created",):
                        result.has_plan = True

            result.text_response = "".join(text_parts)
            result.success = True

    except httpx.TimeoutException:
        result.errors.append("TIMEOUT after 180s")
    except Exception as e:
        result.errors.append(f"Exception: {type(e).__name__}: {e}")

    result.duration_ms = (time.time() - start) * 1000

    if result.success:
        resp_text = result.text_response
        if not resp_text and "done" not in result.event_types:
            result.bug_notes.append("BUG: No text response and no done event")
        if "[REPLY]" in resp_text or "[/REPLY]" in resp_text:
            result.bug_notes.append("BUG: [REPLY] tag leaked into response")
        if "[TOOL_CALLS]" in resp_text:
            result.bug_notes.append("BUG: [TOOL_CALLS] tag leaked into response")
        if "<thinking>" in resp_text:
            result.bug_notes.append("BUG: <thinking> tag leaked into response")
        if tc.expect_tool and not result.tool_calls:
            result.bug_notes.append("WARN: Expected tool usage but none occurred")
        if tc.expect_multi_agent and not result.has_agent_switch and "delegate" not in " ".join(result.tool_calls).lower() and "spawn" not in " ".join(result.tool_calls).lower():
            result.bug_notes.append("WARN: Expected multi-agent delegation but no agent_switch event or delegation tool call")
        if tc.plan_mode and not result.has_plan and "plan" not in resp_text.lower():
            result.bug_notes.append("WARN: Plan mode enabled but no plan content detected")

    return result


async def wait_for_backend(client: httpx.AsyncClient, max_wait: int = 60) -> bool:
    start = time.time()
    while time.time() - start < max_wait:
        try:
            resp = await client.get(f"{BASE}/api/chat/busy")
            if resp.status_code == 200:
                return True
        except Exception:
            pass
        await asyncio.sleep(3)
    return False


async def run_all_tests():
    tests = build_tests()
    start_from = int(os.environ.get("START_FROM", "1"))
    print(f"{'='*80}")
    print(f"  OpenAkita API Comprehensive Test — {len(tests)} test cases (start from #{start_from})")
    print(f"{'='*80}\n")

    results: list[TestResult] = []

    async with httpx.AsyncClient() as client:
        if not await wait_for_backend(client, max_wait=60):
            print("ERROR: Backend not reachable after 60s")
            return

        print("⏳ Waiting for backend to be idle...")
        if not await wait_until_not_busy(client, max_wait=180):
            print("WARNING: Backend still busy after 180s, proceeding anyway...\n")
        else:
            print("✓ Backend is idle\n")

        for tc in tests:
            if tc.id < start_from:
                continue

            for retry in range(3):
                if await wait_for_backend(client, max_wait=30):
                    break
                print(f"      ⏳ Backend unreachable, retry {retry+1}/3...")
                await asyncio.sleep(10)
            else:
                print(f"      ❌ Backend unreachable after retries, skipping #{tc.id}")
                results.append(TestResult(
                    test_id=tc.id, name=tc.name, group=tc.group,
                    success=False, duration_ms=0,
                    errors=["Backend unreachable"]
                ))
                continue

            await wait_until_not_busy(client, max_wait=120)

            group_label = f"[{tc.group}]"
            profile_label = f" (agent={tc.agent_profile_id})" if tc.agent_profile_id else ""
            print(f"  #{tc.id:02d} {group_label:16s} {tc.name}{profile_label}")
            print(f"      → \"{tc.message[:60]}{'...' if len(tc.message) > 60 else ''}\"")

            result = await send_chat(client, tc)
            results.append(result)

            status = "✓" if result.success and not result.errors else "✗"
            resp_preview = result.text_response[:80].replace("\n", "↵") if result.text_response else "(empty)"
            print(f"      {status} {result.duration_ms:.0f}ms | tools={result.tool_calls or '—'}")
            print(f"      Response: {resp_preview}")
            if result.bug_notes:
                for note in result.bug_notes:
                    print(f"      ⚠ {note}")
            if result.errors:
                for err in result.errors:
                    print(f"      ❌ {err[:120]}")
            print()

            await asyncio.sleep(2)

    # Summary Report
    print(f"\n{'='*80}")
    print(f"  TEST SUMMARY")
    print(f"{'='*80}")

    total = len(results)
    passed = sum(1 for r in results if r.success and not r.errors)
    failed = sum(1 for r in results if not r.success or r.errors)
    with_bugs = sum(1 for r in results if r.bug_notes)
    avg_time = sum(r.duration_ms for r in results) / total if total else 0

    print(f"  Total: {total} | Passed: {passed} | Failed: {failed} | With warnings: {with_bugs}")
    print(f"  Average response time: {avg_time:.0f}ms")
    print()

    groups: dict[str, list[TestResult]] = {}
    for r in results:
        groups.setdefault(r.group, []).append(r)

    print(f"  {'Group':<16s} {'Pass':>6s} {'Fail':>6s} {'Warn':>6s} {'Avg ms':>8s}")
    print(f"  {'-'*44}")
    for g, rs in groups.items():
        gp = sum(1 for r in rs if r.success and not r.errors)
        gf = sum(1 for r in rs if not r.success or r.errors)
        gw = sum(1 for r in rs if r.bug_notes)
        ga = sum(r.duration_ms for r in rs) / len(rs)
        print(f"  {g:<16s} {gp:>6d} {gf:>6d} {gw:>6d} {ga:>8.0f}")

    if any(r.bug_notes for r in results):
        print(f"\n{'='*80}")
        print(f"  BUG / WARNING DETAILS")
        print(f"{'='*80}")
        for r in results:
            if r.bug_notes:
                print(f"\n  #{r.test_id:02d} {r.name} [{r.group}]")
                for note in r.bug_notes:
                    print(f"    → {note}")

    if any(r.errors for r in results):
        print(f"\n{'='*80}")
        print(f"  ERROR DETAILS")
        print(f"{'='*80}")
        for r in results:
            if r.errors:
                print(f"\n  #{r.test_id:02d} {r.name} [{r.group}]")
                for err in r.errors:
                    print(f"    → {err[:200]}")

    report_path = os.path.join(os.path.dirname(__file__), "api_test_results.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(
            [
                {
                    "test_id": r.test_id,
                    "name": r.name,
                    "group": r.group,
                    "success": r.success,
                    "duration_ms": round(r.duration_ms),
                    "text_response_len": len(r.text_response),
                    "text_response_preview": r.text_response[:500],
                    "event_types": r.event_types,
                    "tool_calls": r.tool_calls,
                    "errors": r.errors,
                    "has_thinking": r.has_thinking,
                    "has_agent_switch": r.has_agent_switch,
                    "has_plan": r.has_plan,
                    "bug_notes": r.bug_notes,
                }
                for r in results
            ],
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"\n  Full results saved to: {report_path}")
    print()


if __name__ == "__main__":
    asyncio.run(run_all_tests())
