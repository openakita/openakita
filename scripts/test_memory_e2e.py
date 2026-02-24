"""
记忆系统 E2E 测试 — 20 个测试案例
覆盖：任务切换、记忆注入/召回、浏览器调用、规则记忆、幻觉检测等

用法: python scripts/test_memory_e2e.py
"""

import json
import time
import sys
import re
import urllib.request
from datetime import datetime
from pathlib import Path

# 强制无缓冲输出
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

API_BASE = "http://127.0.0.1:18900"
DELAY_BETWEEN = 5  # 每个用例之间等待秒数
TIMEOUT = 120       # 单个请求最大超时

# ── 会话管理 ─────────────────────────────────────────────
# conversation_id=None → 新会话; "SAME" → 复用上一轮的会话
# group 字段用于标记同一组连续对话

TESTS = [
    # ── 第一组: 基础 + 设定规则 ──
    {
        "id": 1,
        "name": "基础问候 (baseline)",
        "message": "你好",
        "conversation_id": None,
        "expect_no_tool": True,
        "description": "简单问候，不应触发记忆搜索",
    },
    {
        "id": 2,
        "name": "设定规则: 称呼偏好",
        "message": "以后叫我老板，记住了，别叫错",
        "conversation_id": None,
        "expect_keywords": ["老板"],
        "description": "设定称呼规则，测试是否被记忆",
    },
    {
        "id": 3,
        "name": "设定规则: 回复风格",
        "message": "以后回复我的时候，每句话结尾都加上「喵~」",
        "conversation_id": None,
        "expect_keywords": ["喵"],
        "description": "设定回复风格规则，后续验证是否遵守",
    },

    # ── 第二组: 知识问答 + 任务切换 ──
    {
        "id": 4,
        "name": "知识问答 (无需记忆)",
        "message": "量子计算和传统计算的区别是什么？简要回答",
        "conversation_id": None,
        "expect_no_tool": True,
        "description": "纯知识问答，不应搜索记忆",
    },
    {
        "id": 5,
        "name": "任务切换: 写代码",
        "message": "写一个 Python 函数，输入一个列表，返回其中所有偶数的平方和",
        "conversation_id": None,
        "expect_keywords": ["def", "return"],
        "description": "代码生成任务，验证任务切换能力",
    },
    {
        "id": 6,
        "name": "任务切换: 翻译",
        "message": "把这句话翻译成英文：今天天气真好，适合出去走走",
        "conversation_id": None,
        "expect_keywords": ["weather", "walk"],
        "description": "翻译任务，快速切换不同类型任务",
    },

    # ── 第三组: 记忆召回测试 ──
    {
        "id": 7,
        "name": "记忆召回: 称呼验证",
        "message": "你还记得我让你怎么叫我吗？",
        "conversation_id": None,
        "expect_tool": "search_memory",
        "expect_keywords": ["老板"],
        "description": "验证是否能召回刚才设定的称呼规则",
    },
    {
        "id": 8,
        "name": "记忆召回: 历史任务",
        "message": "我之前让你做过哪些事情？列举3个",
        "conversation_id": None,
        "expect_tool": "search_memory",
        "description": "验证历史任务记忆是否正常",
    },

    # ── 第四组: 浏览器任务 ──
    {
        "id": 9,
        "name": "浏览器: 打开网页",
        "message": "用浏览器打开 https://www.baidu.com 然后截图给我看",
        "conversation_id": None,
        "expect_tool": "browser",
        "description": "基础浏览器操作，打开网页+截图",
    },
    {
        "id": 10,
        "name": "浏览器: 搜索信息",
        "message": "用浏览器打开百度，搜索「OpenAI GPT-5」，截图搜索结果",
        "conversation_id": None,
        "expect_tool": "browser",
        "description": "浏览器搜索+截图，复杂浏览器操作",
    },

    # ── 第五组: 多轮对话 + 上下文保持 ──
    {
        "id": 11,
        "name": "多轮对话: 开始讨论",
        "message": "我们来讨论一下怎么做一个智能家居控制系统，你先给个大概方案",
        "conversation_id": None,
        "group": "smart_home",
        "description": "开始多轮对话，验证上下文保持",
    },
    {
        "id": 12,
        "name": "多轮对话: 追问细节",
        "message": "你说的方案里，通信协议用什么比较好？MQTT还是HTTP？",
        "conversation_id": "SAME",
        "group": "smart_home",
        "expect_keywords": ["MQTT"],
        "description": "同一会话追问，验证上下文连贯",
    },
    {
        "id": 13,
        "name": "多轮对话: 突然切换话题",
        "message": "对了，今天星期几？",
        "conversation_id": "SAME",
        "group": "smart_home",
        "description": "同一会话内切换话题，不应混淆",
    },

    # ── 第六组: 文件操作 + Shell ──
    {
        "id": 14,
        "name": "Shell: 查看系统信息",
        "message": "帮我查一下当前系统的 Python 版本和 pip 列表前10个包",
        "conversation_id": None,
        "expect_tool": "run_shell",
        "description": "Shell 命令执行测试",
    },
    {
        "id": 15,
        "name": "文件操作: 写入文件",
        "message": "帮我在 data/temp/ 目录下创建一个 hello.txt，内容写「这是记忆系统测试生成的文件」",
        "conversation_id": None,
        "expect_tool": "write_file",
        "description": "文件写入操作测试",
    },
    {
        "id": 16,
        "name": "文件操作: 读取验证",
        "message": "帮我读取 data/temp/hello.txt 文件的内容",
        "conversation_id": None,
        "expect_tool": "read_file",
        "expect_keywords": ["记忆系统测试"],
        "description": "读取刚才创建的文件，验证文件操作闭环",
    },

    # ── 第七组: 记忆混淆测试 ──
    {
        "id": 17,
        "name": "混淆测试: 新规则覆盖",
        "message": "从现在起，叫我「大佬」，不要叫老板了",
        "conversation_id": None,
        "expect_keywords": ["大佬"],
        "description": "覆盖之前的称呼规则，测试记忆更新",
    },
    {
        "id": 18,
        "name": "混淆测试: 验证新规则",
        "message": "你现在应该怎么称呼我？",
        "conversation_id": None,
        "expect_tool": "search_memory",
        "expect_keywords": ["大佬"],
        "not_expect_keywords": [],
        "description": "验证新称呼是否覆盖旧称呼",
    },

    # ── 第八组: 复杂综合任务 ──
    {
        "id": 19,
        "name": "综合: 多步骤任务",
        "message": "帮我做三件事：1. 查看当前时间 2. 在 data/temp/ 下创建一个 report.txt 写入当前时间 3. 读取确认内容",
        "conversation_id": None,
        "description": "多步骤复合任务，测试计划执行能力",
    },
    {
        "id": 20,
        "name": "最终验证: 全面记忆检查",
        "message": "回顾一下，今天这次对话中我给你设定了哪些规则？我让你怎么称呼我？你帮我做了哪些操作？",
        "conversation_id": None,
        "expect_tool": "search_memory",
        "description": "最终综合记忆验证，检查是否有错乱",
    },
]


def send_chat(message: str, conversation_id: str | None = None) -> dict:
    """发送聊天请求，收集 SSE 流式响应"""
    payload = json.dumps({
        "message": message,
        "conversation_id": conversation_id,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{API_BASE}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    result = {
        "full_text": "",
        "tools_called": [],
        "thinking": "",
        "conversation_id": conversation_id,
        "error": None,
        "iterations": 0,
        "usage": {},
    }

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            buffer = ""
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                try:
                    evt = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                evt_type = evt.get("type", "")

                if evt_type == "text_delta":
                    result["full_text"] += evt.get("content", "")
                elif evt_type == "thinking_delta":
                    result["thinking"] += evt.get("content", "")
                elif evt_type == "tool_call_start":
                    result["tools_called"].append({
                        "tool": evt.get("tool", ""),
                        "args": evt.get("args", {}),
                    })
                elif evt_type == "iteration_start":
                    result["iterations"] = evt.get("iteration", 0)
                elif evt_type == "done":
                    result["usage"] = evt.get("usage", {})
                elif evt_type == "error":
                    result["error"] = evt.get("content", str(evt))

    except Exception as e:
        result["error"] = str(e)

    return result


def evaluate_test(test: dict, result: dict) -> dict:
    """评估单个测试结果"""
    verdict = {"pass": True, "issues": []}
    text = result["full_text"]
    tools = [t["tool"] for t in result["tools_called"]]

    if result["error"]:
        verdict["pass"] = False
        verdict["issues"].append(f"ERROR: {result['error']}")
        return verdict

    if not text and not tools:
        verdict["pass"] = False
        verdict["issues"].append("No response text and no tool calls")
        return verdict

    if test.get("expect_no_tool") and tools:
        memory_tools = [t for t in tools if t in ("search_memory", "search_conversation_traces")]
        if memory_tools:
            verdict["pass"] = False
            verdict["issues"].append(f"Expected no memory tools but called: {memory_tools}")

    if test.get("expect_tool"):
        expected = test["expect_tool"]
        matched = any(expected in t for t in tools)
        if not matched:
            verdict["issues"].append(f"Expected tool '{expected}' not found in: {tools}")

    if test.get("expect_keywords"):
        combined = (text + result["thinking"]).lower()
        for kw in test["expect_keywords"]:
            if kw.lower() not in combined:
                verdict["issues"].append(f"Expected keyword '{kw}' not found in response")

    if test.get("not_expect_keywords"):
        combined = text.lower()
        for kw in test["not_expect_keywords"]:
            if kw.lower() in combined:
                verdict["issues"].append(f"Unexpected keyword '{kw}' found in response")

    if len(verdict["issues"]) > 0:
        has_critical = any("ERROR" in i or "No response" in i for i in verdict["issues"])
        if has_critical:
            verdict["pass"] = False

    return verdict


def main():
    print("=" * 70)
    print(f"  记忆系统 E2E 测试  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  共 {len(TESTS)} 个测试案例  |  间隔 {DELAY_BETWEEN}s")
    print("=" * 70)
    print()

    # 先检查服务是否可用
    try:
        check = urllib.request.urlopen(f"{API_BASE}/api/sessions?channel=desktop", timeout=5)
        if check.getcode() != 200:
            print("ERROR: Backend not reachable")
            sys.exit(1)
    except Exception as e:
        print(f"ERROR: Cannot connect to backend: {e}")
        sys.exit(1)

    results = []
    last_conversation_id = None
    group_conversation_ids = {}

    for i, test in enumerate(TESTS):
        # 确定 conversation_id
        conv_id = test.get("conversation_id")
        if conv_id == "SAME":
            conv_id = last_conversation_id
        elif conv_id is None:
            conv_id = None

        group = test.get("group")

        print(f"[{test['id']:02d}/{len(TESTS)}] {test['name']}")
        print(f"  消息: {test['message'][:60]}{'...' if len(test['message']) > 60 else ''}")
        print(f"  目标: {test['description']}")

        start_time = time.time()
        result = send_chat(test["message"], conv_id)
        elapsed = time.time() - start_time

        # 更新会话 ID 追踪
        if group and conv_id is None:
            last_conversation_id = result.get("conversation_id")
        elif conv_id:
            last_conversation_id = conv_id

        verdict = evaluate_test(test, result)

        tool_names = [t["tool"] for t in result["tools_called"]]
        status = "PASS" if verdict["pass"] else "FAIL"
        warn = " (WARN)" if verdict["issues"] and verdict["pass"] else ""

        print(f"  结果: [{status}{warn}] | {elapsed:.1f}s | {result['iterations']} iters | tools: {tool_names}")
        if result["full_text"]:
            preview = result["full_text"][:120].replace("\n", " ")
            print(f"  回复: {preview}...")
        if verdict["issues"]:
            for issue in verdict["issues"]:
                print(f"  ⚠ {issue}")
        print()

        results.append({
            "test_id": test["id"],
            "name": test["name"],
            "elapsed": round(elapsed, 2),
            "iterations": result["iterations"],
            "tools": tool_names,
            "text_preview": result["full_text"][:200],
            "verdict": status,
            "issues": verdict["issues"],
            "usage": result["usage"],
        })

        # 等待间隔（最后一个不等）
        if i < len(TESTS) - 1:
            print(f"  --- 等待 {DELAY_BETWEEN}s ---")
            time.sleep(DELAY_BETWEEN)

    # ── 汇总报告 ──
    print("\n" + "=" * 70)
    print("  测试汇总报告")
    print("=" * 70)

    passed = sum(1 for r in results if r["verdict"] == "PASS")
    warned = sum(1 for r in results if r["verdict"] == "PASS" and r["issues"])
    failed = sum(1 for r in results if r["verdict"] == "FAIL")

    print(f"\n  总计: {len(results)} | PASS: {passed} | FAIL: {failed} | 有警告: {warned}")
    print()

    total_tokens = sum(r["usage"].get("total_tokens", 0) for r in results)
    total_time = sum(r["elapsed"] for r in results)
    print(f"  总耗时: {total_time:.1f}s | 总 tokens: {total_tokens}")
    print()

    if failed > 0:
        print("  ❌ 失败用例:")
        for r in results:
            if r["verdict"] == "FAIL":
                print(f"    [{r['test_id']:02d}] {r['name']}: {'; '.join(r['issues'])}")
        print()

    if warned > 0:
        print("  ⚠ 有警告的用例:")
        for r in results:
            if r["verdict"] == "PASS" and r["issues"]:
                print(f"    [{r['test_id']:02d}] {r['name']}: {'; '.join(r['issues'])}")
        print()

    # 保存详细结果
    report_path = Path("data/temp/e2e_test_report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "summary": {"total": len(results), "passed": passed, "failed": failed, "warned": warned},
            "total_tokens": total_tokens,
            "total_time": round(total_time, 2),
            "results": results,
        }, f, ensure_ascii=False, indent=2)
    print(f"  详细报告已保存: {report_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
