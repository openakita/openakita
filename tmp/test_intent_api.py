"""Direct API test: qwen3.5-plus intent analysis with old vs new prompt."""

import os
import json
import httpx
from dotenv import load_dotenv

load_dotenv(".env")

API_KEY = os.environ["DASHSCOPE_API_KEY"]
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL = "qwen3.5-plus"

OLD_SYSTEM = """【角色】
你是 Intent Analyzer，负责分析用户消息的意图并生成结构化任务定义。

【输入】
用户的原始请求。

【目标】
1. 判断用户意图类型
2. 将请求转化为结构化任务定义
3. 推荐可能需要的工具分类
4. 提取记忆检索关键词

【输出结构】
请用以下 YAML 格式输出：

```yaml
intent: [意图类型: chat/query/task/follow_up/command]
task_type: [任务类型: question/action/creation/analysis/reminder/compound/other]
goal: [一句话描述任务目标]
inputs:
  given: [已提供的信息列表]
  missing: [缺失但可能需要的信息列表，如果没有则为空]
constraints: [约束条件列表，如果没有则为空]
output_requirements: [输出要求列表]
risks_or_ambiguities: [风险或歧义点列表，如果没有则为空]
tool_hints: [可能需要的工具分类列表，从以下选择: File System, Browser, Web Search, IM Channel, Scheduled, Desktop, Agent, Agent Hub, Agent Package, Organization, Profile, Persona, Config。注意：System/Memory/Plan/Skills/Skill Store/MCP 类工具始终可用，无需列出。空列表表示仅使用始终可用的基础工具]
memory_keywords: [用于检索历史记忆的关键词列表。空列表表示不需要检索记忆]
```

【意图类型说明】
- chat: 闲聊、寒暄、感谢、告别、简短确认（如"好的""收到""你好"）
- query: 信息查询，可能不需要工具就能回答（如"Python的GIL是什么"）
- task: 需要通过工具执行的任务（如"帮我写个脚本""搜索一下""创建文件"）
- follow_up: 对上一轮结果的追问或修改要求（如"改成UTF-8编码""再加一个功能"）
- command: 系统指令（以 / 开头的命令，如 /stop /plan）

【规则】
- 不要解决任务，不要给建议，只输出 YAML
- 极短消息（如"嗯""好""谢谢"）→ intent: chat
- 涉及"之前""上次""我说过"的消息 → memory_keywords 应包含相关主题词
- task_type: compound 表示多步骤任务，需要制定计划
- 保持简洁，每项不超过一句话

【示例1 — 闲聊】
用户: "你好呀"

```yaml
intent: chat
task_type: other
goal: 用户打招呼
inputs:
  given: [问候]
  missing: []
constraints: []
output_requirements: [友好回应]
risks_or_ambiguities: []
tool_hints: []
memory_keywords: []
```

【示例2 — 任务】
用户: "帮我写一个Python脚本，读取CSV文件并统计每列的平均值"

```yaml
intent: task
task_type: creation
goal: 创建一个读取CSV文件并计算各列平均值的Python脚本
inputs:
  given:
    - 需要处理的文件格式是CSV
    - 需要统计的是平均值
    - 使用Python语言
  missing:
    - CSV文件的路径或示例
    - 是否需要处理非数值列
constraints: []
output_requirements:
  - 可执行的Python脚本
  - 能够读取CSV文件
  - 输出每列的平均值
risks_or_ambiguities:
  - 未指定如何处理包含非数值数据的列
tool_hints: [File System]
memory_keywords: [CSV, Python脚本, 数据统计]
```"""

NEW_SYSTEM = """\
你是 Intent Analyzer。根据用户消息判断意图，只输出 YAML，不要解释。

意图类型：
- task: 需要执行操作（写文件、搜索、查看目录、创建、发送消息、运行命令等）
- query: 知识问答，不需要工具就能回答
- chat: 纯闲聊、寒暄、感谢、告别
- follow_up: 追问或修改上一轮结果
- command: 以 / 开头的系统指令

task_type 可选值: question/action/creation/analysis/reminder/compound/other

tool_hints 可选值: File System, Browser, Web Search, IM Channel, Desktop, Agent, Organization, Config（空列表=仅基础工具）

输出格式（严格遵循，不要添加多余字段）：
```yaml
intent: <类型>
task_type: <类型>
goal: <一句话描述>
tool_hints: [<工具分类>]
memory_keywords: [<记忆关键词>]
```

示例：
用户: "帮我查看项目里有哪些文件" → intent: task, task_type: action, goal: 列出项目文件, tool_hints: [File System]
用户: "搜索一下最新的AI新闻" → intent: task, task_type: action, goal: 搜索AI新闻, tool_hints: [Web Search]
用户: "Python的GIL是什么" → intent: query, task_type: question, goal: 解释Python GIL机制, tool_hints: []
用户: "你好" → intent: chat, task_type: other, goal: 用户打招呼, tool_hints: []
用户: "改成UTF-8编码" → intent: follow_up, task_type: action, goal: 修改编码为UTF-8, tool_hints: [File System]

重要：你必须分析用户的实际消息内容来判断意图，不要复制上面的示例。"""


TEST_MESSAGES = [
    "帮我查看当前目录下有哪些Python文件",
    "1+1等于几",
    "搜索一下最新的AI新闻",
    "你好",
]


def call_api(system, user_msg, enable_thinking):
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        "max_tokens": 1024,
        "temperature": 0.3,
    }
    if not enable_thinking:
        body["extra_body"] = {"enable_thinking": False}
    resp = httpx.post(
        f"{BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json=body,
        timeout=60,
    )
    data = resp.json()
    if "choices" not in data:
        return f"ERROR: {json.dumps(data, ensure_ascii=False)[:500]}"
    choice = data["choices"][0]
    content = choice["message"].get("content", "")
    reasoning = choice["message"].get("reasoning_content", "")
    usage = data.get("usage", {})
    return {
        "content": content,
        "reasoning": reasoning[:300] if reasoning else "",
        "in_tokens": usage.get("prompt_tokens", "?"),
        "out_tokens": usage.get("completion_tokens", "?"),
    }


def run_test(label, system, enable_thinking):
    print("=" * 60)
    print(f"{label}  |  thinking={'ON' if enable_thinking else 'OFF'}")
    print("=" * 60)
    for msg in TEST_MESSAGES:
        print(f'\nINPUT: "{msg}"')
        r = call_api(system, msg, enable_thinking)
        if isinstance(r, str):
            print(r)
        else:
            print(f"  Tokens: in={r['in_tokens']}, out={r['out_tokens']}")
            if r["reasoning"]:
                print(f"  REASONING: {r['reasoning'][:200]}")
            print(f"  OUTPUT:\n{r['content']}")
        print("-" * 40)


# Test 1: Old prompt, thinking OFF (reproducing the bug)
run_test("TEST 1: OLD PROMPT", OLD_SYSTEM, enable_thinking=False)

# Test 2: Old prompt, thinking ON
run_test("TEST 2: OLD PROMPT", OLD_SYSTEM, enable_thinking=True)

# Test 3: New prompt, thinking OFF
run_test("TEST 3: NEW PROMPT", NEW_SYSTEM, enable_thinking=False)

# Test 4: New prompt, thinking ON
run_test("TEST 4: NEW PROMPT", NEW_SYSTEM, enable_thinking=True)
