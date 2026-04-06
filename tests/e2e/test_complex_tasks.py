"""
Complex Task E2E Test Suite — 20 test cases
Tests multi-step tool chains, tool preference rules, and complex reasoning.
Hits POST /api/chat (SSE) and validates responses and tool selection behavior.
"""

import asyncio
import aiohttp
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

BASE = "http://127.0.0.1:18900"
TIMEOUT = aiohttp.ClientTimeout(total=300, sock_read=300)


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
    chain_texts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    has_thinking: bool = False
    has_agent_switch: bool = False
    has_plan: bool = False
    bug_notes: list[str] = field(default_factory=list)


def build_tests() -> list[TestCase]:
    """Build 20 complex test cases across 4 groups."""
    tests = []
    t = lambda **kw: tests.append(TestCase(id=len(tests) + 1, **kw))

    # ── Group 1: New Tool Tests ──
    t(
        name="web_fetch测试",
        message="帮我获取 https://httpbin.org/get 这个URL的内容，告诉我返回了什么",
        group="new-tool",
        expect_tool=True,
    )
    t(
        name="read_lints测试",
        message="检查一下 src/openakita/tools/terminal.py 这个文件有没有 linter 错误",
        group="new-tool",
        expect_tool=True,
    )
    t(
        name="semantic_search测试",
        message="在项目中语义搜索一下'终端会话是如何管理的'",
        group="new-tool",
        expect_tool=True,
    )
    t(
        name="grep工具测试",
        message="用grep搜索整个src目录下所有包含 'TerminalSession' 的文件",
        group="new-tool",
        expect_tool=True,
    )
    t(
        name="glob工具测试",
        message="查找项目中所有的 test_*.py 测试文件，告诉我有多少个",
        group="new-tool",
        expect_tool=True,
    )

    # ── Group 2: Tool Chain / Multi-step ──
    t(
        name="读取+分析+写入",
        message="读取 pyproject.toml，提取所有依赖包名称，然后把结果写入 /tmp/deps_list.txt",
        group="tool-chain",
        expect_tool=True,
    )
    t(
        name="搜索+读取+总结",
        message="找到项目中定义了 TerminalSessionManager 的文件，读取它的核心代码，给我一个100字的架构总结",
        group="tool-chain",
        expect_tool=True,
    )
    t(
        name="Shell+读取验证",
        message="在 /tmp 目录下创建一个 test_openakita.txt 文件内容写 'hello from openakita'，然后用 read_file 验证内容",
        group="tool-chain",
        expect_tool=True,
    )
    t(
        name="Web搜索+总结",
        message="搜索一下 2026年最新的 Python web框架趋势，给我总结3个要点",
        group="tool-chain",
        expect_tool=True,
    )
    t(
        name="多文件并行读取",
        message="同时读取这三个文件的前10行：pyproject.toml、README.md、AGENTS.md，对比它们描述的项目信息是否一致",
        group="tool-chain",
        expect_tool=True,
    )

    # ── Group 3: Tool Preference Rules ──
    t(
        name="应该用read_file",
        message="帮我看看 src/openakita/__init__.py 文件的内容",
        group="tool-pref",
        expect_tool=True,
    )
    t(
        name="应该用grep",
        message="搜索一下项目里哪些文件引用了 'HIGH_FREQ_TOOLS'",
        group="tool-pref",
        expect_tool=True,
    )
    t(
        name="应该用glob",
        message="找出 src/openakita/tools/ 目录下所有 .py 文件",
        group="tool-pref",
        expect_tool=True,
    )
    t(
        name="应该用write_file",
        message="创建一个文件 /tmp/akita_test.py，内容是 print('hello')",
        group="tool-pref",
        expect_tool=True,
    )
    t(
        name="编辑前先读取",
        message="把 src/openakita/tools/catalog.py 中的 HIGH_FREQ_TOOLS 集合里加一个 'semantic_search'",
        group="tool-pref",
        expect_tool=True,
    )

    # ── Group 4: Complex Reasoning + Tools ──
    t(
        name="代码审查",
        message="审查 src/openakita/tools/handlers/web_fetch.py 的代码质量，给出改进建议",
        group="complex",
        expect_tool=True,
        thinking_mode="on",
    )
    t(
        name="统计分析",
        message="统计 src/openakita/tools/ 目录下每个子目录分别有多少个 .py 文件，用表格展示",
        group="complex",
        expect_tool=True,
    )
    t(
        name="跨文件依赖分析",
        message="分析 src/openakita/core/agent.py 导入了哪些 openakita 内部模块，列出所有 from openakita import 路径",
        group="complex",
        expect_tool=True,
    )
    t(
        name="Shell后台任务",
        message="用后台模式运行 'sleep 3 && echo background_done' 命令，设置 block_timeout_ms 为 0，告诉我PID",
        group="complex",
        expect_tool=True,
    )
    t(
        name="错误处理",
        message="读取一个不存在的文件 /tmp/this_file_does_not_exist_12345.txt，告诉我发生了什么",
        group="complex",
        expect_tool=True,
    )

    return tests


def _chain_blob(result: TestResult) -> str:
    return " ".join(result.chain_texts)


def validate_tool_pref(tc: TestCase, result: TestResult) -> None:
    """
    For tool-pref tests: infer intended specialized tool from test name,
    check chain_texts + tool_calls, and flag run_shell when a specialized tool fits.
    """
    if tc.group != "tool-pref" or not result.success:
        return

    chains = _chain_blob(result)
    tools = result.tool_calls
    has_shell = "run_shell" in tools

    def note(msg: str) -> None:
        result.bug_notes.append(msg)

    # Evidence helpers — chain_text from reasoning_engine often uses Chinese labels
    # (e.g. 正在读取) or "调用 {tool}(...)..." for generic tools.
    if tc.name == "应该用read_file":
        via_chain = "正在读取" in chains or "read_file" in chains.lower()
        via_calls = "read_file" in tools
        if not (via_chain or via_calls):
            note("PREF: expected read_file usage (check tool_calls and chain_texts)")
        if has_shell and "read_file" not in tools:
            note(
                "PREF_VIOLATION: run_shell used; prefer read_file for file content "
                "(e.g. avoid cat/type via shell)"
            )

    elif tc.name == "应该用grep":
        via_chain = "grep" in chains.lower() or '搜索 "' in chains
        via_calls = "grep" in tools
        if not (via_chain or via_calls):
            note("PREF: expected grep usage (check tool_calls and chain_texts)")
        if has_shell and "grep" not in tools:
            note(
                "PREF_VIOLATION: run_shell used; prefer grep tool for project text search "
                "(e.g. avoid shell rg/findstr)"
            )

    elif tc.name == "应该用glob":
        via_chain = "glob" in chains.lower() or "调用 glob" in chains
        via_calls = "glob" in tools
        if not (via_chain or via_calls):
            note("PREF: expected glob usage (check tool_calls and chain_texts)")
        if has_shell and "glob" not in tools:
            note(
                "PREF_VIOLATION: run_shell used; prefer glob for file patterns "
                "(e.g. avoid find/ls via shell)"
            )

    elif tc.name == "应该用write_file":
        via_chain = "正在写入" in chains or "write_file" in chains.lower()
        via_calls = "write_file" in tools
        if not (via_chain or via_calls):
            note("PREF: expected write_file usage (check tool_calls and chain_texts)")
        if has_shell and "write_file" not in tools:
            note(
                "PREF_VIOLATION: run_shell used; prefer write_file for new file content "
                "(e.g. avoid echo/heredoc redirection)"
            )

    elif tc.name == "编辑前先读取":
        has_read = "read_file" in tools
        has_edit = "edit_file" in tools
        if not has_read:
            note("PREF: expected read_file before editing catalog.py")
        if not has_edit:
            note("PREF: expected edit_file (or similar) to modify HIGH_FREQ_TOOLS")
        if has_read and has_edit:
            if tools.index("read_file") > tools.index("edit_file"):
                note("PREF_VIOLATION: edit_file ran before read_file")
        if has_shell and not (has_read and has_edit):
            note(
                "PREF_VIOLATION: run_shell used; prefer read_file then edit_file "
                "for controlled edits"
            )


async def wait_until_not_busy(session: aiohttp.ClientSession, max_wait: int = 120):
    """Poll /api/chat/busy until no conversations are busy."""
    start = time.time()
    while time.time() - start < max_wait:
        async with session.get(f"{BASE}/api/chat/busy") as resp:
            data = await resp.json()
            busy = data.get("busy_conversations", [])
            if not busy:
                return True
        await asyncio.sleep(3)
    return False


async def send_chat(session: aiohttp.ClientSession, tc: TestCase) -> TestResult:
    """Send a single chat request and collect SSE events."""
    result = TestResult(
        test_id=tc.id, name=tc.name, group=tc.group, success=False, duration_ms=0
    )
    body: dict[str, Any] = {
        "message": tc.message,
        "conversation_id": tc.conversation_id or f"complex_{tc.id}_{uuid.uuid4().hex[:8]}",
    }
    if tc.agent_profile_id:
        body["agent_profile_id"] = tc.agent_profile_id
    if tc.plan_mode:
        body["plan_mode"] = True
    if tc.thinking_mode:
        body["thinking_mode"] = tc.thinking_mode

    start = time.time()
    try:
        async with session.post(
            f"{BASE}/api/chat", json=body, timeout=TIMEOUT
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                result.errors.append(f"HTTP {resp.status}: {text[:300]}")
                result.duration_ms = (time.time() - start) * 1000
                return result

            text_parts = []
            async for line in resp.content:
                line_str = line.decode("utf-8", errors="replace").strip()
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
                    result.tool_calls.append(
                        evt.get("tool", evt.get("name", "unknown"))
                    )
                elif evt_type == "chain_text":
                    ct = evt.get("content", evt.get("text", ""))
                    if ct:
                        result.chain_texts.append(ct)
                elif evt_type in ("thinking_start", "thinking_delta"):
                    result.has_thinking = True
                elif evt_type in ("agent_switch", "agent_handoff"):
                    result.has_agent_switch = True
                elif evt_type in ("todo_created",):
                    result.has_plan = True

            result.text_response = "".join(text_parts)
            result.success = True

    except asyncio.TimeoutError:
        result.errors.append("TIMEOUT after 300s")
    except Exception as e:
        result.errors.append(f"Exception: {type(e).__name__}: {e}")

    result.duration_ms = (time.time() - start) * 1000

    # ── Validation checks ──
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
            result.bug_notes.append(
                "WARN: Expected multi-agent delegation but no agent_switch event or delegation tool call"
            )
        if tc.plan_mode and not result.has_plan and "plan" not in resp_text.lower():
            result.bug_notes.append("WARN: Plan mode enabled but no plan content detected")

        validate_tool_pref(tc, result)

    return result


async def wait_for_backend(session: aiohttp.ClientSession, max_wait: int = 60) -> bool:
    """Wait until the backend is reachable."""
    start = time.time()
    while time.time() - start < max_wait:
        try:
            async with session.get(f"{BASE}/api/chat/busy") as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        await asyncio.sleep(3)
    return False


async def run_all_tests():
    tests = build_tests()
    start_from = int(os.environ.get("START_FROM", "1"))
    print(f"{'='*80}")
    print(
        f"  OpenAkita Complex Task E2E Test — {len(tests)} test cases "
        f"(start from #{start_from})"
    )
    print(f"{'='*80}\n")

    results: list[TestResult] = []

    async with aiohttp.ClientSession() as session:
        if not await wait_for_backend(session, max_wait=60):
            print("ERROR: Backend not reachable after 60s")
            return

        print("⏳ Waiting for backend to be idle...")
        if not await wait_until_not_busy(session, max_wait=180):
            print("WARNING: Backend still busy after 180s, proceeding anyway...\n")
        else:
            print("✓ Backend is idle\n")

        for tc in tests:
            if tc.id < start_from:
                continue

            for retry in range(3):
                if await wait_for_backend(session, max_wait=30):
                    break
                print(f"      ⏳ Backend unreachable, retry {retry+1}/3...")
                await asyncio.sleep(10)
            else:
                print(f"      ❌ Backend unreachable after retries, skipping #{tc.id}")
                results.append(
                    TestResult(
                        test_id=tc.id,
                        name=tc.name,
                        group=tc.group,
                        success=False,
                        duration_ms=0,
                        errors=["Backend unreachable"],
                    )
                )
                continue

            await wait_until_not_busy(session, max_wait=120)

            group_label = f"[{tc.group}]"
            profile_label = f" (agent={tc.agent_profile_id})" if tc.agent_profile_id else ""
            print(f"  #{tc.id:02d} {group_label:16s} {tc.name}{profile_label}")
            print(f"      → \"{tc.message[:60]}{'...' if len(tc.message) > 60 else ''}\"")

            result = await send_chat(session, tc)
            results.append(result)

            status = "✓" if result.success and not result.errors else "✗"
            resp_preview = (
                result.text_response[:80].replace("\n", "↵") if result.text_response else "(empty)"
            )
            print(f"      {status} {result.duration_ms:.0f}ms | tools={result.tool_calls or '—'}")
            if result.chain_texts:
                preview = " | ".join(c[:50] for c in result.chain_texts[:3])
                print(f"      chain_texts: {preview}{'...' if len(result.chain_texts) > 3 else ''}")
            print(f"      Response: {resp_preview}")
            if result.bug_notes:
                for note in result.bug_notes:
                    print(f"      ⚠ {note}")
            if result.errors:
                for err in result.errors:
                    print(f"      ❌ {err[:120]}")
            print()

            await asyncio.sleep(2)

    # ── Summary Report ──
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

    report_path = os.path.join(os.path.dirname(__file__), "complex_test_results.json")
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
                    "chain_texts": r.chain_texts,
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
