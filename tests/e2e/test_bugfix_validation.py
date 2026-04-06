"""
Bug Fix Validation Test — 20 test cases
Focuses on: empty content retry, memory retention across turns,
multi-turn context, and agent profile switching.
"""

import json
import time
import uuid
import os
import subprocess
from dataclasses import dataclass, field

import requests

BASE = "http://127.0.0.1:18900"


@dataclass
class Result:
    id: int
    name: str
    success: bool
    duration_ms: float
    response: str = ""
    tools: list[str] = field(default_factory=list)
    bugs: list[str] = field(default_factory=list)


def chat(message, conv_id, timeout_s=300):
    body = {"message": message, "conversation_id": conv_id}
    text_parts, tools = [], []
    t0 = time.time()
    try:
        with requests.post(
            f"{BASE}/api/chat", json=body, stream=True, timeout=timeout_s,
        ) as resp:
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw:
                    continue
                try:
                    evt = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if evt.get("type") == "text_delta":
                    text_parts.append(evt.get("content", ""))
                elif evt.get("type") == "tool_call_start":
                    tools.append(evt.get("name", "?"))
    except requests.exceptions.Timeout:
        return "".join(text_parts) or "(TIMEOUT)", tools, (time.time() - t0) * 1000
    except Exception as e:
        return f"(ERROR: {e})", tools, (time.time() - t0) * 1000
    return "".join(text_parts), tools, (time.time() - t0) * 1000


def wait_idle(max_wait=180):
    t0 = time.time()
    while time.time() - t0 < max_wait:
        try:
            r = requests.get(f"{BASE}/api/chat/busy", timeout=5)
            d = r.json()
            if not d.get("busy_conversations"):
                return True
        except Exception:
            pass
        time.sleep(3)
    return False


def run():
    results: list[Result] = []

    print(f"{'='*70}")
    print("  Bug Fix Validation — 20 test cases")
    print(f"{'='*70}\n")

    try:
        r = requests.get(f"{BASE}/api/chat/busy", timeout=5)
        if r.status_code != 200:
            print("Backend not available")
            return
    except Exception:
        print("Backend not available")
        return

    # ================================================================
    # GROUP A: Empty content handling (tests 1-4)
    # ================================================================
    for i, msg in enumerate([
        "FastAPI和Flask的主要区别是什么？列出3点",
        "执行 echo hello，告诉我输出",
        "用一句话解释什么是微服务",
        "1+1等于几？",
    ], 1):
        wait_idle()
        conv = f"empty_{i}_{uuid.uuid4().hex[:6]}"
        print(f"  #{i:02d} [empty-content] {msg[:40]}")
        text, tools, ms = chat(msg, conv)
        bugs = []
        if not text or text.startswith("⚠️ 大模型返回异常"):
            bugs.append("BUG: Empty/error response from LLM")
        if "[REPLY]" in text:
            bugs.append("BUG: [REPLY] tag leaked")
        r = Result(i, f"空内容-{i}", bool(text and not text.startswith("⚠️")),
                    ms, text[:150], tools, bugs)
        results.append(r)
        status = "✓" if r.success else "✗"
        print(f"      {status} {ms:.0f}ms | {text[:80].replace(chr(10),'↵')}")
        for b in bugs:
            print(f"      ⚠ {b}")
        print()
        time.sleep(2)

    # ================================================================
    # GROUP B: Memory retention within session (tests 5-9)
    # ================================================================
    conv_mem = f"memory_{uuid.uuid4().hex[:8]}"
    mem_turns = [
        (5, "创建文件", "在当前目录创建 bugfix_test_file.py，内容是 def greet(name): return f'Hello {name}'"),
        (6, "运行验证", "运行刚才创建的 bugfix_test_file.py，调用 greet('World')，告诉我输出"),
        (7, "回忆文件", "刚才创建的文件叫什么名字？函数签名是什么？"),
        (8, "修改函数", "把 greet 函数改成支持第二个参数 greeting，默认值 'Hello'"),
        (9, "最终回忆", "总结一下我们创建了什么文件、函数最终长什么样"),
    ]
    for tid, label, msg in mem_turns:
        wait_idle()
        print(f"  #{tid:02d} [memory] {label}")
        text, tools, ms = chat(msg, conv_mem)
        bugs = []
        if not text:
            bugs.append("BUG: Empty response")
        if tid == 7:
            if "bugfix_test_file" not in text.lower():
                bugs.append("BUG: Forgot file name")
            if "greet" not in text.lower():
                bugs.append("BUG: Forgot function name")
        if tid == 9:
            if "greet" not in text.lower():
                bugs.append("BUG: Forgot function in summary")
            if "greeting" not in text.lower():
                bugs.append("BUG: Forgot modified parameter")
        r = Result(tid, f"记忆-{label}", not bool(bugs), ms, text[:150], tools, bugs)
        results.append(r)
        status = "✓" if r.success else "✗"
        print(f"      {status} {ms:.0f}ms | {text[:80].replace(chr(10),'↵')}")
        for b in bugs:
            print(f"      ⚠ {b}")
        print()
        time.sleep(2)

    # ================================================================
    # GROUP C: Chat / knowledge (tests 10-13)
    # ================================================================
    for i, msg in enumerate([
        "你好，今天心情怎么样？",
        "用三句话解释量子纠缠",
        "写一个冒泡排序的Python代码",
        "中国有多少个省级行政区？",
    ], 10):
        wait_idle()
        conv = f"chat_{i}_{uuid.uuid4().hex[:6]}"
        print(f"  #{i:02d} [chat] {msg[:40]}")
        text, tools, ms = chat(msg, conv)
        bugs = []
        if not text:
            bugs.append("BUG: Empty response")
        if "[REPLY]" in text or "[TOOL_CALLS]" in text:
            bugs.append("BUG: Internal tags leaked")
        r = Result(i, f"闲聊-{i}", bool(text), ms, text[:150], tools, bugs)
        results.append(r)
        status = "✓" if r.success else "✗"
        print(f"      {status} {ms:.0f}ms | {text[:80].replace(chr(10),'↵')}")
        for b in bugs:
            print(f"      ⚠ {b}")
        print()
        time.sleep(2)

    # ================================================================
    # GROUP D: Tool usage (tests 14-16)
    # ================================================================
    for i, msg in enumerate([
        "读一下 pyproject.toml 文件的前5行",
        "列出 src/openakita/core/ 目录下的文件",
        "搜索我的记忆里有没有关于 'email' 的内容",
    ], 14):
        wait_idle()
        conv = f"tool_{i}_{uuid.uuid4().hex[:6]}"
        print(f"  #{i:02d} [tool] {msg[:40]}")
        text, tools, ms = chat(msg, conv)
        bugs = []
        if not text:
            bugs.append("BUG: Empty response")
        if not tools:
            bugs.append("WARN: Expected tool usage but none")
        r = Result(i, f"工具-{i}", bool(text), ms, text[:150], tools, bugs)
        results.append(r)
        status = "✓" if r.success else "✗"
        print(f"      {status} {ms:.0f}ms | tools={tools[:3]} | {text[:60].replace(chr(10),'↵')}")
        for b in bugs:
            print(f"      ⚠ {b}")
        print()
        time.sleep(2)

    # ================================================================
    # GROUP E: Multi-turn task continuity (tests 17-20)
    # ================================================================
    conv_task = f"task_{uuid.uuid4().hex[:8]}"
    task_turns = [
        (17, "执行任务", "帮我创建一个文件 email_config.json，内容是 {\"smtp_host\": \"smtp.gmail.com\", \"port\": 587, \"use_tls\": true}"),
        (18, "追问配置", "刚才那个配置文件里 SMTP 端口号是多少？用的什么加密方式？"),
        (19, "修改配置", "把端口号改成 465，加密方式改成 SSL"),
        (20, "验证修改", "现在配置文件里的端口和加密方式是什么？读文件确认一下"),
    ]
    for tid, label, msg in task_turns:
        wait_idle()
        print(f"  #{tid:02d} [task-cont] {label}")
        text, tools, ms = chat(msg, conv_task)
        bugs = []
        if not text:
            bugs.append("BUG: Empty response")
        if tid == 18:
            if "587" not in text:
                bugs.append("BUG: Forgot port 587")
            if "tls" not in text.lower() and "starttls" not in text.lower():
                bugs.append("BUG: Forgot TLS config")
        if tid == 20:
            if "465" not in text:
                bugs.append("BUG: Port not updated to 465")
            if "ssl" not in text.lower():
                bugs.append("BUG: Encryption not updated to SSL")
        r = Result(tid, f"任务连续-{label}", not bool(bugs), ms, text[:150], tools, bugs)
        results.append(r)
        status = "✓" if r.success else "✗"
        print(f"      {status} {ms:.0f}ms | {text[:80].replace(chr(10),'↵')}")
        for b in bugs:
            print(f"      ⚠ {b}")
        print()
        time.sleep(2)

    # ================================================================
    # SUMMARY
    # ================================================================
    print(f"\n{'='*70}")
    print("  SUMMARY")
    print(f"{'='*70}")
    total = len(results)
    passed = sum(1 for r in results if r.success)
    failed = total - passed
    with_bugs = sum(1 for r in results if r.bugs)
    avg_ms = sum(r.duration_ms for r in results) / total if total else 0

    print(f"  Total: {total} | Passed: {passed} | Failed: {failed} | With warnings: {with_bugs}")
    print(f"  Average response time: {avg_ms:.0f}ms\n")

    groups = {}
    for r in results:
        g = r.name.split("-")[0]
        groups.setdefault(g, []).append(r)
    print(f"  {'Group':<12s} {'Pass':>5s} {'Fail':>5s}")
    print(f"  {'-'*24}")
    for g, rs in groups.items():
        p = sum(1 for r in rs if r.success)
        f = len(rs) - p
        print(f"  {g:<12s} {p:>5d} {f:>5d}")

    if any(r.bugs for r in results):
        print(f"\n  BUG DETAILS:")
        for r in results:
            if r.bugs:
                print(f"    #{r.id:02d} {r.name}: {'; '.join(r.bugs)}")

    report_path = os.path.join(os.path.dirname(__file__), "bugfix_test_results.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump([{
            "id": r.id, "name": r.name, "success": r.success,
            "duration_ms": round(r.duration_ms),
            "response_preview": r.response, "tools": r.tools, "bugs": r.bugs,
        } for r in results], f, ensure_ascii=False, indent=2)
    print(f"\n  Results saved to: {report_path}\n")


if __name__ == "__main__":
    run()
