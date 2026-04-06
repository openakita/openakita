"""Comprehensive exploratory test via SSE API."""

import httpx
import json
import time

BASE = "http://127.0.0.1:18900"


def chat(msg, conv_id=None, label="", timeout=180):
    """Send a message via SSE and collect the full response."""
    body = {"message": msg, "mode": "agent"}
    if conv_id:
        body["conversation_id"] = conv_id

    print(f"\n{'='*60}")
    print(f"[{label}] USER: {msg}")
    print("-" * 60)
    t0 = time.time()

    full_text = ""
    thinking_text = ""
    tool_calls = []
    intent_info = {}
    result_conv_id = conv_id
    iterations = "?"
    events_count = 0
    current_tool = None

    try:
        with httpx.stream(
            "POST", f"{BASE}/api/chat",
            json=body,
            timeout=timeout,
        ) as resp:
            if resp.status_code != 200:
                print(f"  HTTP {resp.status_code}: {resp.text[:200]}")
                return conv_id, "", {}

            for line in resp.iter_lines():
                if not line:
                    continue
                if line.startswith("data: "):
                    raw = line[6:]
                    if raw == "[DONE]":
                        break
                    try:
                        evt = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    events_count += 1
                    etype = evt.get("type", "")

                    if etype == "text_delta":
                        full_text += evt.get("content", "")
                    elif etype == "thinking_delta":
                        thinking_text += evt.get("content", "")
                    elif etype == "tool_call_start":
                        current_tool = evt.get("name", "?")
                    elif etype == "tool_call_end":
                        tool_calls.append({
                            "name": current_tool or evt.get("name", "?"),
                            "result_preview": str(evt.get("result", ""))[:100],
                        })
                        current_tool = None
                    elif etype == "metadata":
                        result_conv_id = evt.get("conversation_id", conv_id)
                        intent_info = evt.get("intent", {})
                        iterations = evt.get("iterations", "?")
                    elif etype == "error":
                        print(f"  ERROR EVENT: {evt.get('message', evt)}")
                    elif etype == "done":
                        result_conv_id = evt.get("conversation_id", result_conv_id)
                        iterations = evt.get("iterations", iterations)
                        intent_info = evt.get("intent", intent_info)

        elapsed = time.time() - t0
        print(f"  Time: {elapsed:.1f}s | Events: {events_count} | Iterations: {iterations}")
        if intent_info:
            print(f"  Intent: {json.dumps(intent_info, ensure_ascii=False)[:200]}")
        if tool_calls:
            print(f"  Tools ({len(tool_calls)}): {[tc['name'] for tc in tool_calls]}")
            for tc in tool_calls[:3]:
                print(f"    - {tc['name']}: {tc['result_preview'][:80]}")
        if thinking_text:
            print(f"  Thinking: {thinking_text[:150]}...")
        reply_preview = full_text.strip()[:600]
        print(f"  REPLY ({len(full_text)} chars):")
        print(f"    {reply_preview}")
        if len(full_text) > 600:
            print(f"    ... (truncated)")
        print(f"  ConvID: {result_conv_id}")
        return result_conv_id, full_text, {"intent": intent_info, "tools": tool_calls}

    except Exception as e:
        elapsed = time.time() - t0
        print(f"  EXCEPTION after {elapsed:.1f}s: {e}")
        return conv_id, "", {}


print("=" * 60)
print("COMPREHENSIVE EXPLORATORY TEST")
print("=" * 60)

# T1: Simple Chat
cid1, r1, _ = chat("你好", label="T1-CHAT-简单闲聊")

# T2: Simple Query (no tools)
cid2, r2, _ = chat("1+1等于几", label="T2-QUERY-简单计算")

# T3: Task - File System (needs glob/list tools)
cid3, r3, d3 = chat("帮我查看当前目录下有哪些Python文件", label="T3-TASK-文件查看")

# T4: Knowledge Query
cid4, r4, _ = chat("Python的GIL是什么？简要回答", label="T4-QUERY-知识问答")

# T5: Task - Web Search
cid5, r5, d5 = chat("搜索一下2026年最新的AI新闻", label="T5-TASK-搜索")

# T6: Multi-turn
cid6, r6, d6 = chat("帮我创建一个名为hello_test.py的文件，内容是print('hello world')", label="T6-MULTI-创建文件")
if cid6:
    time.sleep(2)
    cid6b, r6b, d6b = chat("现在帮我运行一下这个文件", conv_id=cid6, label="T6b-MULTI-运行文件")

# T7: Complex task
cid7, r7, d7 = chat("列出当前目录下的文件数量统计：多少个.py文件，多少个.md文件", label="T7-COMPLEX-统计")

# T8: Pure math (should NOT use tools)
cid8, r8, d8 = chat("斐波那契数列的前10项是什么", label="T8-QUERY-数学")

print("\n\n" + "=" * 60)
print("TEST SUMMARY")
print("=" * 60)
tests = [
    ("T1-CHAT", r1, "应回复问候"),
    ("T2-QUERY", r2, "应回答2"),
    ("T3-TASK-FS", r3, "应列出.py文件"),
    ("T4-QUERY", r4, "应解释GIL"),
    ("T5-SEARCH", r5, "应有搜索结果"),
    ("T6-CREATE", r6, "应创建文件"),
    ("T7-COMPLEX", r7, "应统计文件"),
    ("T8-MATH", r8, "应列出数列"),
]
for name, reply, expected in tests:
    status = "OK" if reply.strip() else "FAIL (empty)"
    print(f"  {name}: {status} ({len(reply)} chars) - {expected}")
