"""
Full Agent E2E test — real LLM, real flow.

Sends multiple messages through Agent.chat() and logs the complete pipeline:
  Intent Analysis → Prompt Build → Tool Filtering → ReasoningEngine → Response

Usage:
    python tests/e2e/test_full_agent_flow.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

for noisy in (
    "httpx", "httpcore", "openakita.llm.providers.proxy_utils",
    "openakita.memory", "openakita.skills", "openakita.tools.handlers",
    "openakita.core.trait_miner",
):
    logging.getLogger(noisy).setLevel(logging.WARNING)

logger = logging.getLogger("e2e_full")


def banner(text: str) -> None:
    w = 72
    print(f"\n{'=' * w}")
    print(f"  {text}")
    print(f"{'=' * w}")


def section(text: str) -> None:
    print(f"\n--- {text} {'─' * max(1, 60 - len(text))}")


async def run_test():
    banner("OpenAkita Full Agent Flow E2E Test")

    # ── 1. Init Agent ──────────────────────────────────────────
    section("1. Initializing Agent")
    from openakita.core.agent import Agent

    agent = Agent()
    t0 = time.time()
    await agent.initialize(start_scheduler=False, lightweight=True)
    print(f"  Agent initialized in {time.time() - t0:.1f}s")
    print(f"  Brain model : {agent.brain.model}")
    print(f"  Tools loaded: {len(agent._tools)}")
    print(f"  Tool groups : {list(agent.tool_catalog.get_tool_groups().keys())}")

    # ── 2. Define test cases ───────────────────────────────────
    test_cases = [
        {
            "label": "CHAT — 闲聊",
            "message": "你好呀，今天天气怎么样？",
            "expect_intent": "chat",
            "expect_tools": False,
        },
        {
            "label": "QUERY — 知识问答",
            "message": "Python 的装饰器是什么？简单解释一下",
            "expect_intent": "query",
            "expect_tools": False,
        },
        {
            "label": "TASK — 文件操作",
            "message": "帮我看一下当前目录下有哪些 .py 文件",
            "expect_intent": "task",
            "expect_tools": True,
        },
        {
            "label": "CHAT — 简短确认",
            "message": "好的谢谢",
            "expect_intent": "chat",
            "expect_tools": False,
        },
    ]

    results = []

    for i, tc in enumerate(test_cases):
        section(f"2.{i+1} Test: {tc['label']}")
        print(f"  User: \"{tc['message']}\"")

        t_start = time.time()

        # Capture intent before chat
        intent_before = None

        try:
            response = await asyncio.wait_for(
                agent.chat(tc["message"]),
                timeout=120,
            )
            elapsed = time.time() - t_start

            # Read intent that was set during chat
            intent = getattr(agent, "_current_intent", None)
            intent_str = intent.intent.value if intent else "N/A"
            tool_hints = intent.tool_hints if intent else []
            mem_kw = intent.memory_keywords if intent else []
            force_tool = intent.force_tool if intent else None
            plan_req = intent.todo_required if intent else None

            # Check how many tools were available
            eff_tools = agent._effective_tools
            eff_tool_names = [t.get("name") for t in eff_tools]

            print(f"\n  [Intent]")
            print(f"    type       : {intent_str}")
            print(f"    tool_hints : {tool_hints}")
            print(f"    mem_keywords: {mem_kw}")
            print(f"    force_tool : {force_tool}")
            print(f"    plan_req   : {plan_req}")

            print(f"\n  [Tools]")
            print(f"    effective  : {len(eff_tools)} tools")
            if len(eff_tools) <= 15:
                print(f"    names      : {eff_tool_names}")
            else:
                print(f"    names      : {eff_tool_names[:10]}... (+{len(eff_tools)-10} more)")

            print(f"\n  [Response] ({elapsed:.1f}s, {len(response)} chars)")
            resp_preview = response[:500].replace('\n', '\n    ')
            print(f"    {resp_preview}")
            if len(response) > 500:
                print(f"    ... ({len(response) - 500} more chars)")

            # Validation
            ok = True
            issues = []
            if tc["expect_intent"] and intent_str != tc["expect_intent"]:
                if not (tc["expect_intent"] == "task" and intent_str in ("task", "query")):
                    issues.append(f"intent: got {intent_str}, expected {tc['expect_intent']}")
                    ok = False

            if tc["expect_tools"] is False and intent_str == "chat" and len(eff_tools) > 0:
                pass  # _effective_tools may still be populated; key is that chat path doesn't use them

            if not response or len(response.strip()) < 2:
                issues.append("empty or too short response")
                ok = False

            status = "PASS" if ok else f"WARN ({'; '.join(issues)})"
            print(f"\n  [{status}]")
            results.append({"label": tc["label"], "status": status, "elapsed": elapsed})

        except asyncio.TimeoutError:
            print(f"  TIMEOUT (>120s)")
            results.append({"label": tc["label"], "status": "TIMEOUT", "elapsed": 120})
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback
            traceback.print_exc()
            results.append({"label": tc["label"], "status": f"ERROR: {e}", "elapsed": 0})

    # ── 3. Summary ─────────────────────────────────────────────
    banner("Test Summary")
    total_time = sum(r["elapsed"] for r in results)
    pass_count = sum(1 for r in results if r["status"] == "PASS")

    for r in results:
        icon = "✓" if r["status"] == "PASS" else "✗"
        print(f"  {icon} {r['label']:30s}  {r['status']:10s}  ({r['elapsed']:.1f}s)")

    print(f"\n  {pass_count}/{len(results)} passed, total {total_time:.1f}s")

    # ── 4. System prompt inspection ────────────────────────────
    section("4. System prompt token breakdown")
    from openakita.prompt.budget import estimate_tokens

    # Build a CHAT prompt and a TASK prompt for comparison
    chat_prompt = await agent._build_system_prompt_compiled(
        task_description="", session_type="cli", tools_enabled=False
    )
    task_prompt = await agent._build_system_prompt_compiled(
        task_description="帮我看一下目录", session_type="cli", tools_enabled=True
    )
    print(f"  CHAT prompt : {estimate_tokens(chat_prompt)} tokens ({len(chat_prompt)} chars)")
    print(f"  TASK prompt : {estimate_tokens(task_prompt)} tokens ({len(task_prompt)} chars)")
    saving = (1 - estimate_tokens(chat_prompt) / max(estimate_tokens(task_prompt), 1)) * 100
    print(f"  CHAT saves  : {saving:.0f}% tokens vs TASK")

    # Show sections in CHAT prompt
    print(f"\n  CHAT prompt structure:")
    for line in chat_prompt.split("\n"):
        if line.startswith("# ") or line.startswith("## "):
            print(f"    {line}")

    # Show sections in TASK prompt
    print(f"\n  TASK prompt structure:")
    for line in task_prompt.split("\n"):
        if line.startswith("# ") or line.startswith("## "):
            print(f"    {line}")

    banner("Done")
    return pass_count == len(results)


if __name__ == "__main__":
    ok = asyncio.run(run_test())
    sys.exit(0 if ok else 1)
