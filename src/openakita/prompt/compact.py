"""
会话压缩提示词模板

参考 Claude Code 的结构化压缩机制，适配 OpenAkita 通用 Agent 场景。
提供 NO_TOOLS 保护、analysis+summary 两阶段格式、9 段结构化模板。
"""

from __future__ import annotations

import re

NO_TOOLS_PREAMBLE = """\
重要：仅用纯文本回复。不要调用任何工具。
你已经拥有所需的全部上下文。工具调用会被拒绝。
你的回复必须是一个 <analysis> 块加一个 <summary> 块。
"""

NO_TOOLS_TRAILER = (
    "\n\n再次提醒：不要调用任何工具。仅用纯文本回复 — "
    "一个 <analysis> 块加一个 <summary> 块。工具调用会被拒绝。"
)

ANALYSIS_INSTRUCTION = """\
在提供最终摘要之前，先用 <analysis> 标签整理你的思路，确保覆盖所有要点：
1. 按时间顺序分析对话中的每个阶段，识别：
   - 用户的明确请求和意图
   - 你的处理方法和关键决策
   - 遇到的错误和修复方案
   - 用户给出的反馈（特别是要求你改变做法的反馈）
2. 检查技术准确性和完整性。"""

BASE_COMPACT_PROMPT = f"""\
你的任务是为到目前为止的对话创建详细摘要。
这个摘要将成为后续对话的唯一上下文，必须保留所有关键信息。

{ANALYSIS_INSTRUCTION}

摘要必须包含以下部分（用 <summary> 标签包裹）：

1. 用户请求与意图：详细记录用户的所有明确请求
2. 关键知识和概念：讨论中涉及的重要概念和信息
3. 涉及的资源和操作：文件、API、工具调用等，保留具体路径/名称/参数
4. 遇到的问题和解决方案：每个错误的原因和修复方法
5. 解决过程：已解决的问题和进行中的排查
6. 所有用户消息：列出所有非工具结果的用户消息原文（关键！用户反馈不可丢失）
7. 待处理任务：明确被要求但尚未完成的任务
8. 当前工作：压缩前正在做什么，包含具体信息
9. 可选的下一步：仅当与用户最近请求直接相关时列出

重要规则：
- 保留所有具体数值（端口、路径、密钥、版本号等），不用模糊描述
- 用户设定的行为规则（"不要X"、"必须先Y"等）必须原文保留
- 成功的方案必须详细保留，失败的尝试可简化为一句话
"""

PARTIAL_COMPACT_PROMPT = """\
你的任务是为对话的最近部分创建摘要。
只总结最近消息，早期已保留的消息不需要再总结。
"""

PARTIAL_COMPACT_UP_TO_PROMPT = """\
你的任务是为这段对话创建摘要。
这个摘要将放在继续会话的开头，后面还有更新的消息。
第 8 节应为"已完成的工作"，第 9 节应为"继续工作所需的上下文"。
"""


def get_compact_prompt(custom_instructions: str | None = None) -> str:
    """组装完整压缩提示词。"""
    prompt = NO_TOOLS_PREAMBLE + BASE_COMPACT_PROMPT
    if custom_instructions and custom_instructions.strip():
        prompt += f"\n\n额外指导：\n{custom_instructions}"
    prompt += NO_TOOLS_TRAILER
    return prompt


def get_partial_compact_prompt(
    custom_instructions: str | None = None,
    direction: str = "from",
) -> str:
    """组装部分压缩提示词。"""
    template = PARTIAL_COMPACT_UP_TO_PROMPT if direction == "up_to" else PARTIAL_COMPACT_PROMPT
    prompt = NO_TOOLS_PREAMBLE + template
    if custom_instructions and custom_instructions.strip():
        prompt += f"\n\n额外指导：\n{custom_instructions}"
    prompt += NO_TOOLS_TRAILER
    return prompt


def format_compact_summary(summary: str) -> str:
    """剥离 <analysis> 草稿，保留 <summary> 正文。"""
    formatted = re.sub(r"<analysis>[\s\S]*?</analysis>", "", summary)
    match = re.search(r"<summary>([\s\S]*?)</summary>", formatted)
    if match:
        content = match.group(1).strip()
        formatted = re.sub(r"<summary>[\s\S]*?</summary>", f"摘要:\n{content}", formatted)
    formatted = re.sub(r"\n\n+", "\n\n", formatted)
    return formatted.strip()


def get_compact_user_message(
    summary: str,
    suppress_followup: bool = False,
    recent_preserved: bool = False,
) -> str:
    """将压缩摘要包装为用户消息。"""
    formatted = format_compact_summary(summary)
    msg = f"本次会话续接自之前的对话。以下摘要覆盖了早期部分。\n\n{formatted}"
    if recent_preserved:
        msg += "\n\n最近的消息已原样保留。"
    if suppress_followup:
        msg += (
            "\n\n请直接从中断处继续，不要询问用户。"
            "不要确认摘要、不要回顾之前的工作、不要加\"我继续\"之类的前缀。"
        )
    return msg
