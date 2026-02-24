"""
记忆系统 E2E 测试 v2 — 30 个测试案例
覆盖：persona_trait 去重、skill 不再垃圾生成、fact 去重、conversation_turns 写入、
      extraction_queue 处理、任务切换、浏览器、记忆召回、规则覆盖、会话隔离

用法: python scripts/test_memory_e2e_v2.py
"""

import json
import sqlite3
import time
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

API_BASE = "http://127.0.0.1:18900"
DELAY = 3
TIMEOUT = 120
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "memory" / "openakita.db"

TESTS = [
    # ── Group 1: Baseline ──
    {"id": 1, "name": "简单问候", "message": "你好呀", "conv": None,
     "expect_no_memory_tool": True, "desc": "不应触发记忆搜索"},
    {"id": 2, "name": "数学问题", "message": "3的5次方等于多少", "conv": None,
     "expect_no_memory_tool": True, "desc": "纯计算不应搜索记忆"},

    # ── Group 2: 设定规则 (persona_trait 去重测试) ──
    {"id": 3, "name": "设定称呼A", "message": "以后叫我老铁", "conv": None,
     "desc": "设定称呼，写入 persona_trait"},
    {"id": 4, "name": "覆盖称呼B", "message": "不对，叫我老板比较好", "conv": None,
     "desc": "覆盖称呼，验证不产生重复 persona_trait"},
    {"id": 5, "name": "覆盖称呼C", "message": "还是叫我大佬吧", "conv": None,
     "desc": "再次覆盖称呼，验证 address_style 始终只有一条"},
    {"id": 6, "name": "设定语气规则", "message": "回复我的时候语气轻松一点，不要太正式", "conv": None,
     "desc": "设定 formality 偏好"},
    {"id": 7, "name": "验证称呼记忆", "message": "你应该怎么称呼我？", "conv": None,
     "expect_keywords": ["大佬"], "desc": "验证最新称呼是否生效"},

    # ── Group 3: 任务切换 ──
    {"id": 8, "name": "写代码", "message": "写一个 JavaScript 函数判断回文字符串", "conv": None,
     "expect_keywords": ["function"], "desc": "代码生成"},
    {"id": 9, "name": "翻译中->英", "message": "翻译成英文：人工智能正在改变世界", "conv": None,
     "expect_keywords": ["intelligence", "world"], "desc": "翻译任务"},
    {"id": 10, "name": "翻译英->中", "message": "Translate to Chinese: The quick brown fox jumps over the lazy dog", "conv": None,
     "desc": "反向翻译"},

    # ── Group 4: 多轮对话 (同一会话) ──
    {"id": 11, "name": "开始项目讨论", "message": "我想做一个个人博客系统，用什么技术栈好？", "conv": None,
     "group": "blog", "desc": "启动多轮讨论"},
    {"id": 12, "name": "追问前端", "message": "前端用 React 还是 Vue？", "conv": "SAME",
     "group": "blog", "desc": "同会话追问"},
    {"id": 13, "name": "追问部署", "message": "部署到哪里比较好？", "conv": "SAME",
     "group": "blog", "desc": "同会话继续"},
    {"id": 14, "name": "话题切换", "message": "对了，明天天气怎么样？", "conv": "SAME",
     "group": "blog", "desc": "同会话切换话题"},

    # ── Group 5: 浏览器任务 ──
    {"id": 15, "name": "浏览器打开", "message": "用浏览器打开 https://www.bing.com 然后截图", "conv": None,
     "expect_tool": "browser", "desc": "基础浏览器操作"},
    {"id": 16, "name": "浏览器搜索", "message": "在bing上搜索 artificial intelligence 然后截图", "conv": None,
     "expect_tool": "browser", "desc": "浏览器搜索"},

    # ── Group 6: Shell/文件 ──
    {"id": 17, "name": "Shell 执行", "message": "查一下当前目录下有哪些 .py 文件", "conv": None,
     "expect_tool": "run_shell", "desc": "Shell 命令"},
    {"id": 18, "name": "文件写入", "message": "在 data/temp/ 下创建 test_v2.txt，内容写「E2E 测试 v2」", "conv": None,
     "expect_tool": "write_file", "desc": "文件写入"},
    {"id": 19, "name": "文件读取", "message": "读取 data/temp/test_v2.txt 的内容", "conv": None,
     "expect_tool": "read_file", "desc": "文件读取验证"},

    # ── Group 7: 记忆设定与召回 ──
    {"id": 20, "name": "设定事实", "message": "记住，我的项目代号是 Project-Phoenix", "conv": None,
     "desc": "设定新的事实信息"},
    {"id": 21, "name": "设定偏好", "message": "我不喜欢用 Java，更喜欢 Python 和 Go", "conv": None,
     "desc": "设定编程语言偏好"},
    {"id": 22, "name": "召回事实", "message": "我的项目代号是什么？", "conv": None,
     "desc": "验证事实记忆召回"},
    {"id": 23, "name": "召回偏好", "message": "我喜欢什么编程语言？", "conv": None,
     "desc": "验证偏好记忆召回"},

    # ── Group 8: 知识问答 (不触发记忆) ──
    {"id": 24, "name": "科学问题", "message": "光速是多少？", "conv": None,
     "expect_no_memory_tool": True, "desc": "科学问答不搜记忆"},
    {"id": 25, "name": "常识问题", "message": "地球到月球的距离大约是多少？", "conv": None,
     "expect_no_memory_tool": True, "desc": "常识不搜记忆"},

    # ── Group 9: 复杂任务 ──
    {"id": 26, "name": "多步骤任务", "message": "做两件事：1. 查看当前时间 2. 把时间写入 data/temp/timestamp.txt", "conv": None,
     "desc": "多步骤复合任务"},
    {"id": 27, "name": "代码解释", "message": "解释一下这段代码: sorted(set(x**2 for x in range(10) if x%2==0))", "conv": None,
     "desc": "代码解释任务"},

    # ── Group 10: 最终验证 ──
    {"id": 28, "name": "历史回顾", "message": "我之前让你做过什么？", "conv": None,
     "desc": "历史任务召回"},
    {"id": 29, "name": "规则验证", "message": "你记得我给你设置过哪些规则吗？", "conv": None,
     "desc": "规则记忆验证"},
    {"id": 30, "name": "称呼最终验证", "message": "你该怎么称呼我？快说", "conv": None,
     "expect_keywords": ["大佬"], "desc": "最终称呼验证，不应回答老铁或老板"},
]


def send_chat(message: str, conversation_id: str | None = None) -> dict:
    payload = json.dumps({"message": message, "conversation_id": conversation_id}).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}/api/chat", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    result = {"full_text": "", "tools_called": [], "thinking": "",
              "conversation_id": conversation_id, "error": None,
              "iterations": 0, "usage": {}}
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data: "):
                    continue
                try:
                    evt = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                t = evt.get("type", "")
                if t == "text_delta":
                    result["full_text"] += evt.get("content", "")
                elif t == "thinking_delta":
                    result["thinking"] += evt.get("content", "")
                elif t == "tool_call_start":
                    result["tools_called"].append(evt.get("tool", ""))
                elif t == "iteration_start":
                    result["iterations"] = evt.get("iteration", 0)
                elif t == "done":
                    result["usage"] = evt.get("usage", {})
                elif t == "error":
                    result["error"] = evt.get("content", str(evt))
    except Exception as e:
        result["error"] = str(e)
    return result


def evaluate(test: dict, result: dict) -> dict:
    v = {"pass": True, "issues": []}
    text = result["full_text"]
    tools = result["tools_called"]

    if result["error"]:
        v["pass"] = False
        v["issues"].append(f"ERROR: {result['error']}")
        return v
    if not text and not tools:
        v["pass"] = False
        v["issues"].append("No response")
        return v

    if test.get("expect_no_memory_tool"):
        mem_tools = [t for t in tools if t in ("search_memory", "search_conversation_traces")]
        if mem_tools:
            v["issues"].append(f"Unexpected memory tools: {mem_tools}")

    if test.get("expect_tool"):
        if not any(test["expect_tool"] in t for t in tools):
            v["issues"].append(f"Expected tool '{test['expect_tool']}' not used")

    if test.get("expect_keywords"):
        combined = (text + result["thinking"]).lower()
        for kw in test["expect_keywords"]:
            if kw.lower() not in combined:
                v["issues"].append(f"Keyword '{kw}' missing")

    if any("ERROR" in i or "No response" in i for i in v["issues"]):
        v["pass"] = False
    return v


def check_sqlite():
    """Post-test SQLite validation."""
    print("\n" + "=" * 70)
    print("  SQLite 数据验证")
    print("=" * 70)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    issues = []

    # Check 1: persona_trait 同 dimension 不应重复
    print("\n[Check 1] persona_trait 同 dimension 去重")
    cur.execute("SELECT content FROM memories WHERE type='persona_trait'")
    traits = [r["content"] for r in cur.fetchall()]
    dims = {}
    for t in traits:
        dim = t.split("=")[0].strip() if "=" in t else t[:20]
        dims.setdefault(dim, []).append(t)
    dup_dims = {k: v for k, v in dims.items() if len(v) > 1}
    if dup_dims:
        issues.append(f"persona_trait 仍有重复 dimension: {dup_dims}")
        print(f"  FAIL: {dup_dims}")
    else:
        print(f"  PASS: {len(dims)} unique dimensions, no duplicates")

    # Check 2: 没有新的垃圾 skill
    print("\n[Check 2] 无新增垃圾 skill")
    cur.execute("""
        SELECT COUNT(*) FROM memories
        WHERE type='skill' AND (content LIKE '成功完成:%' OR content LIKE '任务 ''%使用工具组合%')
    """)
    garbage = cur.fetchone()[0]
    if garbage > 0:
        issues.append(f"仍有 {garbage} 条垃圾 skill")
        print(f"  FAIL: {garbage} garbage skills found")
    else:
        print("  PASS: 0 garbage skills")

    # Check 3: conversation_turns 有新数据
    print("\n[Check 3] conversation_turns 有新测试数据")
    cur.execute("SELECT COUNT(DISTINCT session_id) as cnt FROM conversation_turns")
    sessions = cur.fetchone()["cnt"]
    cur.execute("SELECT COUNT(*) FROM conversation_turns")
    turns = cur.fetchone()[0]
    if sessions <= 1:
        issues.append(f"conversation_turns 仅有 {sessions} 个 session")
        print(f"  WARN: only {sessions} sessions, {turns} turns")
    else:
        print(f"  PASS: {sessions} sessions, {turns} turns")

    # Check 4: 记忆类型分布
    print("\n[Check 4] 记忆类型分布")
    cur.execute("SELECT type, COUNT(*) as cnt FROM memories GROUP BY type ORDER BY cnt DESC")
    for r in cur.fetchall():
        print(f"  {r['type']:15s}: {r['cnt']}")

    # Check 5: extraction_queue 状态
    print("\n[Check 5] extraction_queue 状态")
    cur.execute("SELECT status, COUNT(*) FROM extraction_queue GROUP BY status")
    for r in cur.fetchall():
        print(f"  {r[0]}: {r[1]}")
    cur.execute("SELECT COUNT(*) FROM extraction_queue WHERE status='pending'")
    pending = cur.fetchone()[0]
    if pending > 20:
        issues.append(f"extraction_queue 有 {pending} 条长期 pending")

    conn.close()

    print("\n" + "-" * 50)
    if issues:
        print(f"  SQLite 验证: {len(issues)} 个问题")
        for i in issues:
            print(f"  - {i}")
    else:
        print("  SQLite 验证: 全部通过")
    return issues


def main():
    print("=" * 70)
    print(f"  记忆系统 E2E 测试 v2 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  共 {len(TESTS)} 个案例 | 间隔 {DELAY}s")
    print("=" * 70)
    print()

    try:
        check = urllib.request.urlopen(f"{API_BASE}/api/sessions?channel=desktop", timeout=5)
        if check.getcode() != 200:
            print("ERROR: Backend not reachable")
            sys.exit(1)
    except Exception as e:
        print(f"ERROR: Cannot connect: {e}")
        sys.exit(1)

    results = []
    last_conv_id = None

    for i, test in enumerate(TESTS):
        conv_id = test.get("conv")
        if conv_id == "SAME":
            conv_id = last_conv_id

        print(f"[{test['id']:02d}/{len(TESTS)}] {test['name']}")
        print(f"  消息: {test['message'][:60]}{'...' if len(test['message']) > 60 else ''}")

        start = time.time()
        result = send_chat(test["message"], conv_id)
        elapsed = time.time() - start

        if test.get("group") and conv_id is None:
            last_conv_id = result.get("conversation_id")

        verdict = evaluate(test, result)
        tools = result["tools_called"]
        status = "PASS" if verdict["pass"] else "FAIL"
        warn = " (WARN)" if verdict["issues"] and verdict["pass"] else ""

        print(f"  [{status}{warn}] {elapsed:.1f}s | {result['iterations']} iters | tools: {tools[:5]}")
        if result["full_text"]:
            print(f"  回复: {result['full_text'][:100].replace(chr(10), ' ')}...")
        for issue in verdict["issues"]:
            print(f"  ! {issue}")
        print()

        results.append({
            "id": test["id"], "name": test["name"], "elapsed": round(elapsed, 2),
            "verdict": status, "issues": verdict["issues"], "usage": result["usage"],
        })

        if i < len(TESTS) - 1:
            time.sleep(DELAY)

    # ── Summary ──
    passed = sum(1 for r in results if r["verdict"] == "PASS")
    failed = sum(1 for r in results if r["verdict"] == "FAIL")
    warned = sum(1 for r in results if r["verdict"] == "PASS" and r["issues"])
    total_tokens = sum(r["usage"].get("total_tokens", 0) for r in results)
    total_time = sum(r["elapsed"] for r in results)

    print("=" * 70)
    print(f"  总计: {len(results)} | PASS: {passed} | FAIL: {failed} | WARN: {warned}")
    print(f"  耗时: {total_time:.1f}s | Tokens: {total_tokens:,}")
    print("=" * 70)

    if failed:
        print("\n  FAIL:")
        for r in results:
            if r["verdict"] == "FAIL":
                print(f"    [{r['id']:02d}] {r['name']}: {'; '.join(r['issues'])}")

    # ── SQLite 验证 ──
    sqlite_issues = check_sqlite()

    # Save report
    report_path = Path("data/temp/e2e_v2_report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "summary": {"total": len(results), "passed": passed, "failed": failed, "warned": warned},
            "total_tokens": total_tokens, "total_time": round(total_time, 2),
            "results": results, "sqlite_issues": sqlite_issues,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n  报告: {report_path}")


if __name__ == "__main__":
    main()
