"""
响应处理器

从 agent.py 提取的响应处理逻辑，负责:
- LLM 响应文本清理（思考标签、模拟工具调用）
- 任务完成度验证
- 任务复盘分析
- 辅助判断函数
"""

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# ==================== 内部 trace marker 常量 ====================
#
# OpenAkita 通过 `build_tool_trace_summary()` 注入到历史回放上下文里的内部
# 摘要 marker。用 `<<...>>` 这种自然中文几乎不会出现的强格式，是有意为之
# 的反模仿设计——见 ``agent.py::build_tool_trace_summary`` 的 docstring。
#
# 这些 marker 不应作为用户可见正文流式打印，也不应被持久化进 assistant
# content。集中放在这里是为了让流式 scrubber、最终文本清理、历史 → LLM
# 上下文 strip（`agent.py`）、历史 → UI strip（`sessions.py`）共用同一份
# 来源，避免日后任一处遗漏新 marker。
#
# 新增 marker 时只动这一处。

# 完整 marker 字面量，按出现频率排序（命中早终止用）。
INTERNAL_TRACE_MARKERS: tuple[str, ...] = (
    "<<TOOL_TRACE>>",
    "<<DELEGATION_TRACE>>",
    "[执行摘要]",
    "[子Agent工作总结]",
)

# 历史回放 / UI strip 用的"带 \n\n 前缀"版本：用于在 assistant content
# 中查找 marker section 起点。保持与旧 _STRIP_MARKERS 列表行为等价。
INTERNAL_TRACE_SECTION_PREFIXES: tuple[str, ...] = tuple(
    "\n\n" + m for m in INTERNAL_TRACE_MARKERS
)

# marker section 结束分隔符（下一段的起始符），用于 strip 时判断 trace
# section 的右边界。与现有 `_STRIP_MARKERS` 切断策略保持一致。
INTERNAL_TRACE_SECTION_TERMINATORS: tuple[str, ...] = (
    "\n\n[",
    "\n\n<<",
    "\n\n##",
    "\n\n---",
)

# C16 prompt-hardening wrappers（``wrap_external_content``）也只允许进入
# LLM 上下文，绝不能作为用户可见正文或 assistant 历史持久化出去。它带
# nonce/source，不能放进 ``INTERNAL_TRACE_MARKERS`` 这种固定字面量列表，
# 因此单独用 boundary-gated regex 处理。
EXTERNAL_CONTENT_BEGIN_PREFIX = "<<<EXTERNAL_CONTENT_BEGIN"
EXTERNAL_CONTENT_END_PREFIX = "<<<EXTERNAL_CONTENT_END"


# ==================== 文本清理函数 ====================


def strip_thinking_tags(text: str) -> str:
    """
    移除响应中的内部标签内容。

    需要清理的标签包括：
    - <thinking>...</thinking> - Claude extended thinking
    - <think>...</think> - MiniMax/Qwen thinking 格式
    - <minimax:tool_call>...</minimax:tool_call>
    - <<|tool_calls_section_begin|>>...<<|tool_calls_section_end|>> - Kimi K2
    - </thinking> - 残留的闭合标签
    """
    if not text:
        return text

    cleaned = text

    cleaned = re.sub(r"<thinking>.*?</thinking>\s*", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<think>.*?</think>\s*", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(
        r"<minimax:tool_call>.*?</minimax:tool_call>\s*",
        "",
        cleaned,
        flags=re.DOTALL | re.IGNORECASE,
    )
    cleaned = re.sub(
        r"<?minimax:tool_call>?\s+[a-zA-Z_][\w.]*(?::\d+)?\s*"
        r"<\|tool_call_argument_begin\|>.*?"
        r"(?:<\|tool_call_end\|>\s*)?"
        r"(?:<\|tool_calls_section_end\|>|</minimax:tool_call>|$)\s*",
        "",
        cleaned,
        flags=re.DOTALL | re.IGNORECASE,
    )
    cleaned = re.sub(
        r"<<\|tool_calls_section_begin\|>>.*?<<\|tool_calls_section_end\|>>\s*",
        "",
        cleaned,
        flags=re.DOTALL,
    )
    cleaned = re.sub(
        r"<invoke\s+[^>]*>.*?</invoke>\s*",
        "",
        cleaned,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # 移除残留的闭合标签
    cleaned = re.sub(r"</thinking>\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"</think>\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"</minimax:tool_call>\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<<\|tool_calls_section_begin\|>>.*$", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<\?xml[^>]*\?>\s*", "", cleaned)

    # 兜底：清理孤立的开标签（无闭合，从标签到字符串末尾）
    cleaned = re.sub(r"<thinking>\s*.*$", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<think>\s*.*$", "", cleaned, flags=re.DOTALL | re.IGNORECASE)

    return cleaned.strip()


def strip_tool_simulation_text(text: str) -> str:
    """
    移除 LLM 在文本中模拟工具调用的内容。

    当使用不支持原生工具调用的备用模型时，LLM 可能在文本中
    "模拟"工具调用。支持三种情况：
    1. 整行都是工具调用（直接移除）
    2. 行内嵌入的 .tool_name(args)（从行尾剥离，保留前面的正文）
    3. <tool_call>...</tool_call> XML 块（Ask 模式下 LLM 常泄漏此格式）
    """
    if not text:
        return text

    # 先移除 <tool_call>...</tool_call> 块（可能跨行）
    text = re.sub(
        r"<tool_call>\s*.*?\s*</tool_call>",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    ).strip()

    pattern1 = r"^\.?[a-z_]+\s*\(.*\)\s*$"
    pattern2 = r"^[a-z_]+:\d+[\{\(].*[\}\)]\s*$"
    pattern3 = r'^\{["\']?(tool|function|name)["\']?\s*:'
    pattern4 = r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$"

    # 行内 .tool_name(args) 剥离：匹配行尾的 .tool_name(args) 部分
    inline_dot_pattern = re.compile(r"\s*\.[a-z][a-z0-9_]{2,}\s*\(.*\)\s*$", re.IGNORECASE)

    lines = text.split("\n")
    cleaned_lines = []
    in_code_block = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("```"):
            in_code_block = not in_code_block
            cleaned_lines.append(line)
            continue

        if in_code_block:
            cleaned_lines.append(line)
            continue

        is_tool_sim = (
            re.match(pattern1, stripped, re.IGNORECASE)
            or re.match(pattern2, stripped, re.IGNORECASE)
            or re.match(pattern3, stripped, re.IGNORECASE)
            or re.match(pattern4, stripped)
        )
        if is_tool_sim:
            continue

        # 检查行尾是否嵌入了 .tool_name(args)（如混合文本+工具调用）
        m = inline_dot_pattern.search(stripped)
        if m and m.start() > 0:
            cleaned_lines.append(stripped[: m.start()].rstrip())
        else:
            cleaned_lines.append(line)

    return "\n".join(cleaned_lines).strip()


_LEADING_TIMESTAMP_RE = re.compile(r"^\s*\[\d{1,2}:\d{2}\]\s*")


# 完整字符串内部 trace marker 清理用的正则。
# 匹配："消息开头" 或 "段落边界（一个或多个 \n + 可选空白）" 之后的
# marker，一直到字符串末尾或下一段起始符（``\n\n[`` / ``\n\n<<`` /
# ``\n\n##`` / ``\n\n---``，对齐 ``INTERNAL_TRACE_SECTION_TERMINATORS``）。
#
# boundary 用 ``\n+[ \t]*`` 而非 ``\n[ \t]*``：原文中 marker 通常以
# ``\n\n`` 段落分隔形式出现，必须把全部 leading newlines 一起消耗掉，
# 否则 ``.sub("", ...)`` 后会残留单个 ``\n`` 与后续段落粘连成 ``\n\n\n``。
_INTERNAL_TRACE_SECTION_RE = re.compile(
    r"(?:\A|\n+[ \t]*)"
    r"(?:" + "|".join(re.escape(m) for m in INTERNAL_TRACE_MARKERS) + r")"
    r".*?"
    r"(?=\Z|"
    + "|".join(re.escape(t) for t in INTERNAL_TRACE_SECTION_TERMINATORS)
    + r")",
    re.DOTALL,
)

_EXTERNAL_CONTENT_SECTION_RE = re.compile(
    r"(?:\A|\n+[ \t]*)"
    r"<<<EXTERNAL_CONTENT_BEGIN\b[^>]*>>>"
    r".*?"
    r"(?:<<<EXTERNAL_CONTENT_END\b[^>]*>>>|(?=\Z|"
    + "|".join(re.escape(t) for t in INTERNAL_TRACE_SECTION_TERMINATORS)
    + r"))",
    re.DOTALL,
)

# fenced code block 检测：匹配整段 ``` ... ``` 以便在清理时跳过。
_FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)


def strip_internal_trace_markers(text: str) -> str:
    """
    从完整文本中剥离内部 trace marker section。

    覆盖两种位置：

    1. 消息开头即为 trace（整段都是 ``<<TOOL_TRACE>>`` 摘要）→ 返回空串。
    2. 正文后拼接 ``\\n\\n<<TOOL_TRACE>>...`` → 保留正文，剥离 marker 起到
       下一段起始符（或字符串末尾）之间的内容。

    安全约束：

    - 只在消息开头 / 行首 / 段落边界识别 marker（流式 scrubber 同口径），
      避免误删用户讨论 ``<<TOOL_TRACE>>`` 字面量的合法文本。
    - 在 fenced code block (``` ... ```) 内出现的 marker 一律保留，不剥离
      （便于排障 / 文档示例 / 用户讨论格式时贴出 marker）。
    - 末尾 ``rstrip``，去除 marker 之前 ``\\n\\n`` 留下的尾部空白。

    新增 marker 字面量请改 ``INTERNAL_TRACE_MARKERS`` 常量，不要再在此处
    硬编码。
    """
    if not text:
        return text

    # 没有任何 marker 字面量出现 → 快路径返回原文。
    if not any(m in text for m in INTERNAL_TRACE_MARKERS) and (
        EXTERNAL_CONTENT_BEGIN_PREFIX not in text
    ):
        return text

    # 用占位符保护 fenced code block，避免代码示例里的 marker 被误删。
    placeholders: list[str] = []

    def _stash(match: re.Match) -> str:
        placeholders.append(match.group(0))
        return f"\x00FENCED_CODE_{len(placeholders) - 1}\x00"

    masked = _FENCED_CODE_RE.sub(_stash, text)
    cleaned = _INTERNAL_TRACE_SECTION_RE.sub("", masked)
    cleaned = _EXTERNAL_CONTENT_SECTION_RE.sub("", cleaned)

    # 还原 fenced code block。
    for i, original in enumerate(placeholders):
        cleaned = cleaned.replace(f"\x00FENCED_CODE_{i}\x00", original)

    return cleaned.rstrip()


def clean_llm_response(text: str) -> str:
    """
    清理 LLM 响应文本。

    依次应用:
    1. strip_thinking_tags - 移除思考标签
    2. strip_internal_trace_markers - 移除内部 trace marker section
    3. strip_tool_simulation_text - 移除模拟工具调用
    4. strip_intent_tag - 移除意图声明标记
    5. strip leading [HH:MM] timestamp leaked from historical message formatting

    清理顺序约束（**关键安全不变量**）：
    ``strip_thinking_tags`` → ``strip_internal_trace_markers`` →
    ``strip_tool_simulation_text``。若先做工具模拟剥离，模型整段模仿的
    ``<<TOOL_TRACE>>\\n- web_search({...})`` 中的工具调用会被误当成真实
    意图保留下来，反向污染下游解析（``parse_text_tool_calls`` 可能将其
    转成真实工具调用并触发执行）。
    """
    if not text:
        return text

    cleaned = strip_thinking_tags(text)
    cleaned = strip_internal_trace_markers(cleaned)
    cleaned = strip_tool_simulation_text(cleaned)
    _, cleaned = parse_intent_tag(cleaned)
    cleaned = _LEADING_TIMESTAMP_RE.sub("", cleaned)

    return cleaned.strip()


# ==================== 意图声明解析 ====================

_INTENT_TAG_RE = re.compile(r"^\s*\[(ACTION|REPLY)\]\s*\n?", re.IGNORECASE)


def parse_intent_tag(text: str) -> tuple[str | None, str]:
    """
    解析并剥离响应文本开头的意图声明标记。

    模型在纯文本回复时应在第一行声明 [ACTION] 或 [REPLY]：
    - [ACTION]: 声明需要调用工具（若实际未调用则为幻觉）
    - [REPLY]: 声明纯对话回复，不需要工具

    Returns:
        (intent, stripped_text):
        - intent: "ACTION" / "REPLY" / None（无标记）
        - stripped_text: 移除标记后的文本
    """
    if not text:
        return None, text or ""
    m = _INTENT_TAG_RE.match(text)
    if m:
        return m.group(1).upper(), text[m.end() :]
    return None, text


# ==================== 文件交付意图识别（模块级 helper） ====================
#
# 该函数被 ResponseHandler / OrgRuntime / ReasoningEngine 共用：
# 1. ResponseHandler.verify_task_completion 用它决定是否对纯文本最终答复
#    施加"必须有附件证据"的硬校验；
# 2. OrgRuntime._run_node_task 用它决定是否触发 auto-persist 兜底落盘，
#    以及是否要静默 verify_incomplete 诊断卡片；
# 3. ReasoningEngine._handle_final_answer 用它选择 retry prompt 文案
#    （expects=True → 强约束指令；expects=False → 温和提示）。
#
# 设计要点：
# - 仅看「用户原始请求」的文本特征，**不依赖** reasoning_engine 的 stale
#   实例属性（verify 没执行的轮次该属性可能不可信）。
# - 系统/组织合成的「被动通知」前缀（如「[收到任务交付]」「[用户指令最终汇总]」
#   「[系统]」等共 17 种）必须先剔除，避免命中其中包含的「文件/附件/写一份」
#   等字面值导致误判 → 进而让 root 节点 emit task_failed。
#   注：「[收到任务]」不在豁免名单——它是子节点真正接到的工作派单，
#   仍需走正常的 expects_artifact 判定。
# - 关键词偏向「交付意图」而非泛指「话题里出现了文件」。

_SYSTEM_REQUEST_PREFIXES_TUPLE: tuple[str, ...] = (
    # reasoning_engine / agent 自注入的元指令、汇总、上下文头
    "[用户指令最终汇总]",
    "[系统]",
    "[系统提示]",
    "[组织]",
    "[以上是之前的对话历史",
    # OrgRuntime._format_incoming_message 中 13 种 type_label，
    # 其中只有 "[收到任务]" 是「子节点真正接到工作派单」，必须保留 verify；
    # 其余 12 种均为被动收到的通知/反馈，root 或上游收到时只需文字汇总
    # 即可，不应被强制要求附件交付，否则会被 _request_expects_artifact 误
    # 判命中正文里出现的「文件 / 链接 / 写一份 / openakita-promotion-plan.md」
    # 等关键字 → INCOMPLETE → root emit task_failed → 用户看到「任务验证未通过」
    # 噪音卡片（详见 2026-04-28 13:42:53 _134209 失败链）。
    "[收到任务结果]",
    "[收到任务交付]",
    "[任务已通过验收]",
    "[任务被打回]",
    "[收到汇报]",
    "[收到提问]",
    "[收到回答]",
    "[收到上报]",
    "[收到组织公告]",
    "[收到部门公告]",
    "[收到反馈]",
    "[收到握手请求]",
    "[收到消息]",
)

_ARTIFACT_INTENT_KEYWORDS: tuple[str, ...] = (
    "图片",
    "照片",
    "图像",
    "海报",
    "壁纸",
    "配图",
    "截图",
    "附件",
    "文件",
    "下载",
    "发我",
    "发给我",
    "给我一张",
    "给我发",
    "导出",
    "另存",
    "保存为",
    "生成一份",
    "出一份",
    "做一份",
    "写一份",
    # 强信号：带量词的"整理/准备/拟一份"——用户场景"帮我整理一份OpenAkita
    # 的展会宣传策划"必然命中"整理一份"，触发 expects_artifact=True，
    # 避免 trust-but-verify 走 LLM 复核绕远路。
    "整理一份",
    "整理一个",
    "准备一份",
    "准备一个",
    "拟一份",
    "拟一个",
    "起草一份",
    "起草一个",
    "image",
    "photo",
    "picture",
    "file",
    "attachment",
    "download",
    "send me",
    "export",
)

# 弱信号关键词：单独出现时不足以判 expects_artifact=True（因为正常对话里
# "你的方案/这个计划/这个报告"的引用很常见），但**当上下文已经产出过文件**
# （sub-agent 的 output_files 非空 / 当前 ReAct 已 write_file 过）时就视为
# 用户期望最终回复挂上附件——典型场景是用户说"整理一份…策划"，子节点
# 已经写好 markdown 但 coordinator 没用 deliver_artifacts 转发。
_SOFT_ARTIFACT_KEYWORDS: tuple[str, ...] = (
    "方案",
    "策划",
    "策略",
    "计划",
    "报告",
    "汇总",
    "总结",
    "文档",
    "ppt",
    "幻灯片",
    "脚本",
    "白皮书",
    "提案",
    "plan",
    "report",
    "proposal",
    "document",
    "summary",
)

_INPUT_ARTIFACT_REFERENCE_PATTERNS: tuple[str, ...] = (
    r"\bread\s+(?:the\s+)?attached\b",
    r"\bopen\s+(?:the\s+)?attached\b",
    r"\banaly[sz]e\s+(?:the\s+)?attached\b",
    r"\bsummar(?:ize|ise|y)\s+(?:the\s+)?attached\b",
    r"\bextract\b.+\b(?:attached|file|attachment)\b",
    r"\b(?:attached|uploaded)\s+(?:file|image|photo|picture|document|attachment)\b",
    r"读取(?:我)?(?:刚)?(?:上传|发送|发来)?的?(?:附件|文件|图片)",
    r"阅读(?:我)?(?:刚)?(?:上传|发送|发来)?的?(?:附件|文件|图片)",
    r"分析(?:我)?(?:刚)?(?:上传|发送|发来)?的?(?:附件|文件|图片)",
    r"查看(?:我)?(?:刚)?(?:上传|发送|发来)?的?(?:附件|文件|图片)",
    r"总结(?:我)?(?:刚)?(?:上传|发送|发来)?的?(?:附件|文件|图片)",
    r"(?:附件|文件|图片)(?:里|中|内容)",
)

_ARTIFACT_DELIVERY_INTENT_KEYWORDS: tuple[str, ...] = (
    "下载",
    "发我",
    "发给我",
    "给我一张",
    "给我发",
    "导出",
    "另存",
    "保存为",
    "生成一份",
    "出一份",
    "做一份",
    "写一份",
    "生成文件",
    "创建文件",
    "输出文件",
    "生成图片",
    "生成图像",
    "生成海报",
    "download",
    "send me",
    "export",
    "attach the file",
    "attach a file",
    "create a file",
    "write a file",
    "generate a file",
    "save as",
    "generate an image",
    "create an image",
    "make an image",
)


def _looks_like_input_artifact_reference(text: str) -> bool:
    return any(
        re.search(pattern, text, flags=re.IGNORECASE)
        for pattern in _INPUT_ARTIFACT_REFERENCE_PATTERNS
    )


def _has_artifact_delivery_intent(text: str) -> bool:
    return any(key in text for key in _ARTIFACT_DELIVERY_INTENT_KEYWORDS)


def request_expects_artifact(
    user_request: str | None,
    has_produced_files: bool = False,
) -> bool:
    """判断用户原始请求是否明显要求附件/文件类交付物。

    返回 True 表示用户文本里出现了"交付物"信号，调用方据此决定是否
    强约束 LLM 走 ``write_file`` / ``org_submit_deliverable(file_attachments=...)``
    路径。返回 False 时调用方应避免向用户喷发"必须交付文件"的诊断噪音。

    Args:
        user_request: 用户原始请求文本。
        has_produced_files: 上下文中是否已经产出过文件（如 sub-agent 已 write_file、
            或当前会话有 delivery_receipts）。默认 False。
            为 True 时，弱信号词（"方案/策划/计划/报告/汇总/文档"等）也会触发
            expects_artifact=True；典型场景是 coordinator 收到 sub-agent 已经
            写好的 markdown 文件但还没用 deliver_artifacts 转发——这时把弱信号
            升级成强信号，让 trust-but-verify 直接走"必须 deliver_artifacts"
            分支，避免兜到 LLM 再绕一圈。
    """
    raw = (user_request or "").strip()
    if not raw:
        return False
    if raw.startswith(_SYSTEM_REQUEST_PREFIXES_TUPLE):
        return False
    text = raw.lower()
    if _looks_like_input_artifact_reference(text) and not _has_artifact_delivery_intent(text):
        return False
    if any(key in text for key in _ARTIFACT_INTENT_KEYWORDS):
        return True
    if has_produced_files and any(key in text for key in _SOFT_ARTIFACT_KEYWORDS):
        return True
    return False


class ResponseHandler:
    """
    响应处理器。

    负责 LLM 响应的后处理，包括任务完成度验证和复盘分析。
    """

    def __init__(self, brain: Any, memory_manager: Any = None) -> None:
        """
        Args:
            brain: Brain 实例，用于 LLM 调用
            memory_manager: MemoryManager 实例（可选，用于保存复盘结果）
        """
        self._brain = brain
        self._memory_manager = memory_manager

    # 系统/组织自动注入的「被动收到」类元指令前缀（17 项）。命中其中任何
    # 一个意味着「user_request」实际上不是真正用户输入，而是 OrgRuntime /
    # reasoning_engine 后端合成的汇总/通知/系统消息：
    #   - reasoning_engine / agent 自注入：「[用户指令最终汇总]」「[系统]」
    #     「[系统提示]」「[组织]」「[以上是之前的对话历史」
    #   - OrgRuntime 13 种 type_label 中除「[收到任务]」之外的 12 种被动通知
    # 「[收到任务]」专门保留 verify，因为它是子节点真正接到的工作派单。
    # 显式白名单制，不写正则一刀切，避免误伤恰好以 "[" 开头的真实用户输入。
    _SYSTEM_REQUEST_PREFIXES: tuple[str, ...] = _SYSTEM_REQUEST_PREFIXES_TUPLE

    @staticmethod
    def _request_expects_artifact(
        user_request: str | None,
        has_produced_files: bool = False,
    ) -> bool:
        # 兼容旧调用点：转发到模块级 ``request_expects_artifact``。
        # 该函数同时被 runtime / reasoning_engine 直接 import，避免依赖
        # ResponseHandler 实例属性导致的 stale 信号问题。
        return request_expects_artifact(user_request, has_produced_files=has_produced_files)

    async def verify_task_completion(
        self,
        user_request: str,
        assistant_response: str,
        executed_tools: list[str],
        delivery_receipts: list[dict] | None = None,
        tool_results: list[dict] | None = None,
        conversation_id: str | None = None,
        bypass: bool = False,
        accepted_child_count: int = 0,
        has_recent_accepted_signal: bool = False,
    ) -> bool:
        """
        任务完成度复核。

        让 LLM 判断当前响应是否真正完成了用户的意图。

        Args:
            user_request: 用户原始请求
            assistant_response: 助手当前响应
            executed_tools: 已执行的工具列表
            delivery_receipts: 交付回执
            tool_results: 累积的工具执行结果（含 is_error 标记）
            conversation_id: 对话 ID（用于 Plan 检查）
            bypass: 当 Supervisor 已介入时跳过验证

        Returns:
            True 如果任务已完成
        """
        if bypass:
            logger.info("[TaskVerify] Bypassed (supervisor intervention active)")
            return True

        # 内层兜底：当 user_request 命中「被动通知」类前缀（17 项，
        # 含「[收到任务交付]」「[用户指令最终汇总]」「[系统]」等）时，上游
        # reasoning_engine 计算 is_summary_round / supervisor bypass 可能因
        # history 时间戳、消息压缩等边界情况失误，导致 bypass=False。这里
        # 直接看 user_request 头部前缀做最终防线：
        #   - root 节点收到下属 TASK_DELIVERED 时无需附件硬约束；
        #   - 汇总轮、被动通知轮统一豁免 verify，避免错误 emit task_failed。
        # 注意「[收到任务]」不在前缀里——子节点真正派单仍走完整 verify。
        head = (user_request or "").lstrip()
        if head and head.startswith(self._SYSTEM_REQUEST_PREFIXES):
            preview = head[:30].replace("\n", " ")
            logger.info(
                "[TaskVerify] Bypassed (passive-notification prefix matched: %r)",
                preview,
            )
            return True

        delivery_receipts = delivery_receipts or []

        # === Deterministic Validation (Agent Harness) ===
        plan_fail_reason = ""
        try:
            from .validators import ValidationContext, ValidationResult, create_default_registry

            val_context = ValidationContext(
                user_request=user_request,
                assistant_response=assistant_response,
                executed_tools=executed_tools or [],
                delivery_receipts=delivery_receipts,
                tool_results=tool_results or [],
                conversation_id=conversation_id or "",
                accepted_child_count=int(accepted_child_count or 0),
                has_recent_accepted_signal=bool(has_recent_accepted_signal),
            )
            registry = create_default_registry()
            report = registry.run_all(val_context)

            if report.applicable_count > 0:
                for output in report.outputs:
                    if output.result == ValidationResult.PASS and output.name in (
                        "ArtifactValidator",
                        "CompletePlanValidator",
                        "OrgDelegationValidator",
                    ):
                        logger.info(
                            f"[TaskVerify] Deterministic PASS: {output.name} — {output.reason}"
                        )
                        return True

                for output in report.outputs:
                    if output.result == ValidationResult.FAIL and output.name == "PlanValidator":
                        plan_fail_reason = output.reason
                        logger.info(
                            f"[TaskVerify] PlanValidator FAIL (non-blocking): {output.reason}"
                        )

                # NOTE: 历史上这里有一段把 ArtifactValidator FAIL 当 PASS 的"临时
                # 兜底"（理由：交付失败是基础设施问题不算 agent 错），结果就是
                # 哪怕 LLM 完全没产出文件、明明该交附件也只回了一段文字，验收
                # 也会被静默放行 → 用户感觉"明明要附件结果一个文件没有"。这条
                # 短路在 2025-04 被删除：FAIL 就让它 FAIL，下游 LLM 复核或硬
                # verify_incomplete 路径会负责给出正确的失败原因和重试提示。
        except Exception as e:
            logger.debug(f"[TaskVerify] Deterministic validation skipped: {e}")

        # === Trust-but-verify: org_submit_deliverable / deliver_artifacts 信任路径 ===
        # 组织节点用 org_submit_deliverable 提交交付物时，如果 deliverable 内容
        # 足够长或带文件附件，说明已经实际产出，直接放行；否则才走 LLM 复核。
        # 这是修复"sub-agent 实际已经写好文档/代码并提交，但 LLM verify 误判
        # 'verify_incomplete' 反复重试"的关键。
        # E1-1：trust-but-verify 在「expects_artifact=True」时收紧——纯文字
        # 即便 ≥200 字符也不能 PASS，必须有附件。原因：用户明确要求"导出/写文件/
        # 生成报告/做 PPT"等场景下，只回长文 = 没完成；放行就成了用户反馈的
        # "明明要附件结果一个文件没有"。这条收紧只在 expects_artifact 才生效，
        # 其它场景（普通问答 / 计划讨论 / 复盘）维持原 PASS 阈值，行为不变。
        # ──── has_produced_files 信号：当本次会话已经有 delivery_receipts 或
        # write_file 已被执行时，"方案/策划/计划/报告"等弱信号词也升级为
        # expects_artifact=True，避免 coordinator 收到子节点 markdown 后忘
        # deliver_artifacts 又被 trust-but-verify INSUFFICIENT 兜到 LLM 复核。
        _has_produced_files = bool(delivery_receipts) or any(
            (tr.get("tool_name") or tr.get("name") or "") in {"write_file", "auto_persist_node_final_answer"}
            for tr in (tool_results or [])
            if isinstance(tr, dict) and not tr.get("is_error")
        )
        expects_artifact = self._request_expects_artifact(
            user_request, has_produced_files=_has_produced_files
        )
        try:
            executed_set = set(executed_tools or [])
            tool_results_list = tool_results or []
            org_submit_ok = "org_submit_deliverable" in executed_set
            deliver_artifacts_ok = "deliver_artifacts" in executed_set
            successful_receipts = [
                r
                for r in delivery_receipts
                if r.get("status") in {"delivered", "skipped", "relayed"}
            ]
            failed_receipts = [
                r
                for r in delivery_receipts
                if r.get("status") not in {"delivered", "skipped", "relayed"}
            ]

            if org_submit_ok or deliver_artifacts_ok:
                # 找出 submit_deliverable 工具调用对应的成功结果
                submit_ok_run = False
                deliverable_len = 0
                attachments_count = 0
                for tr in tool_results_list:
                    name = tr.get("tool_name") or tr.get("name") or ""
                    if name not in (
                        "org_submit_deliverable", "deliver_artifacts",
                    ):
                        continue
                    if tr.get("is_error"):
                        continue
                    submit_ok_run = True
                    args = tr.get("arguments") or tr.get("input") or {}
                    if isinstance(args, dict):
                        deliverable_len = max(
                            deliverable_len,
                            len(args.get("deliverable") or ""),
                        )
                        files = args.get("file_attachments") or args.get("files") or []
                        if isinstance(files, list):
                            attachments_count = max(attachments_count, len(files))

                if successful_receipts:
                    attachments_count = max(attachments_count, len(successful_receipts))

                # 兜底：tool_results 里拿不到详细参数（旧调用路径），退一步只看
                # 工具是否被执行 + delivery_receipts 是否非空（说明附件已发送）
                if not submit_ok_run and (org_submit_ok or deliver_artifacts_ok):
                    submit_ok_run = True
                    if successful_receipts:
                        attachments_count = max(attachments_count, len(successful_receipts))

                # 阈值分两档：
                #   - expects_artifact=True：必须 attachments_count >= 1，纯文本不算
                #   - expects_artifact=False（普通讨论/答复）：保持历史行为
                #     (deliverable_len>=200 或 attachments_count>=1) 任一即可
                if expects_artifact:
                    pass_ok = submit_ok_run and attachments_count >= 1
                else:
                    pass_ok = submit_ok_run and (
                        deliverable_len >= 200 or attachments_count >= 1
                    )

                if expects_artifact and deliver_artifacts_ok and failed_receipts:
                    logger.info(
                        "[TaskVerify] artifact delivery returned failed receipts, INCOMPLETE"
                    )
                    return False
                if pass_ok:
                    logger.info(
                        "[TaskVerify] trust-but-verify PASS: "
                        "submit_deliverable executed, deliverable_len=%d files=%d "
                        "expects_artifact=%s",
                        deliverable_len, attachments_count, expects_artifact,
                    )
                    return True
                if expects_artifact and deliver_artifacts_ok and delivery_receipts:
                    logger.info(
                        "[TaskVerify] artifact delivery attempted but no successful receipt, "
                        "INCOMPLETE"
                    )
                    return False
                if submit_ok_run:
                    logger.info(
                        "[TaskVerify] trust-but-verify INSUFFICIENT: "
                        "deliverable_len=%d files=%d expects_artifact=%s "
                        "→ fall back to LLM verify",
                        deliverable_len, attachments_count, expects_artifact,
                    )
        except Exception as exc:
            logger.debug(
                "[TaskVerify] trust-but-verify gate skipped: %s", exc,
            )

        # 宣称已交付但无证据
        if (
            any(
                k in (assistant_response or "")
                for k in (
                    "已发送",
                    "已交付",
                    "已发给你",
                    "已发给您",
                    "下面是图片",
                    "给你一张",
                    "给您一张",
                    "我给你发",
                    "我给您发",
                    "我为你生成了图片",
                    "我为您生成了图片",
                    "图片如下",
                    "附件如下",
                )
            )
            and not delivery_receipts
            and "deliver_artifacts" not in (executed_tools or [])
        ):
            logger.info("[TaskVerify] delivery claim without receipts, INCOMPLETE")
            return False

        if (
            expects_artifact
            and not delivery_receipts
            and "deliver_artifacts" not in (executed_tools or [])
        ):
            logger.info(
                "[TaskVerify] artifact requested but no delivery receipts/tools, INCOMPLETE"
            )
            return False

        _delivered_ok = any(r.get("status") == "delivered" for r in delivery_receipts)
        # 宣称用户在本机已看到界面/窗口，但无交付回执等可证实路径（与「空口交付」同构）
        if (
            any(
                k in (assistant_response or "")
                for k in (
                    "你应该能看到",
                    "你屏幕上",
                    "你桌面上",
                    "你的桌面",
                    "在你电脑上",
                    "你玩游戏时能看到",
                )
            )
            and not _delivered_ok
            and "deliver_artifacts" not in (executed_tools or [])
        ):
            logger.info("[TaskVerify] user-visible UI claim without delivery/evidence, INCOMPLETE")
            return False

        # LLM 判断
        from .tool_executor import smart_truncate

        user_display, _ = smart_truncate(user_request, 3000, save_full=False, label="verify_user")
        response_display, _ = smart_truncate(
            assistant_response, 8000, save_full=False, label="verify_response"
        )

        _plan_section = ""
        if plan_fail_reason:
            _plan_section = (
                f"\n## Plan 状态\n"
                f"当前 Plan 有未完成步骤: {plan_fail_reason}\n"
                f"注意: 若用户意图是**宿主内**任务（工作区写文件、宿主 shell、宿主浏览器自动化等），"
                f"工具已成功执行且与 Plan 一致时可判 COMPLETED。"
                f"若用户意图是**用户本机可观测**（本机 GUI 窗口、本机软件安装、游戏内 overlay 等），"
                f"仅宿主侧 run_shell 等成功**不足**；需有交付回执、用户可在自己机器上执行的明确步骤，"
                f"或助手已清楚说明「效果在宿主、用户屏不可见」并给出可行替代方案。\n"
            )

        verify_prompt = f"""请判断以下交互是否已经**完成**用户的意图。

## 用户消息
{user_display}

## 助手响应
{response_display}

## 已执行的工具
{", ".join(executed_tools) if executed_tools else "无"}

## 附件交付回执（如有）
{delivery_receipts if delivery_receipts else "无"}
{_plan_section}
## 执行域前提（必读）

工具在 **OpenAkita 宿主**执行，与用户发消息的设备/IM 客户端**默认不同域**。宿主上命令成功 ≠ 用户本机已出现窗口或已安装软件。

## 判断标准

### 非任务类消息（直接判 COMPLETED）
- 如果用户消息是**闲聊/问候**，助手已礼貌回复 → **COMPLETED**
- 如果用户消息是**简单确认/反馈**，助手已简短回应 → **COMPLETED**
- 如果用户消息是**简单问答**，助手已给出回答 → **COMPLETED**

### 任务类消息 — 分层完成标准

**A. 宿主内可验证的完成**（以下任一满足且用户意图属此类 → 可 COMPLETED）
- 已执行 write_file / edit_file 等且目标为工作区内保存文件
- 已执行浏览器工具且意图是在**宿主侧**操作网页
- 已有 **deliver_artifacts** 成功回执（status=delivered），且用户要的是可交付产物
- 已调用 **complete_todo** 且 Plan 语义已闭环
- 工具在宿主执行成功，且用户请求**未要求**在用户本人电脑屏幕/本机系统中看到效果

**B. 用户本机可观测的完成**（用户明确要求在本机看到窗口、本机安装、游戏画面内效果等）
- 仅有宿主侧 run_shell / Python 成功**不能**单独作为完成证据
- 需至少其一：成功交付（回执）、回复中含用户可在**自己机器**上执行的明确命令/步骤并已给出、或助手明确说明边界且用户目标已调整为可达成形态

**C. 仍在进行中**
- 响应仅为「现在开始…」「让我…」且关键工具未执行 → **INCOMPLETE**

**D. 上游平台硬性限制**
- 助手已实际尝试且遇不可绕过的 API/平台限制，并已向用户解释 → **COMPLETED**
- 若仍有其他可行路径（换命令、换文件路径等）→ **INCOMPLETE**

**E. 多路径同因失败（关键去抖规则）**
- 如果助手已经在**两个或两个以上不同路径 / 命令 / 文件**上尝试同一件事，
  并且**根本失败原因相同**（例如：同一权限错误 / 同一路径不存在 / 同一依赖缺失），
  这是上游限制而非"换条路就能成"，必须判 **COMPLETED**（带说明），不要再继续 INCOMPLETE
  让模型陷入"再换一个路径试试"的死循环。
- 例：依次执行 `pip install foo`、`python -m pip install foo`、`pipx install foo` 三次都
  报同样的 PermissionError → 这是**环境**问题，已超出 LLM 可解决范围，应判 COMPLETED 并
  把诊断结果交还用户。

## 回答要求
STATUS: COMPLETED 或 INCOMPLETE
EVIDENCE: 完成的证据
MISSING: 缺失的内容
NEXT: 建议的下一步"""

        try:
            response = await self._brain.think_lightweight(
                prompt=verify_prompt,
                system=(
                    "你是任务完成度判断助手。OpenAkita 工具在宿主环境执行，与用户聊天设备通常不是同一台机器；"
                    "必须区分「宿主内已验证完成」与「用户本机可观测完成」，不要仅凭宿主命令退出成功判定后者已完成。"
                ),
                max_tokens=512,
            )

            result = response.content.strip().upper() if response.content else ""
            is_completed = "STATUS: COMPLETED" in result or (
                "COMPLETED" in result and "INCOMPLETE" not in result
            )

            logger.info(
                f"[TaskVerify] request={user_request[:50]}... result={'COMPLETED' if is_completed else 'INCOMPLETE'}"
            )

            # Decision Trace: 记录验证决策
            try:
                from ..tracing.tracer import get_tracer

                tracer = get_tracer()
                tracer.record_decision(
                    decision_type="task_verification",
                    reasoning=f"tools={executed_tools}, receipts={len(delivery_receipts)}",
                    outcome="completed" if is_completed else "incomplete",
                )
            except Exception:
                pass

            return is_completed

        except Exception as e:
            logger.warning(f"[TaskVerify] Failed to verify: {e}, assuming INCOMPLETE")
            return False

    async def do_task_retrospect(self, task_monitor: Any) -> str:
        """
        执行任务复盘分析。

        当任务耗时过长时，让 LLM 分析原因。

        Args:
            task_monitor: TaskMonitor 实例

        Returns:
            复盘分析结果
        """
        try:
            from .task_monitor import RETROSPECT_PROMPT

            context = task_monitor.get_retrospect_context()
            prompt = RETROSPECT_PROMPT.format(context=context)

            response = await self._brain.think_lightweight(
                prompt=prompt,
                system="你是一个任务执行分析专家。请简洁地分析任务执行情况，找出耗时原因和改进建议。",
                max_tokens=512,
            )

            result = strip_thinking_tags(response.content).strip() if response.content else ""

            task_monitor.metrics.retrospect_result = result

            # 如果发现重复错误模式，记录到记忆
            if self._memory_manager and any(kw in result for kw in ("重复", "无效", "弯路")):
                try:
                    from ..memory.types import Memory, MemoryPriority, MemoryScope, MemoryType

                    memory = Memory(
                        type=MemoryType.ERROR,
                        priority=MemoryPriority.LONG_TERM,
                        content=f"任务执行复盘发现问题：{result}",
                        source="retrospect",
                        importance_score=0.7,
                        scope=MemoryScope.AGENT,
                    )
                    self._memory_manager.add_memory(
                        memory, scope=MemoryScope.AGENT
                    )
                except Exception as e:
                    logger.warning(f"Failed to save retrospect to memory: {e}")

            return result

        except Exception as e:
            logger.warning(f"Task retrospect failed: {e}")
            return ""

    async def do_task_retrospect_background(self, task_monitor: Any, session_id: str) -> None:
        """
        后台执行任务复盘分析（不阻塞主响应）。
        """
        try:
            retrospect_result = await self.do_task_retrospect(task_monitor)

            if not retrospect_result:
                return

            from .task_monitor import RetrospectRecord, get_retrospect_storage

            record = RetrospectRecord(
                task_id=task_monitor.metrics.task_id,
                session_id=session_id,
                description=task_monitor.metrics.description,
                duration_seconds=task_monitor.metrics.total_duration_seconds,
                iterations=task_monitor.metrics.total_iterations,
                model_switched=task_monitor.metrics.model_switched,
                initial_model=task_monitor.metrics.initial_model,
                final_model=task_monitor.metrics.final_model,
                retrospect_result=retrospect_result,
            )

            storage = get_retrospect_storage()
            storage.save(record)

            logger.info(f"[Session:{session_id}] Retrospect saved: {task_monitor.metrics.task_id}")

        except Exception as e:
            logger.error(f"[Session:{session_id}] Background retrospect failed: {e}")

    @staticmethod
    def should_compile_prompt(message: str) -> bool:
        """判断是否需要进行 Prompt 编译"""
        if len(message.strip()) < 20:
            return False
        return True

    @staticmethod
    def get_last_user_request(messages: list[dict]) -> str:
        """获取最后一条用户请求"""
        from .tool_executor import smart_truncate

        def _strip_context_prefix(text: str) -> str:
            """移除对话历史前缀，提取真正的用户输入。"""
            _marker = "：]"
            if text.startswith("[以上是之前的对话历史"):
                idx = text.find(_marker)
                if idx != -1:
                    text = text[idx + len(_marker) :].strip()
            return text

        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str) and not content.startswith("[系统]"):
                    content = _strip_context_prefix(content)
                    result, _ = smart_truncate(content, 3000, save_full=False, label="user_request")
                    return result
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text = part.get("text", "")
                            if not text.startswith("[系统]"):
                                text = _strip_context_prefix(text)
                                result, _ = smart_truncate(
                                    text, 3000, save_full=False, label="user_request"
                                )
                                return result
        return ""

