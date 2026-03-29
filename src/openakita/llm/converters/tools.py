"""
工具调用格式转换器

负责在内部格式（Anthropic-like）和 OpenAI 格式之间转换工具定义和调用。
支持文本格式工具调用解析（降级方案）。
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from ..types import Tool, ToolUseBlock

logger = logging.getLogger(__name__)

# JSON 解析失败时写入 input 的标记键，供 ToolExecutor 拦截
PARSE_ERROR_KEY = "__parse_error__"


def _try_repair_json(s: str) -> dict | None:
    """尝试修复被截断的 JSON 字符串。

    LLM 生成超长 tool_call arguments 时，API 可能截断 JSON，
    导致 json.loads 失败。此函数尝试简单修复：
    - 补齐缺少的引号
    - 补齐缺少的花括号
    返回 None 表示修复失败。
    """
    s = s.strip()
    if not s:
        return None

    if not s.startswith("{"):
        return None

    for suffix in ['"}', '"}}', '"}}}}', '"}]}', '"]}', '"}', '}', '}}', '}}}']:
        try:
            result = json.loads(s + suffix)
            if isinstance(result, dict):
                logger.debug(
                    f"[JSON_REPAIR] Repaired with suffix {suffix!r}, "
                    f"recovered {len(result)} keys: {sorted(result.keys())}"
                )
                return result
        except json.JSONDecodeError:
            continue

    return None


def _dump_raw_arguments(tool_name: str, arguments: str) -> None:
    """将解析失败的原始 arguments 写入诊断文件，方便排查截断问题。"""
    try:
        from datetime import datetime

        debug_dir = Path("data/llm_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        dump_file = debug_dir / f"truncated_args_{tool_name}_{ts}.txt"
        dump_file.write_text(arguments, encoding="utf-8")
        logger.info(
            f"[TOOL_CALL] Raw truncated arguments ({len(arguments)} chars) "
            f"saved to {dump_file}"
        )
    except Exception as exc:
        logger.warning(f"[TOOL_CALL] Failed to dump raw arguments: {exc}")


# ── OpenAI Chat Completions 格式转换 ──────────────────────


def convert_tools_to_anthropic(tools: list[Tool]) -> list[dict]:
    """将内部工具定义转换为 Anthropic 格式（内部格式本身即 Anthropic-like）。"""
    _KNOWN_TOOL_NAMES.update(t.name for t in tools)
    return [tool.to_dict() for tool in tools]


def convert_tools_to_openai(tools: list[Tool]) -> list[dict]:
    """将内部工具定义转换为 OpenAI 格式。"""
    _KNOWN_TOOL_NAMES.update(t.name for t in tools)
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }
        for tool in tools
    ]


def convert_tools_from_openai(tools: list[dict]) -> list[Tool]:
    """将 OpenAI 工具定义转换为内部格式。"""
    result = []
    for tool in tools:
        if tool.get("type") == "function":
            func = tool.get("function", {})
            result.append(
                Tool(
                    name=func.get("name", ""),
                    description=func.get("description", ""),
                    input_schema=func.get("parameters", {}),
                )
            )
    return result


def convert_tool_calls_from_openai(tool_calls: list[dict]) -> list[ToolUseBlock]:
    """将 OpenAI 工具调用转换为内部格式。

    OpenAI 格式:
    {
        "id": "call_xxx",
        "type": "function",
        "function": {
            "name": "get_weather",
            "arguments": "{\"location\": \"Beijing\"}"  # JSON 字符串
        }
    }

    内部格式:
    {
        "type": "tool_use",
        "id": "call_xxx",
        "name": "get_weather",
        "input": {"location": "Beijing"}  # JSON 对象
    }
    """
    result = []
    for tc in tool_calls:
        # 兼容：部分 OpenAI 兼容网关可能缺失 tc.type 字段，但仍提供 function{name,arguments}
        func = tc.get("function") or {}
        tc_type = tc.get("type")
        if tc_type == "function" or (not tc_type and isinstance(func, dict) and func.get("name")):

            arguments = func.get("arguments", "{}")
            if isinstance(arguments, str):
                try:
                    input_dict = json.loads(arguments)
                except json.JSONDecodeError as je:
                    tool_name = func.get("name", "?")
                    arg_len = len(arguments)
                    arg_preview = arguments[:300] + "..." if arg_len > 300 else arguments
                    logger.warning(
                        f"[TOOL_CALL] JSON parse failed for tool '{tool_name}': "
                        f"{je} | arg_len={arg_len} | preview={arg_preview!r}"
                    )
                    input_dict = _try_repair_json(arguments)
                    _dump_raw_arguments(tool_name, arguments)
                    if input_dict is not None:
                        recovered_keys = sorted(input_dict.keys())
                        err_msg = (
                            f"❌ 工具 '{tool_name}' 的参数 JSON 被 API 截断后自动修复，"
                            f"但内容可能不完整（恢复的键: {recovered_keys}）。\n"
                            f"原始参数长度: {arg_len} 字符。\n"
                            "请缩短参数后重试：\n"
                            "- write_file / edit_file：将大文件拆分为多次小写入\n"
                            "- 其他工具：精简参数，避免嵌入超长文本"
                        )
                        input_dict = {PARSE_ERROR_KEY: err_msg}
                        logger.warning(
                            f"[TOOL_CALL] JSON repair succeeded for tool '{tool_name}' "
                            f"(recovered keys: {recovered_keys}), treating as truncation "
                            f"error. Raw args ({arg_len} chars) dumped to data/llm_debug/."
                        )
                    else:
                        err_msg = (
                            f"❌ 工具 '{tool_name}' 的参数 JSON 被 API 截断且无法修复"
                            f"（共 {arg_len} 字符）。\n"
                            "请缩短参数后重试：\n"
                            "- write_file / edit_file：将大文件拆分为多次小写入\n"
                            "- 其他工具：精简参数，避免嵌入超长文本"
                        )
                        input_dict = {PARSE_ERROR_KEY: err_msg}
                        logger.error(
                            f"[TOOL_CALL] JSON repair failed for tool '{tool_name}', "
                            f"injecting parse error marker. "
                            f"Raw args ({arg_len} chars) dumped to data/llm_debug/."
                        )
            else:
                input_dict = arguments

            extra = tc.get("extra_content") or None
            result.append(
                ToolUseBlock(
                    id=tc.get("id", ""),
                    name=func.get("name", ""),
                    input=input_dict,
                    provider_extra=extra,
                )
            )

    return result


def convert_tool_calls_to_openai(tool_uses: list[ToolUseBlock]) -> list[dict]:
    """将内部工具调用转换为 OpenAI 格式。"""
    result = []
    for tu in tool_uses:
        tc: dict = {
            "id": tu.id,
            "type": "function",
            "function": {
                "name": tu.name,
                "arguments": json.dumps(tu.input, ensure_ascii=False),
            },
        }
        if tu.provider_extra:
            tc["extra_content"] = tu.provider_extra
        result.append(tc)
    return result


def convert_tool_result_to_openai(tool_use_id: str, content: str, is_error: bool = False) -> dict:
    """将工具结果转换为 OpenAI 格式消息。"""
    return {
        "role": "tool",
        "tool_call_id": tool_use_id,
        "content": content,
    }


def convert_tool_result_from_openai(msg: dict) -> dict | None:
    """将 OpenAI 工具结果消息转换为内部格式。"""
    if msg.get("role") != "tool":
        return None

    return {
        "type": "tool_result",
        "tool_use_id": msg.get("tool_call_id", ""),
        "content": msg.get("content", ""),
    }


# ── 文本格式工具调用解析（降级方案）───────────────────────
#
# 注册表驱动：每种格式由 _TextToolFormat(name, detect_re, parse) 描述。
# parse 函数接收完整文本，返回 (清理后文本, 工具调用列表)，
# 解析与清理在同一函数内完成，消除不同步风险。
# 新增格式只需添加一条注册 + 编写 parse 函数。


@dataclass(frozen=True)
class _TextToolFormat:
    """一种文本工具调用格式的描述。"""

    name: str
    detect_re: re.Pattern
    parse: Callable[[str], tuple[str, list[ToolUseBlock]]]
    fallback: bool = False


# ── 共享: <invoke> 块解析器 ────────────────────────────


def _parse_invoke_blocks(content: str) -> list[ToolUseBlock]:
    """解析 <invoke> 块中的工具调用（被多种 XML 包装格式共享）。"""
    tool_calls = []

    invoke_pattern = r'<invoke\s+name=["\']?([^"\'>\s]+)["\']?\s*>(.*?)</invoke>'
    invokes = re.findall(invoke_pattern, content, re.DOTALL | re.IGNORECASE)

    if not invokes:
        invoke_pattern_incomplete = (
            r'<invoke\s+name=["\']?([^"\'>\s]+)["\']?\s*>(.*?)(?:</invoke>|$)'
        )
        invokes = re.findall(invoke_pattern_incomplete, content, re.DOTALL | re.IGNORECASE)

    for tool_name, invoke_content in invokes:
        params = {}
        param_pattern = r'<parameter\s+name=["\']?([^"\'>\s]+)["\']?\s*>(.*?)</parameter>'
        param_matches = re.findall(param_pattern, invoke_content, re.DOTALL | re.IGNORECASE)

        for param_name, param_value in param_matches:
            param_value = param_value.strip()
            try:
                params[param_name] = json.loads(param_value)
            except json.JSONDecodeError:
                params[param_name] = param_value

        tool_call = ToolUseBlock(
            id=f"text_call_{uuid.uuid4().hex[:8]}",
            name=tool_name.strip(),
            input=params,
        )
        tool_calls.append(tool_call)
        logger.info(
            f"[TEXT_TOOL_PARSE] Extracted tool call: {tool_name} "
            f"with params: {list(params.keys())}"
        )

    return tool_calls


def _make_invoke_wrapper_parser(
    open_tag: str, close_tag: str,
) -> Callable[[str], tuple[str, list[ToolUseBlock]]]:
    """为使用 <invoke> 内部结构的 XML 包装格式创建解析器。

    function_calls 和 minimax:tool_call 结构相同（都包裹 <invoke> 块），
    仅外层标签不同，通过此工厂函数统一生成。
    """
    _open_esc = re.escape(open_tag)
    _close_esc = re.escape(close_tag)
    _complete_re = re.compile(
        f"{_open_esc}\\s*(.*?)\\s*{_close_esc}", re.DOTALL | re.IGNORECASE,
    )
    _incomplete_re = re.compile(
        f"{_open_esc}\\s*(.*?)$", re.DOTALL | re.IGNORECASE,
    )

    def parser(text: str) -> tuple[str, list[ToolUseBlock]]:
        matches = _complete_re.findall(text) or _incomplete_re.findall(text)
        tool_calls: list[ToolUseBlock] = []
        for m in matches:
            tool_calls.extend(_parse_invoke_blocks(m))
        if not tool_calls:
            return text, []
        clean = _complete_re.sub("", text).strip()
        clean = _incomplete_re.sub("", clean).strip()
        return clean, tool_calls

    return parser


# ── Kimi K2 格式 ──────────────────────────────────────


def _parse_kimi_k2(text: str) -> tuple[str, list[ToolUseBlock]]:
    """解析 Kimi K2 格式的工具调用。

    格式：
    <<|tool_calls_section_begin|>>
    <<|tool_call_begin|>>functions.get_weather:0
    <<|tool_call_argument_begin|>>{"city": "Beijing"}<<|tool_call_end|>>
    <<|tool_calls_section_end|>>
    """
    if "<<|tool_calls_section_begin|>>" not in text:
        return text, []

    section_pattern = r"<<\|tool_calls_section_begin\|>>(.*?)<<\|tool_calls_section_end\|>>"
    section_matches = re.findall(section_pattern, text, re.DOTALL)

    if not section_matches:
        section_pattern_incomplete = r"<<\|tool_calls_section_begin\|>>(.*?)$"
        section_matches = re.findall(section_pattern_incomplete, text, re.DOTALL)

    tool_calls: list[ToolUseBlock] = []
    for section in section_matches:
        call_pattern = (
            r"<<\|tool_call_begin\|>>\s*(?P<tool_id>[\w\.]+:\d+)\s*"
            r"<<\|tool_call_argument_begin\|>>\s*(?P<arguments>.*?)\s*<<\|tool_call_end\|>>"
        )

        for match in re.finditer(call_pattern, section, re.DOTALL):
            tool_id = match.group("tool_id")
            arguments_str = match.group("arguments").strip()

            try:
                func_name = tool_id.split(".")[1].split(":")[0]
            except IndexError:
                func_name = tool_id

            try:
                arguments = json.loads(arguments_str)
            except json.JSONDecodeError:
                arguments = {"raw": arguments_str}

            tool_calls.append(ToolUseBlock(
                id=f"kimi_call_{tool_id.replace('.', '_').replace(':', '_')}",
                name=func_name,
                input=arguments,
            ))
            logger.info(
                f"[KIMI_TOOL_PARSE] Extracted tool call: {func_name} "
                f"with args: {list(arguments.keys())}"
            )

    if not tool_calls:
        return text, []

    clean = re.sub(
        r"<<\|tool_calls_section_begin\|>>.*?<<\|tool_calls_section_end\|>>",
        "", text, flags=re.DOTALL,
    ).strip()
    clean = re.sub(
        r"<<\|tool_calls_section_begin\|>>.*$", "", clean, flags=re.DOTALL,
    ).strip()
    return clean, tool_calls


# ── GLM 格式 ──────────────────────────────────────────

_GLM_COMPLETE_RE = re.compile(
    r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL | re.IGNORECASE,
)
_GLM_INCOMPLETE_RE = re.compile(
    r"<tool_call>\s*(.*?)$", re.DOTALL | re.IGNORECASE,
)
_GLM_KV_RE = re.compile(
    r"<arg_key>\s*(.*?)\s*</arg_key>\s*<arg_value>\s*(.*?)\s*</arg_value>",
    re.DOTALL,
)


def _parse_glm(text: str) -> tuple[str, list[ToolUseBlock]]:
    """解析 GLM 模型的 <tool_call> 格式。

    格式:
    <tool_call>run_shell<arg_key>command</arg_key><arg_value>...</arg_value></tool_call>
    """
    matches = _GLM_COMPLETE_RE.findall(text) or _GLM_INCOMPLETE_RE.findall(text)

    tool_calls: list[ToolUseBlock] = []
    for content in matches:
        name_match = re.match(r"(\w[\w-]*)", content.strip())
        if not name_match:
            continue
        tool_name = name_match.group(1)

        params: dict = {}
        for kv in _GLM_KV_RE.finditer(content):
            key, val = kv.group(1).strip(), kv.group(2).strip()
            try:
                params[key] = json.loads(val)
            except json.JSONDecodeError:
                params[key] = val

        tool_calls.append(ToolUseBlock(
            id=f"glm_call_{uuid.uuid4().hex[:8]}",
            name=tool_name,
            input=params,
        ))
        logger.info(
            f"[GLM_TOOL_PARSE] Extracted tool call: {tool_name} "
            f"with params: {list(params.keys())}"
        )

    if not tool_calls:
        return text, []

    clean = _GLM_COMPLETE_RE.sub("", text).strip()
    clean = _GLM_INCOMPLETE_RE.sub("", clean).strip()
    return clean, tool_calls


# ── [TOOL_CALL] 标签格式 ──────────────────────────────────
#
# kimi-k2-thinking 等模型将工具调用包裹在 [TOOL_CALL]...[/TOOL_CALL] 标签中。
# 内部格式不固定，已观察到以下变体：
#
# A. arrow + --keys:
#    [TOOL_CALL] {tool => "web_search", "args": {--query "test", --max_results 10}}[/TOOL_CALL]
# B. 标准 JSON:
#    [TOOL_CALL] { "tool": "get_org", "args": { "id": "abc" } } [/TOOL_CALL]
# C. 等号语法:
#    [TOOL_CALL] {tool = "setup_organization", args = {"action": "get_org"}}[/TOOL_CALL]
# D. 紧凑多行 JSON:
#    [TOOL_CALL]{ "tool": "name", "args": {...} }[/TOOL_CALL]
#
# 结束标签可以是 [/TOOL_CALL] 或 </invoke>，也可能缺失。

_TOOL_CALL_TAG_DETECT_RE = re.compile(r"\[TOOL_CALL\]", re.IGNORECASE)

_TOOL_CALL_TAG_BLOCK_RE = re.compile(
    r"\[TOOL_CALL\]\s*(.*?)\s*(?:\[/TOOL_CALL\]|</invoke>)",
    re.DOTALL | re.IGNORECASE,
)

_TOOL_CALL_TAG_UNCLOSED_RE = re.compile(
    r"\[TOOL_CALL\]\s*(\{.+\})\s*$",
    re.DOTALL | re.IGNORECASE,
)

_TAG_TOOL_NAME_RE = re.compile(
    r"""(?:"?(?:tool|name|function)"?\s*(?:=>|=|:)\s*"([^"]+)")""",
)

_TAG_ARGS_START_RE = re.compile(
    r"""(?:"?(?:args|arguments|parameters|input)"?\s*(?:=>|=|:)\s*)(\{)""",
)


def _find_matching_brace(text: str, start: int) -> int:
    """找到与 start 处 '{' 匹配的 '}' 位置，正确跳过引号内的花括号。"""
    if start >= len(text) or text[start] != "{":
        return -1
    depth = 0
    in_string = False
    escape_next = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _extract_tool_from_obj(obj: dict) -> tuple[str, dict] | None:
    """从已解析的 dict 中提取工具名和参数。"""
    name = obj.get("tool") or obj.get("name") or obj.get("function")
    if not name or not isinstance(name, str):
        return None
    args = (
        obj.get("args")
        or obj.get("arguments")
        or obj.get("parameters")
        or obj.get("input")
        or {}
    )
    return name, args if isinstance(args, dict) else {}


def _normalize_tag_body(body: str) -> str:
    """将 arrow/equals/--key 语法标准化为 JSON 兼容格式。"""
    s = body
    s = re.sub(r"(\w+)\s*=>\s*", r'"\1": ', s)
    s = re.sub(r"(\w+)\s*=\s*(?=[\"'{[\d])", r'"\1": ', s)
    s = re.sub(r"--(\w+)\s+", r'"\1": ', s)
    return s


def _parse_tag_args_block(body: str) -> dict:
    """从 [TOOL_CALL] 体中提取 args 部分并尝试解析为 dict。"""
    m = _TAG_ARGS_START_RE.search(body)
    if not m:
        return {}
    brace_start = m.start(1)
    brace_end = _find_matching_brace(body, brace_start)
    if brace_end < 0:
        return {}
    args_str = body[brace_start : brace_end + 1]
    for attempt in (args_str, _normalize_tag_body(args_str)):
        try:
            result = json.loads(attempt)
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, ValueError):
            continue
    return {}


def _parse_tool_call_tag_body(body: str) -> tuple[str, dict] | None:
    """解析 [TOOL_CALL] 标签内的内容，提取工具名和参数。"""
    body = body.strip()
    if not body:
        return None

    for text_to_try in (body, _normalize_tag_body(body)):
        try:
            obj = json.loads(text_to_try)
            if isinstance(obj, dict):
                result = _extract_tool_from_obj(obj)
                if result:
                    return result
        except (json.JSONDecodeError, ValueError):
            continue

    name_match = _TAG_TOOL_NAME_RE.search(body)
    if not name_match:
        return None
    tool_name = name_match.group(1)
    args = _parse_tag_args_block(body)
    return tool_name, args


def _parse_tool_call_tags(text: str) -> tuple[str, list[ToolUseBlock]]:
    """解析 [TOOL_CALL]...[/TOOL_CALL] 格式的工具调用。"""
    tool_calls: list[ToolUseBlock] = []
    spans_to_remove: list[tuple[int, int]] = []

    for m in _TOOL_CALL_TAG_BLOCK_RE.finditer(text):
        result = _parse_tool_call_tag_body(m.group(1))
        if result:
            name, args = result
            tool_calls.append(ToolUseBlock(
                id=f"tag_call_{uuid.uuid4().hex[:12]}",
                name=name,
                input=args,
            ))
            spans_to_remove.append((m.start(), m.end()))

    if not tool_calls:
        for m in _TOOL_CALL_TAG_UNCLOSED_RE.finditer(text):
            result = _parse_tool_call_tag_body(m.group(1))
            if result:
                name, args = result
                tool_calls.append(ToolUseBlock(
                    id=f"tag_call_{uuid.uuid4().hex[:12]}",
                    name=name,
                    input=args,
                ))
                spans_to_remove.append((m.start(), m.end()))

    if not tool_calls:
        return text, []

    parts: list[str] = []
    prev = 0
    for s, e in sorted(spans_to_remove):
        parts.append(text[prev:s])
        prev = e
    parts.append(text[prev:])
    clean = "".join(parts).strip()

    clean = re.sub(r"\[/?TOOL_CALL\]", "", clean, flags=re.IGNORECASE).strip()
    return clean, tool_calls


# ── JSON 格式工具调用检测与解析 ──────────────────────────
# 部分模型（如 Qwen 2.5）在 failover 时会把工具调用以原始 JSON
# 写入文本响应，而非走结构化 tool_use。典型格式：
#   {{"name": "browser_open", "arguments": {"visible": true}}}
#   {"name": "web_search", "arguments": {"query": "test"}}

_JSON_TOOL_CALL_HEADER_RE = re.compile(
    r'\{+\s*"name"\s*:\s*"([a-z_][a-z0-9_]*)"\s*,\s*"arguments"\s*:\s*',
)


def _extract_balanced_braces(text: str, start: int) -> str | None:
    """从 start 位置的 ``{`` 开始提取一个括号平衡的 JSON 对象。"""
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _parse_json_tool_calls(text: str) -> tuple[str, list[ToolUseBlock]]:
    """从文本中提取 JSON 格式工具调用。

    匹配 {"name": "xxx", "arguments": {...}} 或双花括号变体。
    使用括号计数法正确处理深度嵌套的参数 JSON。
    返回 (清理后文本, 工具调用列表)。
    """
    tool_calls: list[ToolUseBlock] = []
    spans_to_remove: list[tuple[int, int]] = []

    for m in _JSON_TOOL_CALL_HEADER_RE.finditer(text):
        tool_name = m.group(1)
        args_start = m.end()

        args_str = _extract_balanced_braces(text, args_start)
        if args_str is None:
            continue

        outer_end = args_start + len(args_str)
        while outer_end < len(text) and text[outer_end] in " \t\n\r}":
            outer_end += 1

        outer_start = m.start()
        while outer_start > 0 and text[outer_start - 1] == "{":
            outer_start -= 1

        try:
            arguments = json.loads(args_str)
        except json.JSONDecodeError:
            arg_len = len(args_str)
            repaired = _try_repair_json(args_str)
            _dump_raw_arguments(tool_name, args_str)
            if repaired is not None:
                recovered_keys = sorted(repaired.keys())
                err_msg = (
                    f"❌ 工具 '{tool_name}' 的参数 JSON 被截断后自动修复，"
                    f"但内容可能不完整（恢复的键: {recovered_keys}）。\n"
                    f"原始参数长度: {arg_len} 字符。\n"
                    "请缩短参数后重试：\n"
                    "- write_file / edit_file：将大文件拆分为多次小写入\n"
                    "- 其他工具：精简参数，避免嵌入超长文本"
                )
                arguments = {PARSE_ERROR_KEY: err_msg}
                logger.warning(
                    f"[JSON_TOOL_PARSE] JSON repair succeeded for '{tool_name}' "
                    f"(recovered keys: {recovered_keys}), treating as truncation. "
                    f"Raw args ({arg_len} chars) dumped."
                )
            else:
                err_msg = (
                    f"❌ 工具 '{tool_name}' 的参数 JSON 被截断且无法修复"
                    f"（共 {arg_len} 字符）。\n"
                    "请缩短参数后重试：\n"
                    "- write_file / edit_file：将大文件拆分为多次小写入\n"
                    "- 其他工具：精简参数，避免嵌入超长文本"
                )
                arguments = {PARSE_ERROR_KEY: err_msg}
                logger.warning(
                    f"[JSON_TOOL_PARSE] Failed to parse/repair arguments for "
                    f"'{tool_name}' ({arg_len} chars). Injecting parse error marker."
                )

        tc = ToolUseBlock(
            id=f"json_call_{uuid.uuid4().hex[:8]}",
            name=tool_name,
            input=arguments,
        )
        tool_calls.append(tc)
        spans_to_remove.append((outer_start, outer_end))
        logger.info(
            f"[JSON_TOOL_PARSE] Extracted tool call: {tool_name} "
            f"with args: {list(arguments.keys()) if isinstance(arguments, dict) else '?'}"
        )

    if tool_calls:
        parts: list[str] = []
        prev = 0
        for s, e in sorted(spans_to_remove):
            parts.append(text[prev:s])
            prev = e
        parts.append(text[prev:])
        clean_text = "".join(parts).strip()
    else:
        clean_text = text

    return clean_text, tool_calls


# ── Dot-style 格式 (.tool_name(kwargs)) ──────────────────

_KNOWN_TOOL_NAMES: set[str] = set()
"""由 convert_tools_to_openai / convert_tools_to_responses 自动填充。

每次发起 LLM 请求时，传入的工具定义会自动注册到此集合。
解析 LLM 回复中的文本工具调用时，只接受集合内的工具名。
如果需要手动注册，请调用 register_tool_names()。
"""


def register_tool_names(names: Iterable[str]) -> None:
    """手动注册工具名到文本工具调用解析的白名单中。"""
    _KNOWN_TOOL_NAMES.update(names)

_DOT_STYLE_RE = re.compile(r"\.([a-z][a-z0-9_]{2,})\s*\(")


def _find_matching_paren(text: str, start: int) -> int:
    """找到与 start 位置的 '(' 匹配的 ')' 位置，考虑引号内的括号。"""
    if start >= len(text) or text[start] != "(":
        return -1
    depth = 0
    in_single_quote = False
    in_double_quote = False
    escape_next = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape_next:
            escape_next = False
            continue
        if ch == "\\":
            escape_next = True
            continue
        if ch == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
        elif ch == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
        elif not in_single_quote and not in_double_quote:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return i
    return -1


def _parse_python_kwargs(args_str: str) -> dict:
    """将 Python 风格的 kwargs 字符串解析为 dict。"""
    import ast

    args_str = args_str.strip()
    if not args_str:
        return {}
    try:
        tree = ast.parse(f"_f({args_str})", mode="eval")
        call_node = tree.body
        if not isinstance(call_node, ast.Call):
            return {"raw_args": args_str}
        result = {}
        for kw in call_node.keywords:
            if kw.arg is None:
                continue
            try:
                result[kw.arg] = ast.literal_eval(kw.value)
            except (ValueError, TypeError):
                result[kw.arg] = ast.unparse(kw.value)
        return result if result else {"raw_args": args_str}
    except (SyntaxError, ValueError, TypeError):
        return {"raw_args": args_str}


def _parse_dot_style(text: str) -> tuple[str, list[ToolUseBlock]]:
    """解析 .tool_name(kwargs) 格式的工具调用（Qwen 等模型常见）。"""
    tool_calls: list[ToolUseBlock] = []
    spans_to_remove: list[tuple[int, int]] = []

    for m in _DOT_STYLE_RE.finditer(text):
        tool_name = m.group(1)
        if tool_name not in _KNOWN_TOOL_NAMES:
            continue
        paren_start = m.end() - 1
        paren_end = _find_matching_paren(text, paren_start)
        if paren_end < 0:
            continue
        args_str = text[paren_start + 1 : paren_end]
        arguments = _parse_python_kwargs(args_str)
        tool_calls.append(ToolUseBlock(
            id=f"dot_{uuid.uuid4().hex[:12]}",
            name=tool_name,
            input=arguments,
        ))
        spans_to_remove.append((m.start(), paren_end + 1))
        logger.info(
            f"[DOT_TOOL_PARSE] Extracted tool call: {tool_name} "
            f"with args: {list(arguments.keys())}"
        )

    if not tool_calls:
        return text, []

    parts: list[str] = []
    prev = 0
    for s, e in sorted(spans_to_remove):
        parts.append(text[prev:s])
        prev = e
    parts.append(text[prev:])
    return "".join(parts).strip(), tool_calls


# ── 方括号格式 [tool_name(kwargs)] ──────────────────────
#
# 部分模型（如 Qwen3-coder-plus）在不支持原生 function calling 时
# 会将工具调用包裹在方括号中输出：
#   [create_plan(id="my-plan", description="...", steps=[...])]
#   [delegate_to_agent(agent_id="office-doc", message="...")]
#   [list_skills()]
#
# 与 dot_style (.tool_name) 类似，但使用 [ ] 包裹而非 . 前缀。
# 安全保障：必须匹配 _KNOWN_TOOL_NAMES 以避免误识别 Markdown 链接等。

_BRACKET_CALL_RE = re.compile(r"\[([a-z_][a-z0-9_]{2,})\s*\(")


def _parse_bracket_calls(text: str) -> tuple[str, list[ToolUseBlock]]:
    """解析 [tool_name(kwargs)] 格式的工具调用。"""
    tool_calls: list[ToolUseBlock] = []
    spans_to_remove: list[tuple[int, int]] = []

    for m in _BRACKET_CALL_RE.finditer(text):
        tool_name = m.group(1)
        if tool_name not in _KNOWN_TOOL_NAMES:
            continue

        paren_start = m.end() - 1
        paren_end = _find_matching_paren(text, paren_start)
        if paren_end < 0:
            continue

        # ')' 后必须紧跟 ']'（允许空白），否则不是工具调用
        closing_bracket = -1
        for i in range(paren_end + 1, min(paren_end + 6, len(text))):
            if text[i] == "]":
                closing_bracket = i
                break
            if text[i] not in " \t\n\r":
                break
        if closing_bracket < 0:
            continue

        # 排除 Markdown 链接 [text](url)：']' 后紧跟 '(' 说明是链接而非工具调用
        after_bracket = closing_bracket + 1
        if after_bracket < len(text) and text[after_bracket] == "(":
            continue

        args_str = text[paren_start + 1 : paren_end]
        arguments = _parse_python_kwargs(args_str)

        tool_calls.append(ToolUseBlock(
            id=f"bracket_{uuid.uuid4().hex[:12]}",
            name=tool_name,
            input=arguments,
        ))
        spans_to_remove.append((m.start(), closing_bracket + 1))
        logger.info(
            f"[BRACKET_TOOL_PARSE] Extracted tool call: {tool_name} "
            f"with args: {list(arguments.keys())}"
        )

    if not tool_calls:
        return text, []

    parts: list[str] = []
    prev = 0
    for s, e in sorted(spans_to_remove):
        parts.append(text[prev:s])
        prev = e
    parts.append(text[prev:])
    return "".join(parts).strip(), tool_calls


# ── 围栏代码块格式 ```json { function_call } ``` ─────────
#
# 部分模型将工具调用放入 Markdown 围栏代码块中，常见两种变体：
#
# 变体 1（OpenAI 风格）:
#   ```json
#   {"type": "function_call", "function_call": {"name": "xxx", "arguments": "..."}}
#   ```
#
# 变体 2（简化风格）:
#   ```json
#   {"function": "xxx", "params": {"key": "value"}}
#   ```
#
# 安全保障：
# - 必须在围栏代码块内
# - JSON 必须包含特征字段组合（type+function_call / function+params）
# - 工具名必须在 _KNOWN_TOOL_NAMES 中

_FENCED_FUNC_DETECT_RE = re.compile(
    r"```(?:json)?\s*\n\s*\{.*?\"(?:function_call|function)\"\s*:",
    re.DOTALL,
)
_FENCED_CODE_BLOCK_RE = re.compile(
    r"```(?:json)?\s*\n(.*?)\n\s*```",
    re.DOTALL,
)


def _parse_fenced_json_tool_calls(text: str) -> tuple[str, list[ToolUseBlock]]:
    """解析围栏代码块中的 JSON 格式工具调用。"""
    tool_calls: list[ToolUseBlock] = []
    spans_to_remove: list[tuple[int, int]] = []

    for m in _FENCED_CODE_BLOCK_RE.finditer(text):
        json_str = m.group(1).strip()
        if not json_str.startswith("{"):
            continue
        try:
            obj = json.loads(json_str)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue

        tool_name: str | None = None
        arguments: dict | None = None

        # 变体 1: {"type": "function_call", "function_call": {"name": ..., "arguments": ...}}
        # 也兼容 "function" 作为内层键名
        if obj.get("type") == "function_call":
            fc = obj.get("function_call") or obj.get("function")
            if isinstance(fc, dict) and fc.get("name"):
                tool_name = fc["name"]
                args = fc.get("arguments", "{}")
                if isinstance(args, str):
                    try:
                        arguments = json.loads(args)
                    except json.JSONDecodeError:
                        arguments = {"raw_args": args}
                elif isinstance(args, dict):
                    arguments = args
                else:
                    arguments = {}

        # 变体 2: {"function": "xxx", "params": {...}}
        if tool_name is None and isinstance(obj.get("function"), str) and "params" in obj:
            tool_name = obj["function"]
            params = obj["params"]
            if isinstance(params, str):
                try:
                    arguments = json.loads(params)
                except json.JSONDecodeError:
                    arguments = {"raw_args": params}
            elif isinstance(params, dict):
                arguments = params
            else:
                arguments = {}

        if not tool_name or tool_name not in _KNOWN_TOOL_NAMES or arguments is None:
            continue

        tool_calls.append(ToolUseBlock(
            id=f"fenced_{uuid.uuid4().hex[:12]}",
            name=tool_name,
            input=arguments,
        ))
        spans_to_remove.append((m.start(), m.end()))
        logger.info(
            f"[FENCED_TOOL_PARSE] Extracted tool call: {tool_name} "
            f"with args: {list(arguments.keys())}"
        )

    if not tool_calls:
        return text, []

    parts: list[str] = []
    prev = 0
    for s, e in sorted(spans_to_remove):
        parts.append(text[prev:s])
        prev = e
    parts.append(text[prev:])
    return "".join(parts).strip(), tool_calls


# ── 格式注册表 + 公开 API ─────────────────────────────
#
# 顺序有意义：JSON 放最后，因为其检测 pattern 最宽泛。
# 前面的格式使用精确的 XML 标签匹配，不会误报。

_TEXT_TOOL_FORMATS: list[_TextToolFormat] = [
    _TextToolFormat(
        "function_calls",
        re.compile(r"<function_calls>", re.IGNORECASE),
        _make_invoke_wrapper_parser("<function_calls>", "</function_calls>"),
    ),
    _TextToolFormat(
        "minimax",
        re.compile(r"<minimax:tool_call>", re.IGNORECASE),
        _make_invoke_wrapper_parser("<minimax:tool_call>", "</minimax:tool_call>"),
    ),
    _TextToolFormat(
        "kimi_k2",
        re.compile(r"<<\|tool_calls_section_begin\|>>"),
        _parse_kimi_k2,
    ),
    _TextToolFormat(
        "glm",
        re.compile(r"<tool_call>", re.IGNORECASE),
        _parse_glm,
    ),
    _TextToolFormat(
        "tool_call_tag",
        _TOOL_CALL_TAG_DETECT_RE,
        _parse_tool_call_tags,
    ),
    # ↓ 以下为 fallback 格式，仅当上方精确格式未匹配时才尝试
    _TextToolFormat(
        "fenced_json",
        _FENCED_FUNC_DETECT_RE,
        _parse_fenced_json_tool_calls,
        fallback=True,
    ),
    _TextToolFormat(
        "bracket_call",
        _BRACKET_CALL_RE,
        _parse_bracket_calls,
        fallback=True,
    ),
    _TextToolFormat(
        "dot_style",
        _DOT_STYLE_RE,
        _parse_dot_style,
        fallback=True,
    ),
    _TextToolFormat(
        "json",
        _JSON_TOOL_CALL_HEADER_RE,
        _parse_json_tool_calls,
        fallback=True,
    ),
]


def has_text_tool_calls(text: str) -> bool:
    """检查文本中是否包含文本格式的工具调用。"""
    return any(fmt.detect_re.search(text) for fmt in _TEXT_TOOL_FORMATS)


def parse_text_tool_calls(text: str) -> tuple[str, list[ToolUseBlock]]:
    """从文本中解析工具调用（降级方案）。

    当 LLM 不支持原生工具调用或偶尔退化为文本格式时，
    遍历所有已注册的格式解析器，提取工具调用并清理残留标记。

    Args:
        text: LLM 返回的文本内容

    Returns:
        (clean_text, tool_calls): 清理后的文本和解析出的工具调用列表
    """
    all_tools: list[ToolUseBlock] = []
    clean = text
    for fmt in _TEXT_TOOL_FORMATS:
        if fmt.fallback and all_tools:
            continue
        if fmt.detect_re.search(clean):
            clean, tools = fmt.parse(clean)
            if tools:
                all_tools.extend(tools)
                logger.info(
                    f"[TEXT_TOOL_PARSE] {fmt.name}: extracted {len(tools)} tool calls"
                )
    return clean, all_tools


# ── Responses API 格式转换 ──────────────────────────────────
#
# OpenAI Responses API 使用 internally-tagged 格式，与 Chat Completions
# 的 externally-tagged 格式不同。以下函数仅在 api_type="openai_responses"
# 的端点中使用，不影响现有 Chat Completions 路径。


def convert_tools_to_responses(tools: list[Tool]) -> list[dict]:
    """将内部工具定义转换为 Responses API 格式。

    Chat Completions: {"type": "function", "function": {"name", "description", "parameters"}}
    Responses API:    {"type": "function", "name", "description", "parameters", "strict": true}
    """
    _KNOWN_TOOL_NAMES.update(t.name for t in tools)
    return [
        {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        }
        for tool in tools
    ]


def convert_tool_calls_from_responses(items: list[dict]) -> list[ToolUseBlock]:
    """从 Responses API output items 中提取工具调用。

    Responses 格式:
    {"type": "function_call", "id": ..., "call_id": ..., "name": ..., "arguments": "..."}
    """
    result = []
    for item in items:
        if item.get("type") != "function_call":
            continue
        arguments = item.get("arguments", "{}")
        if isinstance(arguments, str):
            try:
                input_dict = json.loads(arguments)
            except json.JSONDecodeError:
                tool_name = item.get("name", "?")
                repaired = _try_repair_json(arguments)
                _dump_raw_arguments(tool_name, arguments)
                if repaired is not None:
                    err_msg = (
                        f"❌ 工具 '{tool_name}' 的参数 JSON 被 API 截断后自动修复，"
                        f"但内容可能不完整。请缩短参数后重试。"
                    )
                    input_dict = {PARSE_ERROR_KEY: err_msg}
                else:
                    err_msg = (
                        f"❌ 工具 '{tool_name}' 的参数 JSON 被 API 截断且无法修复。"
                        "请缩短参数后重试。"
                    )
                    input_dict = {PARSE_ERROR_KEY: err_msg}
        else:
            input_dict = arguments

        result.append(
            ToolUseBlock(
                id=item.get("call_id") or item.get("id", ""),
                name=item.get("name", ""),
                input=input_dict,
            )
        )
    return result


def convert_tool_result_to_responses(call_id: str, content: str) -> dict:
    """将工具执行结果转换为 Responses API 的 function_call_output item。

    Chat Completions: {"role": "tool", "tool_call_id": ..., "content": ...}
    Responses API:    {"type": "function_call_output", "call_id": ..., "output": ...}
    """
    return {
        "type": "function_call_output",
        "call_id": call_id,
        "output": content,
    }

