"""
并行工具调用演示脚本

目的：
- 验证 Agent 对“同一轮多个 tool_use/tool_calls”的承接能力
- 在启用 TOOL_MAX_PARALLEL>1 时，观察工具并行带来的耗时下降

运行（Windows PowerShell 示例）：
    $env:TOOL_MAX_PARALLEL="4"
    python scripts/tool_parallel_demo.py
"""

from __future__ import annotations

import asyncio
import time


async def main() -> None:
    # 延迟导入：确保从环境变量读取 TOOL_MAX_PARALLEL
    from openakita.core.agent import Agent

    agent = Agent()
    await agent.initialize(start_scheduler=False)

    # 关闭中断检查，让 chat/会话模式也允许并行（仅演示）
    agent.set_interrupt_enabled(False)

    tool_calls = [
        {"id": "t1", "name": "run_shell", "input": {"command": "python -c \"import time; time.sleep(2); print('A')\""}},
        {"id": "t2", "name": "run_shell", "input": {"command": "python -c \"import time; time.sleep(2); print('B')\""}},
    ]

    t0 = time.time()
    results, executed, _ = await agent._execute_tool_calls_batch(  # noqa: SLF001 - demo script
        tool_calls,
        allow_interrupt_checks=False,
        capture_delivery_receipts=False,
    )
    dt = time.time() - t0

    print(f"executed={executed}")
    print(f"elapsed={dt:.2f}s (expect ~2s when parallel, ~4s when serial)")
    for r in results:
        print("-", r.get("tool_use_id"), r.get("content", "")[:120])


if __name__ == "__main__":
    asyncio.run(main())

