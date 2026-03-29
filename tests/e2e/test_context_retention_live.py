"""
Live multi-turn context retention test.

Calls the real OpenAkita API with actual LLM inference to verify
that context is retained across conversation turns.

Usage:
    python tests/e2e/test_context_retention_live.py [--base-url http://127.0.0.1:18900]
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid

import httpx

BASE_URL = "http://127.0.0.1:18900"
TIMEOUT = 300.0


def send_message(
    client: httpx.Client,
    message: str,
    conversation_id: str,
) -> tuple[str, list[str]]:
    """Send a message via SSE streaming and collect the full response."""
    full_text = ""
    tool_calls = []

    with client.stream(
        "POST",
        f"{BASE_URL}/api/chat",
        json={
            "message": message,
            "conversation_id": conversation_id,
            "mode": "agent",
        },
        timeout=TIMEOUT,
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            payload = line[6:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                evt = json.loads(payload)
            except json.JSONDecodeError:
                continue

            evt_type = evt.get("type", "")
            if evt_type == "text_delta":
                full_text += evt.get("content", "")
            elif evt_type == "tool_call_start":
                tool_calls.append(evt.get("name", "unknown"))
            elif evt_type == "error":
                print(f"  [ERROR] {evt.get('content', evt)}")

    return full_text, tool_calls


def p(label: str, ok: bool, detail: str = ""):
    icon = "✅" if ok else "❌"
    print(f"  {icon} {label}" + (f"  ({detail})" if detail else ""))
    return ok


def run_test(base_url: str):
    global BASE_URL
    BASE_URL = base_url

    client = httpx.Client()
    conv_id = f"ctx_test_{uuid.uuid4().hex[:8]}"

    print("=" * 70)
    print("上下文记忆保持 — 实时多轮对话测试")
    print(f"API: {BASE_URL}  |  conversation_id: {conv_id}")
    print("=" * 70)

    passed = 0
    failed = 0

    def check(label: str, ok: bool, detail: str = ""):
        nonlocal passed, failed
        if ok:
            passed += 1
        else:
            failed += 1
        icon = "✅" if ok else "❌"
        print(f"  {icon} {label}" + (f"  ({detail})" if detail else ""))

    # ───────── 场景 1: 基础事实记忆 ─────────
    print("\n── 场景 1: 基础事实记忆 ──")

    print("\n[Turn 1] 建立事实...")
    t1, t1_tools = send_message(
        client,
        "请记住以下信息：我的项目代号是Phoenix，团队有5个人，使用Python和React技术栈。不需要使用任何工具，直接确认你记住了即可。",
        conv_id,
    )
    print(f"  → {t1[:200]}")

    print("\n[Turn 2] 追问项目代号...")
    t2, t2_tools = send_message(
        client,
        "我的项目代号叫什么？直接回答，不需要搜索。",
        conv_id,
    )
    print(f"  → {t2[:200]}")
    check(
        "记住项目代号 Phoenix",
        "phoenix" in t2.lower(),
        f"response contains 'Phoenix': {'phoenix' in t2.lower()}",
    )
    check(
        "不使用搜索工具",
        not any("search" in t.lower() or "web" in t.lower() for t in t2_tools),
        f"tools: {t2_tools}",
    )

    print("\n[Turn 3] 追问团队人数和技术栈...")
    t3, t3_tools = send_message(
        client,
        "团队多少人？用什么技术？直接回答。",
        conv_id,
    )
    print(f"  → {t3[:200]}")
    check("记住团队人数 5", "5" in t3)
    check("记住技术栈 Python", "python" in t3.lower())

    # ───────── 场景 2: 计算结果引用 ─────────
    print("\n── 场景 2: 计算结果引用 ──")

    print("\n[Turn 4] 给出计算...")
    t4, _ = send_message(
        client,
        "帮我算一下 17 × 23 等于多少，直接口算回答即可。",
        conv_id,
    )
    print(f"  → {t4[:200]}")
    check("计算 17×23=391 正确", "391" in t4)

    print("\n[Turn 5] 追问计算结果...")
    t5, _ = send_message(
        client,
        "刚才那个计算结果再加100是多少？",
        conv_id,
    )
    print(f"  → {t5[:200]}")
    check("追问 391+100=491", "491" in t5)

    # ───────── 场景 3: 多轮对话后回溯 ─────────
    print("\n── 场景 3: 填充对话后回溯 ──")

    fillers = [
        "简单介绍一下什么是微服务架构，三句话以内。",
        "什么是 REST API？一句话解释。",
        "Docker 和虚拟机的区别是什么？简短回答。",
        "什么是 CI/CD？简短回答。",
        "Git rebase 和 merge 的区别？一句话。",
    ]
    for i, q in enumerate(fillers):
        turn = 6 + i
        print(f"\n[Turn {turn}] {q[:40]}...")
        text, _ = send_message(client, q, conv_id)
        print(f"  → {text[:120]}...")

    print("\n[Turn 11] 回溯到 Turn 1 的信息...")
    t11, t11_tools = send_message(
        client,
        "回到最开始的话题，我告诉你的项目信息 — 代号、人数、技术栈分别是什么？直接回答。",
        conv_id,
    )
    print(f"  → {t11[:300]}")
    check("11轮后记住代号 Phoenix", "phoenix" in t11.lower())
    check("11轮后记住人数 5", "5" in t11)
    check(
        "11轮后记住技术 Python/React",
        "python" in t11.lower() or "react" in t11.lower(),
    )
    check(
        "回溯时不搜索",
        not any("search" in t.lower() or "web" in t.lower() for t in t11_tools),
        f"tools: {t11_tools}",
    )

    # ───────── 场景 4: 纠正记忆 ─────────
    print("\n── 场景 4: 纠正记忆 ──")

    print("\n[Turn 12] 更新信息...")
    t12, _ = send_message(
        client,
        "更正一下，项目代号改为 Pegasus 了，团队扩展到8个人。直接确认。",
        conv_id,
    )
    print(f"  → {t12[:200]}")

    print("\n[Turn 13] 验证纠正...")
    t13, _ = send_message(
        client,
        "现在项目代号和团队人数分别是什么？",
        conv_id,
    )
    print(f"  → {t13[:200]}")
    check("纠正后代号变为 Pegasus", "pegasus" in t13.lower())
    check("纠正后人数变为 8", "8" in t13)

    # ───────── 结果汇总 ─────────
    total = passed + failed
    print("\n" + "=" * 70)
    print(f"测试结果: {passed}/{total} 通过" + (" ✅ 全部通过!" if failed == 0 else f" ({failed} 项失败)"))
    print("=" * 70)

    client.close()
    return failed == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18900")
    args = parser.parse_args()

    ok = run_test(args.base_url)
    sys.exit(0 if ok else 1)
