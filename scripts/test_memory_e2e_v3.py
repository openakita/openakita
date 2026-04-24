"""
记忆系统 E2E 测试 v3 — 60 个测试案例
结果输出到 data/temp/e2e_v3_report.txt + .json
"""

import json
import sqlite3
import time
import sys
import uuid
import urllib.request
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

API_BASE = "http://127.0.0.1:18900"
DELAY = 2
TIMEOUT = 120
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "memory" / "openakita.db"
REPORT_TXT = Path(__file__).resolve().parent.parent / "data" / "temp" / "e2e_v3_report.txt"
REPORT_JSON = Path(__file__).resolve().parent.parent / "data" / "temp" / "e2e_v3_report.json"

# 预分配会话 ID，确保相关测试共享 conversation
CONV_NICKNAME = f"conv_nick_{uuid.uuid4().hex[:8]}"
CONV_MULTITURN = f"conv_multi_{uuid.uuid4().hex[:8]}"
CONV_RULE = f"conv_rule_{uuid.uuid4().hex[:8]}"
CONV_PROJECT = f"conv_proj_{uuid.uuid4().hex[:8]}"
CONV_CODING = f"conv_code_{uuid.uuid4().hex[:8]}"
CONV_MEMORY_RECALL = f"conv_recall_{uuid.uuid4().hex[:8]}"

TESTS = [
    # ══════ Group 1: Baseline — 不应触发记忆 ══════
    {"id": 1, "name": "简单问候", "msg": "你好", "conv": None,
     "no_mem_tool": True},
    {"id": 2, "name": "数学计算", "msg": "127 × 53 等于多少", "conv": None,
     "no_mem_tool": True},
    {"id": 3, "name": "常识-光速", "msg": "光速是多少", "conv": None,
     "no_mem_tool": True},
    {"id": 4, "name": "常识-月球", "msg": "月球离地球多远", "conv": None,
     "no_mem_tool": True},
    {"id": 5, "name": "中文翻译", "msg": "翻译成英文：今天天气真好", "conv": None,
     "kw": ["weather", "nice", "today"]},
    {"id": 6, "name": "英文翻译", "msg": "Translate: The cat sat on the mat", "conv": None},

    # ══════ Group 2: 称呼设定与覆盖（同一会话） ══════
    {"id": 7, "name": "设定称呼-铁子", "msg": "以后叫我铁子", "conv": CONV_NICKNAME},
    {"id": 8, "name": "验证称呼-铁子", "msg": "你现在怎么称呼我？", "conv": CONV_NICKNAME,
     "kw": ["铁子"]},
    {"id": 9, "name": "覆盖称呼-Boss", "msg": "不对，叫我Boss", "conv": CONV_NICKNAME},
    {"id": 10, "name": "验证称呼-Boss", "msg": "再说一次，你该怎么叫我？", "conv": CONV_NICKNAME,
     "kw": ["Boss"]},
    {"id": 11, "name": "跨会话验证称呼", "msg": "你该怎么称呼我？", "conv": None,
     "desc": "跨会话后是否记住最新称呼"},

    # ══════ Group 3: 规则设定与验证（同一会话） ══════
    {"id": 12, "name": "设定规则-喵", "msg": "从现在开始，你每句话结尾都要加上「喵~」", "conv": CONV_RULE},
    {"id": 13, "name": "验证规则-喵", "msg": "今天星期几？", "conv": CONV_RULE,
     "kw": ["喵"]},
    {"id": 14, "name": "追加规则-emoji", "msg": "另外，每条回复开头都加一个合适的emoji", "conv": CONV_RULE},
    {"id": 15, "name": "验证双规则", "msg": "帮我算一下 2+3", "conv": CONV_RULE,
     "kw": ["喵"]},
    {"id": 16, "name": "取消规则-喵", "msg": "不用加喵了，太幼稚了", "conv": CONV_RULE},
    {"id": 17, "name": "验证规则取消", "msg": "1+1等于几", "conv": CONV_RULE,
     "desc": "不应再出现喵"},

    # ══════ Group 4: 事实记忆设定与召回 ══════
    {"id": 18, "name": "设定事实-生日", "msg": "记住，我的生日是3月15日", "conv": None},
    {"id": 19, "name": "设定事实-城市", "msg": "我住在深圳", "conv": None},
    {"id": 20, "name": "设定事实-项目", "msg": "我现在在做的项目叫 SkyNet-Alpha", "conv": None},
    {"id": 21, "name": "设定偏好-语言", "msg": "我最喜欢用 Rust 和 TypeScript，讨厌 PHP", "conv": None},
    {"id": 22, "name": "召回-生日", "msg": "我的生日是哪天？", "conv": CONV_MEMORY_RECALL,
     "kw": ["3月15"]},
    {"id": 23, "name": "召回-城市", "msg": "我住在哪个城市？", "conv": CONV_MEMORY_RECALL,
     "kw": ["深圳"]},
    {"id": 24, "name": "召回-项目", "msg": "我在做什么项目？", "conv": CONV_MEMORY_RECALL},
    {"id": 25, "name": "召回-偏好", "msg": "我喜欢和讨厌什么编程语言？", "conv": CONV_MEMORY_RECALL},

    # ══════ Group 5: 多轮对话（同一话题延续） ══════
    {"id": 26, "name": "讨论-开始", "msg": "我想搭一个智能家居系统，你觉得需要哪些硬件？", "conv": CONV_MULTITURN},
    {"id": 27, "name": "讨论-追问1", "msg": "传感器用哪种好？", "conv": CONV_MULTITURN},
    {"id": 28, "name": "讨论-追问2", "msg": "中枢控制器推荐用树莓派还是ESP32？", "conv": CONV_MULTITURN},
    {"id": 29, "name": "讨论-突然切换", "msg": "对了，帮我查一下今天的新闻", "conv": CONV_MULTITURN,
     "desc": "同会话内话题切换"},
    {"id": 30, "name": "讨论-回到原题", "msg": "回到刚才的话题，智能家居系统还需要什么软件？", "conv": CONV_MULTITURN},

    # ══════ Group 6: 代码任务（同一会话） ══════
    {"id": 31, "name": "写Python函数", "msg": "用 Python 写一个函数，判断一个数是否是素数", "conv": CONV_CODING},
    {"id": 32, "name": "写测试用例", "msg": "给上面的素数函数写几个测试用例", "conv": CONV_CODING},
    {"id": 33, "name": "代码解释", "msg": "解释一下 Python 的 GIL 是什么", "conv": None},
    {"id": 34, "name": "正则表达式", "msg": "写一个正则表达式匹配中国手机号", "conv": None},
    {"id": 35, "name": "SQL查询", "msg": "写一个 SQL 查询，找出订单金额最高的前10个客户", "conv": None},

    # ══════ Group 7: Shell 和文件操作 ══════
    {"id": 36, "name": "Shell-列目录", "msg": "列出当前目录下的文件夹", "conv": None,
     "tool": "list_directory"},
    {"id": 37, "name": "文件-创建", "msg": "在 data/temp 下创建 e2e_v3_hello.txt，写入 Hello E2E v3", "conv": None,
     "tool": "write_file"},
    {"id": 38, "name": "文件-读取", "msg": "读取 data/temp/e2e_v3_hello.txt 的内容", "conv": None,
     "tool": "read_file"},
    {"id": 39, "name": "Shell-系统信息", "msg": "查一下当前系统的 Python 版本", "conv": None},

    # ══════ Group 8: 浏览器任务 ══════
    {"id": 40, "name": "浏览器-打开Bing", "msg": "用浏览器打开 https://www.bing.com", "conv": None,
     "tool": "browser"},
    {"id": 41, "name": "浏览器-搜索", "msg": "在bing上搜索 OpenAI GPT-5 然后截图", "conv": None,
     "tool": "browser"},

    # ══════ Group 9: 项目讨论（同一会话） ══════
    {"id": 42, "name": "项目-需求分析", "msg": "帮我分析一下做一个在线教育平台需要哪些核心功能", "conv": CONV_PROJECT},
    {"id": 43, "name": "项目-技术选型", "msg": "这个教育平台用什么技术栈比较合适？", "conv": CONV_PROJECT},
    {"id": 44, "name": "项目-数据库设计", "msg": "课程和学生的数据库表结构大概怎么设计？", "conv": CONV_PROJECT},

    # ══════ Group 10: 知识问答（不应搜记忆） ══════
    {"id": 45, "name": "历史问题", "msg": "秦始皇统一六国是哪一年？", "conv": None,
     "no_mem_tool": True},
    {"id": 46, "name": "地理问题", "msg": "世界上最深的海沟叫什么？", "conv": None,
     "no_mem_tool": True},
    {"id": 47, "name": "科学概念", "msg": "量子纠缠是什么意思？简单解释", "conv": None,
     "no_mem_tool": True},

    # ══════ Group 11: 记忆隔离测试 ══════
    {"id": 48, "name": "会话A-设定", "msg": "在这个对话里，我要讨论的主题是机器学习", "conv": f"conv_iso_a_{uuid.uuid4().hex[:6]}"},
    {"id": 49, "name": "会话B-设定", "msg": "在这个对话里，我要讨论的主题是烘焙蛋糕", "conv": f"conv_iso_b_{uuid.uuid4().hex[:6]}"},
    {"id": 50, "name": "会话C-验证", "msg": "我们刚才在聊什么话题？", "conv": None,
     "desc": "新会话不应知道A或B的话题"},

    # ══════ Group 12: 复合任务 ══════
    {"id": 51, "name": "多步骤-时间写文件", "msg": "做两件事：1. 获取当前时间 2. 把时间写入 data/temp/v3_time.txt", "conv": None},
    {"id": 52, "name": "多步骤-搜索总结", "msg": "搜索一下最新的 AI Agent 技术趋势，简要总结", "conv": None},

    # ══════ Group 13: 边界情况 ══════
    {"id": 53, "name": "超短消息", "msg": "嗯", "conv": None},
    {"id": 54, "name": "纯数字", "msg": "42", "conv": None},
    {"id": 55, "name": "纯emoji", "msg": "👍", "conv": None},
    {"id": 56, "name": "长消息", "msg": "请帮我写一篇关于人工智能在医疗健康领域应用的文章大纲，要求包含以下方面：诊断辅助、药物研发、个性化治疗方案、医学影像分析、手术机器人、患者数据管理、远程医疗、AI伦理问题。每个方面都要有2-3个要点。", "conv": None},

    # ══════ Group 14: 历史回顾与记忆系统自检 ══════
    {"id": 57, "name": "回顾任务", "msg": "总结一下我今天让你做了哪些事？", "conv": None,
     "desc": "验证历史回顾能力"},
    {"id": 58, "name": "回顾规则", "msg": "你记得我设置过什么规则吗？", "conv": None},
    {"id": 59, "name": "回顾偏好", "msg": "你知道我的编程语言偏好吗？", "conv": None},

    # ══════ Group 15: 最终综合验证 ══════
    {"id": 60, "name": "综合验证", "msg": "快速回答：我叫什么、住哪里、生日是哪天、在做什么项目？", "conv": None,
     "desc": "一次性验证多个记忆点"},
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

    if test.get("no_mem_tool"):
        mem_tools = [t for t in tools if t in ("search_memory", "search_conversation_traces")]
        if mem_tools:
            v["issues"].append(f"Unnecessary memory tools: {mem_tools}")

    if test.get("tool"):
        if not any(test["tool"] in t for t in tools):
            v["issues"].append(f"Expected tool '{test['tool']}' not used")

    if test.get("kw"):
        combined = (text + result["thinking"]).lower()
        for kw in test["kw"]:
            if kw.lower() not in combined:
                v["issues"].append(f"Keyword '{kw}' missing")
                v["pass"] = False

    return v


def check_sqlite() -> list[str]:
    issues = []
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    lines = ["\n" + "=" * 70, "  SQLite 数据验证", "=" * 70]

    # 1. persona_trait 去重
    lines.append("\n[Check 1] persona_trait 同 dimension 去重")
    cur.execute("SELECT content FROM memories WHERE type='persona_trait'")
    traits = [r["content"] for r in cur.fetchall()]
    dims = {}
    for t in traits:
        dim = t.split("=")[0].strip() if "=" in t else t[:20]
        dims.setdefault(dim, []).append(t)
    dup_dims = {k: v for k, v in dims.items() if len(v) > 1}
    if dup_dims:
        issues.append(f"persona_trait 重复: {dup_dims}")
        lines.append(f"  FAIL: {len(dup_dims)} 个 dimension 有重复")
        for d, vals in dup_dims.items():
            lines.append(f"    {d}: {vals}")
    else:
        lines.append(f"  PASS: {len(dims)} 个 dimension，无重复")

    # 2. 垃圾 skill
    lines.append("\n[Check 2] 垃圾 skill 检查")
    cur.execute("""
        SELECT COUNT(*) FROM memories
        WHERE type='skill' AND (content LIKE '成功完成:%' OR content LIKE '任务 ''%使用工具组合%')
    """)
    garbage = cur.fetchone()[0]
    if garbage > 0:
        issues.append(f"{garbage} 条垃圾 skill")
        lines.append(f"  FAIL: {garbage} 条")
    else:
        lines.append("  PASS: 0 条")

    # 3. conversation_turns
    lines.append("\n[Check 3] conversation_turns 记录")
    cur.execute("SELECT COUNT(DISTINCT session_id) as cnt FROM conversation_turns")
    sessions = cur.fetchone()["cnt"]
    cur.execute("SELECT COUNT(*) FROM conversation_turns")
    turns = cur.fetchone()[0]
    lines.append(f"  {sessions} 个 session, {turns} 条 turn")
    # 检查最近 10 分钟内是否有新 turn
    cur.execute("""
        SELECT COUNT(*) FROM conversation_turns
        WHERE timestamp > datetime('now', '-30 minutes')
    """)
    recent = cur.fetchone()[0]
    if recent == 0:
        issues.append("最近15分钟无 conversation_turns 写入")
        lines.append("  WARN: 最近15分钟无新写入")
    else:
        lines.append(f"  PASS: 最近15分钟有 {recent} 条新写入")

    # 4. fact 去重
    lines.append("\n[Check 4] fact 去重")
    cur.execute("SELECT content FROM memories WHERE type='fact' ORDER BY created_at DESC")
    facts = [r["content"] for r in cur.fetchall()]
    seen = []
    dup_facts = []
    for f in facts:
        f_lower = f.strip().lower()
        for s in seen:
            if f_lower in s or s in f_lower:
                dup_facts.append(f)
                break
        else:
            seen.append(f_lower)
    if dup_facts:
        issues.append(f"{len(dup_facts)} 条重复 fact")
        lines.append(f"  WARN: {len(dup_facts)} 条疑似重复")
        for df in dup_facts[:5]:
            lines.append(f"    - {df[:80]}")
    else:
        lines.append(f"  PASS: {len(facts)} 条 fact，无重复")

    # 5. 记忆分布
    lines.append("\n[Check 5] 记忆类型分布")
    cur.execute("SELECT type, COUNT(*) as cnt FROM memories GROUP BY type ORDER BY cnt DESC")
    for r in cur.fetchall():
        lines.append(f"  {r['type']:15s}: {r['cnt']}")

    # 6. extraction_queue
    lines.append("\n[Check 6] extraction_queue")
    cur.execute("SELECT status, COUNT(*) FROM extraction_queue GROUP BY status")
    rows = cur.fetchall()
    if rows:
        for r in rows:
            lines.append(f"  {r[0]}: {r[1]}")
    else:
        lines.append("  空")

    conn.close()

    lines.append("\n" + "-" * 50)
    if issues:
        lines.append(f"  SQLite 验证: {len(issues)} 个问题")
        for i in issues:
            lines.append(f"  - {i}")
    else:
        lines.append("  SQLite 验证: 全部通过")

    return issues, lines


def main():
    REPORT_TXT.parent.mkdir(parents=True, exist_ok=True)
    out = open(REPORT_TXT, "w", encoding="utf-8")

    def log(s=""):
        print(s)
        out.write(s + "\n")
        out.flush()

    log("=" * 70)
    log(f"  记忆系统 E2E 测试 v3 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"  共 {len(TESTS)} 个案例 | 间隔 {DELAY}s")
    log("=" * 70)
    log()

    try:
        check = urllib.request.urlopen(f"{API_BASE}/api/sessions?channel=desktop", timeout=5)
        if check.getcode() != 200:
            log("ERROR: Backend not reachable")
            sys.exit(1)
    except Exception as e:
        log(f"ERROR: Cannot connect: {e}")
        sys.exit(1)

    results = []

    for i, test in enumerate(TESTS):
        conv_id = test.get("conv")

        log(f"[{test['id']:02d}/{len(TESTS)}] {test['name']}")
        log(f"  消息: {test['msg'][:70]}{'...' if len(test['msg']) > 70 else ''}")
        if test.get("desc"):
            log(f"  目标: {test['desc']}")

        start = time.time()
        result = send_chat(test["msg"], conv_id)
        elapsed = time.time() - start

        verdict = evaluate(test, result)
        tools = result["tools_called"]
        status = "PASS" if verdict["pass"] else "FAIL"
        warn = " (WARN)" if verdict["issues"] and verdict["pass"] else ""

        log(f"  [{status}{warn}] {elapsed:.1f}s | {result['iterations']} iters | tools: {tools[:6]}")
        if result["full_text"]:
            reply_preview = result["full_text"][:150].replace("\n", " ")
            log(f"  回复: {reply_preview}...")
        for issue in verdict["issues"]:
            log(f"  ! {issue}")
        log()

        results.append({
            "id": test["id"], "name": test["name"],
            "msg": test["msg"][:100],
            "elapsed": round(elapsed, 2),
            "verdict": status,
            "issues": verdict["issues"],
            "tools": tools,
            "reply_preview": result["full_text"][:200],
            "usage": result["usage"],
            "conv_id": conv_id,
        })

        if i < len(TESTS) - 1:
            time.sleep(DELAY)

    # ── Summary ──
    passed = sum(1 for r in results if r["verdict"] == "PASS")
    failed = sum(1 for r in results if r["verdict"] == "FAIL")
    warned = sum(1 for r in results if r["verdict"] == "PASS" and r["issues"])
    total_tokens = sum(r["usage"].get("total_tokens", 0) for r in results)
    total_time = sum(r["elapsed"] for r in results)

    log("=" * 70)
    log(f"  总计: {len(results)} | PASS: {passed} | FAIL: {failed} | WARN: {warned}")
    log(f"  耗时: {total_time:.1f}s | Tokens: {total_tokens:,}")
    log("=" * 70)

    if failed:
        log("\n  FAILED TESTS:")
        for r in results:
            if r["verdict"] == "FAIL":
                log(f"    [{r['id']:02d}] {r['name']}: {'; '.join(r['issues'])}")

    if warned:
        log("\n  WARNED TESTS:")
        for r in results:
            if r["verdict"] == "PASS" and r["issues"]:
                log(f"    [{r['id']:02d}] {r['name']}: {'; '.join(r['issues'])}")

    # ── SQLite 验证 ──
    sqlite_issues, sqlite_lines = check_sqlite()
    for line in sqlite_lines:
        log(line)

    # Save JSON report
    with open(REPORT_JSON, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "summary": {"total": len(results), "passed": passed, "failed": failed, "warned": warned},
            "total_tokens": total_tokens, "total_time": round(total_time, 2),
            "results": results, "sqlite_issues": sqlite_issues,
        }, f, ensure_ascii=False, indent=2)

    log(f"\n  报告: {REPORT_TXT}")
    log(f"  JSON: {REPORT_JSON}")
    out.close()


if __name__ == "__main__":
    main()

