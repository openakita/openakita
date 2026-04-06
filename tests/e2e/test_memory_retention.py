"""
Memory Retention Test — Simulates the user's email scenario.

Tests that the agent remembers what it did across multiple turns
in the same conversation (same conversation_id).

Focus: after doing a multi-step task, does the agent recall
the approach when asked a follow-up?
"""

import asyncio
import aiohttp
import json
import time
import uuid
import os

BASE = "http://127.0.0.1:18900"
TIMEOUT = aiohttp.ClientTimeout(total=300, sock_read=300)


async def send_chat(session: aiohttp.ClientSession, message: str,
                    conversation_id: str) -> dict:
    """Send a chat and collect response."""
    body = {"message": message, "conversation_id": conversation_id}
    text_parts = []
    tool_calls = []
    events = []

    try:
        async with session.post(f"{BASE}/api/chat", json=body,
                                timeout=TIMEOUT) as resp:
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
                events.append(evt_type)
                if evt_type == "text_delta":
                    text_parts.append(evt.get("content", ""))
                elif evt_type == "tool_call_start":
                    tool_calls.append(evt.get("name", "?"))

    except asyncio.TimeoutError:
        return {"text": "(TIMEOUT)", "tools": tool_calls, "events": events}
    except Exception as e:
        return {"text": f"(ERROR: {e})", "tools": tool_calls, "events": events}

    return {"text": "".join(text_parts), "tools": tool_calls, "events": events}


async def wait_not_busy(session: aiohttp.ClientSession, max_wait: int = 120):
    start = time.time()
    while time.time() - start < max_wait:
        try:
            async with session.get(f"{BASE}/api/chat/busy") as resp:
                data = await resp.json()
                if not data.get("busy_conversations"):
                    return True
        except Exception:
            pass
        await asyncio.sleep(3)
    return False


async def run_test():
    conv_id = f"mem_test_{uuid.uuid4().hex[:8]}"

    print(f"{'='*70}")
    print(f"  Memory Retention Test")
    print(f"  Conversation ID: {conv_id}")
    print(f"{'='*70}\n")

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"{BASE}/api/chat/busy") as resp:
                if resp.status != 200:
                    print("Backend not available")
                    return
        except Exception:
            print("Backend not available")
            return

        await wait_not_busy(session)

        # Turn 1: Do a task that creates a file
        print("[Turn 1] Ask agent to create a Python script")
        r1 = await send_chat(
            session,
            "帮我在当前目录创建一个文件 test_memory_check.py，内容是一个函数 send_email(to, subject, body)，"
            "函数内部用 print 模拟发送，打印 to/subject/body，然后返回 True。创建完告诉我文件路径。",
            conv_id,
        )
        print(f"  Response: {r1['text'][:200]}")
        print(f"  Tools: {r1['tools']}")
        print()

        await wait_not_busy(session)
        await asyncio.sleep(2)

        # Turn 2: Ask to run the script
        print("[Turn 2] Ask to test the script")
        r2 = await send_chat(
            session,
            "运行一下刚才创建的那个脚本，调用 send_email('test@example.com', '测试', '你好')，看看输出",
            conv_id,
        )
        print(f"  Response: {r2['text'][:200]}")
        print(f"  Tools: {r2['tools']}")
        print()

        await wait_not_busy(session)
        await asyncio.sleep(2)

        # Turn 3: KEY TEST — ask about what was done (memory check)
        print("[Turn 3] KEY TEST — Ask agent to recall what it did")
        r3 = await send_chat(
            session,
            "刚才我们创建的那个 send_email 函数在哪个文件里？函数签名是什么？",
            conv_id,
        )
        print(f"  Response: {r3['text'][:300]}")
        print(f"  Tools: {r3['tools']}")
        print()

        # Validate
        resp_text = r3["text"].lower()
        recall_file = "test_memory_check" in resp_text
        recall_func = "send_email" in resp_text
        print(f"  Memory check:")
        print(f"    Recalls file name: {'✓' if recall_file else '✗'}")
        print(f"    Recalls function: {'✓' if recall_func else '✗'}")
        print()

        await wait_not_busy(session)
        await asyncio.sleep(2)

        # Turn 4: Ask to modify the function (needs to know what exists)
        print("[Turn 4] Ask to modify the function")
        r4 = await send_chat(
            session,
            "把刚才那个 send_email 函数改一下，加一个 cc 参数（可选，默认 None），也打印出来",
            conv_id,
        )
        print(f"  Response: {r4['text'][:200]}")
        print(f"  Tools: {r4['tools']}")
        print()

        await wait_not_busy(session)
        await asyncio.sleep(2)

        # Turn 5: Final recall test
        print("[Turn 5] Final recall — summarize everything done")
        r5 = await send_chat(
            session,
            "总结一下我们这次对话做了什么，文件在哪，函数最终是什么样的",
            conv_id,
        )
        print(f"  Response: {r5['text'][:400]}")
        print(f"  Tools: {r5['tools']}")
        print()

        # Final assessment
        final_text = r5["text"].lower()
        has_file = "test_memory_check" in final_text
        has_func = "send_email" in final_text
        has_cc = "cc" in final_text

        print(f"{'='*70}")
        print(f"  FINAL ASSESSMENT")
        print(f"{'='*70}")
        print(f"  Recalls file name: {'✓' if has_file else '✗'}")
        print(f"  Recalls function:  {'✓' if has_func else '✗'}")
        print(f"  Recalls cc param:  {'✓' if has_cc else '✗'}")
        overall = has_file and has_func and has_cc
        print(f"  Overall: {'PASS — Memory retained' if overall else 'FAIL — Memory lost'}")
        print()


if __name__ == "__main__":
    asyncio.run(run_test())
