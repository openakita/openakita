"""
E2E test: Real LLM call to verify the full optimization pipeline.

Usage:
    # Requires a working llm_endpoints.json with at least one endpoint.
    pytest tests/e2e/test_optimization_e2e.py -v -s

    # Or run directly:
    python tests/e2e/test_optimization_e2e.py

This test:
1. Initializes a real Brain (reads llm_endpoints.json)
2. Sends test messages through IntentAnalyzer (real LLM call)
3. Verifies intent parsing results
4. Builds the full system prompt based on intent results
5. Prints the prompt for visual inspection
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("e2e_optimization")


async def run_e2e_test():
    from openakita.core.brain import Brain
    from openakita.core.intent_analyzer import IntentAnalyzer, IntentType
    from openakita.prompt.builder import build_system_prompt
    from openakita.prompt.compiler import compile_all, check_compiled_outdated
    from openakita.prompt.budget import BudgetConfig, estimate_tokens
    from openakita.tools.catalog import ToolCatalog
    from openakita.tools.definitions import BASE_TOOLS
    from openakita.config import settings

    print("=" * 70)
    print("  OpenAkita Optimization E2E Test — Real LLM Calls")
    print("=" * 70)

    # 1. Initialize Brain
    print("\n[1/6] Initializing Brain...")
    try:
        brain = Brain()
        print(f"  Brain model: {brain.model}")
        print(f"  Compiler available: {brain._compiler_available()}")
    except Exception as e:
        print(f"  FATAL: Cannot init Brain: {e}")
        print("  Make sure data/llm_endpoints.json exists and has valid endpoints.")
        return False

    # 2. Initialize IntentAnalyzer
    print("\n[2/6] Initializing IntentAnalyzer...")
    analyzer = IntentAnalyzer(brain)

    # 3. Test messages covering all intent types
    test_messages = [
        ("你好呀", "Expected: CHAT"),
        ("Python的GIL是什么？", "Expected: QUERY"),
        ("帮我写一个Python脚本读取CSV文件", "Expected: TASK"),
        ("把上次那个脚本改成UTF-8编码", "Expected: FOLLOW_UP"),
        ("/stop", "Expected: COMMAND"),
        ("帮我搜索一下最新的AI论文，然后写个总结，保存到文件里", "Expected: TASK (compound)"),
        ("谢谢", "Expected: CHAT"),
        ("搜索一下OpenAkita的GitHub仓库", "Expected: TASK"),
    ]

    print("\n[3/6] Running IntentAnalyzer on test messages (real LLM calls)...\n")
    results = []
    for msg, expected in test_messages:
        print(f"  Message: \"{msg}\"  ({expected})")
        try:
            result = await asyncio.wait_for(analyzer.analyze(msg), timeout=30)
            results.append((msg, expected, result))
            print(f"    → intent={result.intent.value}, task_type={result.task_type}")
            print(f"      tool_hints={result.tool_hints}")
            print(f"      memory_keywords={result.memory_keywords}")
            print(f"      force_tool={result.force_tool}, todo_required={result.todo_required}")
            print(f"      confidence={result.confidence}")
            if result.task_definition:
                print(f"      task_def={result.task_definition[:100]}...")
            print()
        except Exception as e:
            print(f"    → ERROR: {e}\n")
            results.append((msg, expected, None))

    # 4. Validate intent classifications
    print("\n[4/6] Validating intent classifications...")
    errors = []
    for msg, expected, result in results:
        if result is None:
            errors.append(f"  FAIL: \"{msg}\" — LLM call failed")
            continue

        # Basic sanity checks
        if "CHAT" in expected and result.intent != IntentType.CHAT:
            errors.append(f"  WARN: \"{msg}\" — got {result.intent.value}, expected CHAT")
        if "TASK" in expected and result.intent not in (IntentType.TASK, IntentType.QUERY):
            errors.append(f"  WARN: \"{msg}\" — got {result.intent.value}, expected TASK/QUERY")
        if "QUERY" in expected and result.intent not in (IntentType.QUERY, IntentType.CHAT):
            errors.append(f"  WARN: \"{msg}\" — got {result.intent.value}, expected QUERY/CHAT")
        if "COMMAND" in expected and result.intent != IntentType.COMMAND:
            errors.append(f"  WARN: \"{msg}\" — got {result.intent.value}, expected COMMAND")

    if errors:
        for e in errors:
            print(e)
        print(f"  ⚠ {len(errors)} classification warnings (LLM output may vary)")
    else:
        print("  ✓ All classifications look reasonable")

    # 5. Build system prompts for different intents
    print("\n[5/6] Building system prompts based on intent results...\n")
    identity_dir = settings.identity_path
    if not identity_dir.exists():
        print(f"  WARNING: identity_dir not found at {identity_dir}")
        identity_dir = Path("identity")

    if check_compiled_outdated(identity_dir):
        print("  Recompiling identity files...")
        compile_all(identity_dir, use_llm=False)

    catalog = ToolCatalog(BASE_TOOLS)

    # a) CHAT intent prompt (tools_enabled=False)
    prompt_chat = build_system_prompt(
        identity_dir=identity_dir,
        tools_enabled=False,
    )
    tokens_chat = estimate_tokens(prompt_chat)
    print(f"  [CHAT path]  {tokens_chat} tokens, {len(prompt_chat)} chars")

    # b) TASK intent prompt (tools_enabled=True, full catalog)
    prompt_task = build_system_prompt(
        identity_dir=identity_dir,
        tools_enabled=True,
        tool_catalog=catalog,
        include_tools_guide=True,
    )
    tokens_task = estimate_tokens(prompt_task)
    print(f"  [TASK path]  {tokens_task} tokens, {len(prompt_task)} chars")

    # c) TASK with filtered tools (simulating File System hint)
    tool_groups = catalog.get_tool_groups()
    allowed = {"ask_user"}
    for hint in ["File System"]:
        allowed |= tool_groups.get(hint, set())
    filtered = [t for t in BASE_TOOLS if t.get("name") in allowed]
    prompt_filtered = build_system_prompt(
        identity_dir=identity_dir,
        tools_enabled=True,
        tool_catalog=ToolCatalog(filtered),
        include_tools_guide=True,
    )
    tokens_filtered = estimate_tokens(prompt_filtered)
    print(f"  [FILTERED]   {tokens_filtered} tokens, {len(prompt_filtered)} chars (File System only)")

    # Token savings
    saving_chat = (1 - tokens_chat / max(tokens_task, 1)) * 100
    saving_filtered = (1 - tokens_filtered / max(tokens_task, 1)) * 100
    print(f"\n  Token savings:")
    print(f"    CHAT vs TASK:     {saving_chat:.0f}% fewer tokens")
    print(f"    FILTERED vs TASK: {saving_filtered:.0f}% fewer tokens")

    # 6. Print sample prompts for visual inspection
    print("\n[6/6] Sample system prompt content (CHAT path, first 1000 chars):\n")
    print("-" * 60)
    print(prompt_chat[:1000])
    print("... [truncated]")
    print("-" * 60)

    print("\n\n=== Tool catalog header (first 500 chars) ===")
    catalog_text = catalog.generate_catalog()
    print(catalog_text[:500])
    print("... [truncated]")

    # Dynamic tool groups
    print("\n=== Dynamic tool groups ===")
    for cat, tools in sorted(tool_groups.items()):
        print(f"  {cat}: {sorted(tools)[:5]}{'...' if len(tools) > 5 else ''}")

    # Final result
    success_count = sum(1 for _, _, r in results if r is not None)
    total = len(results)
    print(f"\n{'=' * 70}")
    print(f"  RESULT: {success_count}/{total} LLM calls succeeded")
    print(f"  Classification warnings: {len(errors)}")
    print(f"  System prompt: CHAT={tokens_chat}t, TASK={tokens_task}t, FILTERED={tokens_filtered}t")
    print(f"{'=' * 70}")

    return success_count == total


if __name__ == "__main__":
    ok = asyncio.run(run_e2e_test())
    sys.exit(0 if ok else 1)
