"""
工具调用格式转换器

负责在内部格式（Anthropic-like）和 OpenAI 格式之间转换工具定义和调用。
支持文本格式工具调用解析（降级方案）。
"""

import json
import logging
import re
import uuid
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

    # 确保以 { 开头
    if not s.startswith("{"):
        return None

    # 尝试逐步补齐
    for suffix in ['"}', '"}}', '"}}}}', '"}]}'  , '"]}'  , '"}'  , '}', '}}', '}}}']:
        try:
            result = json.loads(s + suffix)
            if isinstance(result, dict):
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


def convert_tools_to_openai(tools: list[Tool]) -> list[dict]:
    """
    将内部工具定义转换为 OpenAI 格式

    内部格式:
    {
        "name": "get_weather",
        "description": "获取天气",
        "input_schema": {"type": "object", "properties": {...}}
    }

    OpenAI 格式:
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "获取天气",
            "parameters": {"type": "object", "properties": {...}}
        }
    }
    """
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
    """
    将 OpenAI 工具定义转换为内部格式
    """
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
    """
    将 OpenAI 工具调用转换为内部格式

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

            # 解析 arguments（JSON 字符串 -> dict）
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
                    # 尝试修复截断的 JSON（补齐缺少的引号和括号）
                    input_dict = _try_repair_json(arguments)
                    if input_dict is not None:
                        logger.info(
                            f"[TOOL_CALL] JSON repair succeeded for tool '{tool_name}' "
                            f"(recovered {len(input_dict)} keys)"
                        )
                    else:
                        # ★ 不再静默回退为 {}。
                        # 将错误信息放入 input，由 ToolExecutor 拦截并
                        # 返回给 LLM，让模型知道参数被截断，可自行重试。
                        _dump_raw_arguments(tool_name, arguments)
                        err_msg = (
                            f"⚠️ 你的工具调用 '{tool_name}' 的 JSON 参数被 API 截断或格式错误 "
                            f"(共 {arg_len} 字符)，无法解析。\n"
                            "请缩短参数内容后重试，例如：\n"
                            "- 对于 write_file：将大文件拆分为多次小写入\n"
                            "- 对于其他工具：精简参数，避免在参数中嵌入过长文本"
                        )
                        input_dict = {PARSE_ERROR_KEY: err_msg}
                        logger.error(
                            f"[TOOL_CALL] JSON repair failed for tool '{tool_name}', "
                            f"injecting parse error marker. "
                            f"Raw args ({arg_len} chars) dumped to data/llm_debug/."
                        )
            else:
                input_dict = arguments

            result.append(
                ToolUseBlock(
                    id=tc.get("id", ""),
                    name=func.get("name", ""),
                    input=input_dict,
                )
            )

    return result


def convert_tool_calls_to_openai(tool_uses: list[ToolUseBlock]) -> list[dict]:
    """
    将内部工具调用转换为 OpenAI 格式
    """
    return [
        {
            "id": tu.id,
            "type": "function",
            "function": {
                "name": tu.name,
                "arguments": json.dumps(tu.input, ensure_ascii=False),
            },
        }
        for tu in tool_uses
    ]


def convert_tool_result_to_openai(tool_use_id: str, content: str, is_error: bool = False) -> dict:
    """
    将工具结果转换为 OpenAI 格式消息

    OpenAI 使用独立的 "tool" 角色消息来传递工具结果
    """
    return {
        "role": "tool",
        "tool_call_id": tool_use_id,
        "content": content,
    }


def convert_tool_result_from_openai(msg: dict) -> dict | None:
    """
    将 OpenAI 工具结果消息转换为内部格式
    """
    if msg.get("role") != "tool":
        return None

    return {
        "type": "tool_result",
        "tool_use_id": msg.get("tool_call_id", ""),
        "content": msg.get("content", ""),
    }


def parse_text_tool_calls(text: str) -> tuple[str, list[ToolUseBlock]]:
    """
    从文本中解析工具调用（降级方案）

    当 LLM 不支持原生工具调用时，会以文本格式返回工具调用。
    此函数解析这些文本格式的工具调用。

    支持格式：
    1. <function_calls>...</function_calls> 块
    2. <minimax:tool_call>...</minimax:tool_call> 块（MiniMax 格式）

    Args:
        text: LLM 返回的文本内容

    Returns:
        (clean_text, tool_calls): 清理后的文本和解析出的工具调用列表
    """
    tool_calls = []
    clean_text = text

    # === 格式 1: <function_calls>...</function_calls> ===
    function_calls_pattern = r"<function_calls>\s*(.*?)\s*</function_calls>"
    matches = re.findall(function_calls_pattern, text, re.DOTALL | re.IGNORECASE)

    if not matches:
        # 尝试匹配不完整的格式（没有结束标签）
        function_calls_pattern_incomplete = r"<function_calls>\s*(.*?)$"
        matches = re.findall(function_calls_pattern_incomplete, text, re.DOTALL | re.IGNORECASE)

    for match in matches:
        tool_calls.extend(_parse_invoke_blocks(match))

    # === 格式 2: <minimax:tool_call>...</minimax:tool_call> (MiniMax 格式) ===
    minimax_pattern = r"<minimax:tool_call>\s*(.*?)\s*</minimax:tool_call>"
    minimax_matches = re.findall(minimax_pattern, text, re.DOTALL | re.IGNORECASE)

    if not minimax_matches:
        # 尝试匹配不完整的格式
        minimax_pattern_incomplete = r"<minimax:tool_call>\s*(.*?)$"
        minimax_matches = re.findall(minimax_pattern_incomplete, text, re.DOTALL | re.IGNORECASE)

    for match in minimax_matches:
        tool_calls.extend(_parse_invoke_blocks(match))

    # === 格式 3: <<|tool_calls_section_begin|>>...<<|tool_calls_section_end|>> (Kimi K2 格式) ===
    kimi_tool_calls = _parse_kimi_tool_calls(text)
    tool_calls.extend(kimi_tool_calls)

    # === 格式 4: JSON 格式 {"name": "...", "arguments": {...}} ===
    _json_parsed = False
    if not tool_calls and _has_json_tool_calls(text):
        json_clean, json_tool_calls = _parse_json_tool_calls(text)
        if json_tool_calls:
            tool_calls.extend(json_tool_calls)
            clean_text = json_clean
            _json_parsed = True

    # 清理文本，移除已解析的工具调用（格式 1-3 的清理；格式 4 已在上面处理）
    if tool_calls and not _json_parsed:
        # 移除 function_calls 块
        clean_text = re.sub(
            r"<function_calls>.*?</function_calls>", "", text, flags=re.DOTALL | re.IGNORECASE
        ).strip()

        # 移除不完整的 function_calls 块
        clean_text = re.sub(
            r"<function_calls>.*$", "", clean_text, flags=re.DOTALL | re.IGNORECASE
        ).strip()

        # 移除 minimax:tool_call 块
        clean_text = re.sub(
            r"<minimax:tool_call>.*?</minimax:tool_call>",
            "",
            clean_text,
            flags=re.DOTALL | re.IGNORECASE,
        ).strip()

        # 移除不完整的 minimax:tool_call 块
        clean_text = re.sub(
            r"<minimax:tool_call>.*$", "", clean_text, flags=re.DOTALL | re.IGNORECASE
        ).strip()

        # 移除 Kimi K2 格式的工具调用
        clean_text = re.sub(
            r"<<\|tool_calls_section_begin\|>>.*?<<\|tool_calls_section_end\|>>",
            "",
            clean_text,
            flags=re.DOTALL,
        ).strip()

        # 移除不完整的 Kimi 格式
        clean_text = re.sub(
            r"<<\|tool_calls_section_begin\|>>.*$", "", clean_text, flags=re.DOTALL
        ).strip()

    return clean_text, tool_calls


def _parse_kimi_tool_calls(text: str) -> list[ToolUseBlock]:
    """
    解析 Kimi K2 格式的工具调用

    格式：
    <<|tool_calls_section_begin|>>
    <<|tool_call_begin|>>functions.get_weather:0<<|tool_call_argument_begin|>>{"city": "Beijing"}<<|tool_call_end|>>
    <<|tool_calls_section_end|>>

    Args:
        text: 包含工具调用的文本

    Returns:
        工具调用列表
    """
    tool_calls = []

    # 检查是否包含 Kimi 格式
    if "<<|tool_calls_section_begin|>>" not in text:
        return []

    # 提取工具调用区块
    section_pattern = r"<<\|tool_calls_section_begin\|>>(.*?)<<\|tool_calls_section_end\|>>"
    section_matches = re.findall(section_pattern, text, re.DOTALL)

    if not section_matches:
        # 尝试不完整格式
        section_pattern_incomplete = r"<<\|tool_calls_section_begin\|>>(.*?)$"
        section_matches = re.findall(section_pattern_incomplete, text, re.DOTALL)

    for section in section_matches:
        # 提取每个工具调用
        # 格式: <<|tool_call_begin|>>functions.func_name:idx<<|tool_call_argument_begin|>>{json}<<|tool_call_end|>>
        call_pattern = r"<<\|tool_call_begin\|>>\s*(?P<tool_id>[\w\.]+:\d+)\s*<<\|tool_call_argument_begin\|>>\s*(?P<arguments>.*?)\s*<<\|tool_call_end\|>>"

        for match in re.finditer(call_pattern, section, re.DOTALL):
            tool_id = match.group("tool_id")
            arguments_str = match.group("arguments").strip()

            # 解析函数名: functions.get_weather:0 -> get_weather
            try:
                func_name = tool_id.split(".")[1].split(":")[0]
            except IndexError:
                func_name = tool_id

            # 解析参数
            try:
                arguments = json.loads(arguments_str)
            except json.JSONDecodeError:
                arguments = {"raw": arguments_str}

            tool_call = ToolUseBlock(
                id=f"kimi_call_{tool_id.replace('.', '_').replace(':', '_')}",
                name=func_name,
                input=arguments,
            )
            tool_calls.append(tool_call)
            logger.info(
                f"[KIMI_TOOL_PARSE] Extracted tool call: {func_name} with args: {list(arguments.keys())}"
            )

    return tool_calls


def _parse_invoke_blocks(content: str) -> list[ToolUseBlock]:
    """
    解析 <invoke> 块中的工具调用

    Args:
        content: 包含 <invoke> 块的内容

    Returns:
        工具调用列表
    """
    tool_calls = []

    # 查找 invoke 块
    invoke_pattern = r'<invoke\s+name=["\']?([^"\'>\s]+)["\']?\s*>(.*?)</invoke>'
    invokes = re.findall(invoke_pattern, content, re.DOTALL | re.IGNORECASE)

    if not invokes:
        # 尝试不完整格式
        invoke_pattern_incomplete = (
            r'<invoke\s+name=["\']?([^"\'>\s]+)["\']?\s*>(.*?)(?:</invoke>|$)'
        )
        invokes = re.findall(invoke_pattern_incomplete, content, re.DOTALL | re.IGNORECASE)

    for tool_name, invoke_content in invokes:
        # 解析参数
        params = {}
        param_pattern = r'<parameter\s+name=["\']?([^"\'>\s]+)["\']?\s*>(.*?)</parameter>'
        param_matches = re.findall(param_pattern, invoke_content, re.DOTALL | re.IGNORECASE)

        for param_name, param_value in param_matches:
            # 清理参数值
            param_value = param_value.strip()

            # 尝试解析为 JSON
            try:
                params[param_name] = json.loads(param_value)
            except json.JSONDecodeError:
                params[param_name] = param_value

        # 创建工具调用
        tool_call = ToolUseBlock(
            id=f"text_call_{uuid.uuid4().hex[:8]}",
            name=tool_name.strip(),
            input=params,
        )
        tool_calls.append(tool_call)
        logger.info(
            f"[TEXT_TOOL_PARSE] Extracted tool call: {tool_name} with params: {list(params.keys())}"
        )

    return tool_calls


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


def _has_json_tool_calls(text: str) -> bool:
    """检测文本中是否包含 JSON 格式的工具调用。"""
    return bool(_JSON_TOOL_CALL_HEADER_RE.search(text))


def _parse_json_tool_calls(text: str) -> tuple[str, list[ToolUseBlock]]:
    """
    从文本中提取 JSON 格式工具调用。

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

        # 找到外层闭合花括号（跳过可能的多余 }）
        outer_end = args_start + len(args_str)
        while outer_end < len(text) and text[outer_end] in " \t\n\r}":
            outer_end += 1

        # 向前找外层开头的多余 { 以便整块移除
        outer_start = m.start()
        while outer_start > 0 and text[outer_start - 1] == "{":
            outer_start -= 1

        try:
            arguments = json.loads(args_str)
        except json.JSONDecodeError:
            repaired = _try_repair_json(args_str)
            if repaired is not None:
                arguments = repaired
            else:
                logger.warning(
                    f"[JSON_TOOL_PARSE] Failed to parse arguments for "
                    f"'{tool_name}': {args_str[:120]}"
                )
                arguments = {"raw": args_str}

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


def has_text_tool_calls(text: str) -> bool:
    """
    检查文本中是否包含工具调用格式

    支持检测：
    - <function_calls> 格式（通用）
    - <minimax:tool_call> 格式（MiniMax）
    - <<|tool_calls_section_begin|>> 格式（Kimi K2）
    - JSON 格式: {"name": "tool", "arguments": {...}} 或 {{"name": ...}}
    """
    return bool(
        re.search(r"<function_calls>", text, re.IGNORECASE)
        or re.search(r"<minimax:tool_call>", text, re.IGNORECASE)
        or re.search(r"<<\|tool_calls_section_begin\|>>", text)
        or _has_json_tool_calls(text)
    )
