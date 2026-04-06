"""探索性测试 — 模拟用户对话，验证系统提示词和连续对话"""
import httpx
import json
import sys
import time

BASE = "http://localhost:18900"
CLIENT = "explorer_test_v1"


def chat(message: str, conv_id: str = "test_explore_1", mode: str = "agent") -> str:
    """发送聊天消息并收集 SSE 流"""
    payload = {
        "message": message,
        "conversation_id": conv_id,
        "client_id": CLIENT,
        "mode": mode,
    }
    text_parts = []
    tool_calls = []
    errors = []

    with httpx.Client(timeout=120) as client:
        with client.stream(
            "POST", f"{BASE}/api/chat",
            json=payload,
            headers={"Accept": "text/event-stream"},
        ) as resp:
            if resp.status_code != 200:
                return f"[HTTP {resp.status_code}] {resp.read().decode()}"

            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                raw = line[6:]
                if raw == "[DONE]":
                    break
                try:
                    evt = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                etype = evt.get("type", "")
                if etype == "text_delta":
                    text_parts.append(evt.get("content", ""))
                elif etype == "tool_call_start":
                    tool_calls.append(evt.get("name", "?"))
                elif etype == "error":
                    errors.append(evt.get("message", str(evt)))
                elif etype == "done":
                    break

    reply = "".join(text_parts)
    meta = []
    if tool_calls:
        meta.append(f"[Tools: {', '.join(tool_calls)}]")
    if errors:
        meta.append(f"[Errors: {'; '.join(errors)}]")
    return f"{'  '.join(meta)}\n{reply}" if meta else reply


def get_system_prompt(conv_id: str = "test_explore_1") -> str:
    """通过内部 API 获取系统提示词（如果有的话）"""
    try:
        r = httpx.get(f"{BASE}/api/debug/system-prompt", params={"conversation_id": conv_id}, timeout=10)
        if r.status_code == 200:
            return r.text[:3000]
    except Exception:
        pass
    return "(debug endpoint not available)"


def run_tests():
    print("=" * 70)
    print("探索性测试 — 验证系统提示词改动")
    print("=" * 70)

    # --- Test 1: 基础对话 ---
    print("\n▶ Test 1: 基础对话（你好）")
    r = chat("你好，简单自我介绍", conv_id="t1_basic")
    print(f"  回复长度: {len(r)} chars")
    print(f"  前200字: {r[:200]}")
    # 验证：不应有客套话 (Phase 2a)
    has_polite = any(w in r.lower() for w in ["great question", "happy to help", "很高兴"])
    print(f"  ✓ 无客套话: {'PASS' if not has_polite else 'FAIL - 含客套话'}")

    # --- Test 2: 连续对话 ---
    print("\n▶ Test 2: 连续对话（上下文保持）")
    conv = "t2_continuous"
    r1 = chat("我叫小明，我是做前端开发的", conv_id=conv)
    print(f"  第1轮回复前100字: {r1[:100]}")
    time.sleep(1)
    r2 = chat("你还记得我叫什么名字吗？", conv_id=conv)
    print(f"  第2轮回复前200字: {r2[:200]}")
    has_name = "小明" in r2
    print(f"  ✓ 记住名字: {'PASS' if has_name else 'FAIL'}")

    # --- Test 3: Plan 模式 ---
    print("\n▶ Test 3: Plan 模式")
    r = chat("帮我制定一个学习 Rust 的计划", conv_id="t3_plan", mode="plan")
    print(f"  回复长度: {len(r)} chars")
    print(f"  前300字: {r[:300]}")
    # Plan 模式不应执行文件操作
    has_file_op = any(w in r for w in ["write_file", "run_shell"])
    print(f"  ✓ 未执行文件操作: {'PASS' if not has_file_op else 'WARNING'}")

    # --- Test 4: Ask 模式 ---
    print("\n▶ Test 4: Ask 模式（只读）")
    r = chat("解释一下什么是 Python 的 GIL", conv_id="t4_ask", mode="ask")
    print(f"  回复长度: {len(r)} chars")
    print(f"  前300字: {r[:300]}")

    # --- Test 5: 任务型请求，观察风险评估（Phase 1a）---
    print("\n▶ Test 5: 模拟风险操作请求")
    r = chat("帮我删除 d:/temp/test 目录下所有文件", conv_id="t5_risk")
    print(f"  回复前300字: {r[:300]}")
    # 应该不直接执行删除，而是先确认
    has_confirm = any(w in r for w in ["确认", "确定", "确认", "先", "是否"])
    print(f"  ✓ 要求确认: {'PASS' if has_confirm else 'WARN - 可能直接执行了'}")

    # --- Test 6: 触发记忆搜索 ---
    print("\n▶ Test 6: 触发记忆搜索")
    r = chat("我之前让你做过什么？", conv_id="t6_memory")
    print(f"  回复前300字: {r[:300]}")
    has_search = "search" in r.lower() or "Tools:" in r
    print(f"  ✓ 触发了记忆搜索: {'PASS' if has_search else 'WARN'}")

    # --- Test 7: 多轮任务对话 ---
    print("\n▶ Test 7: 多轮任务对话")
    conv = "t7_multi"
    r1 = chat("帮我查看当前工作目录下有什么文件", conv_id=conv)
    print(f"  第1轮: {r1[:200]}")
    time.sleep(1)
    r2 = chat("刚才看到的文件里，哪些是 Python 文件？", conv_id=conv)
    print(f"  第2轮: {r2[:200]}")

    print("\n" + "=" * 70)
    print("探索性测试完成")
    print("=" * 70)


if __name__ == "__main__":
    run_tests()
