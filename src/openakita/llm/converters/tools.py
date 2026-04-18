"""
Tool call format converters.

Converts tool definitions and calls between the internal format (Anthropic-like)
and the OpenAI format. Also supports parsing tool calls from text (fallback).
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

# Marker key written into input when JSON parsing fails, intercepted by ToolExecutor
PARSE_ERROR_KEY = "__parse_error__"


def _try_repair_json(s: str) -> dict | None:
    """Attempt to repair a truncated JSON string.

    When LLMs generate very long tool_call arguments, the API may truncate
    the JSON, causing json.loads to fail. This function attempts simple fixes:
    - Close missing quotes
    - Close missing braces
    Returns None if repair fails.
    """
    s = s.strip()
    if not s:
        return None

    if not s.startswith("{"):
        return None

    for suffix in ['"}', '"}}', '"}}}}', '"}]}', '"]}', '"}', "}", "}}", "}}}"]:
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
    """Write the raw unparseable arguments to a diagnostic file for debugging truncation."""
    try:
        from datetime import datetime

        debug_dir = Path("data/llm_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        dump_file = debug_dir / f"truncated_args_{tool_name}_{ts}.txt"
        dump_file.write_text(arguments, encoding="utf-8")
        logger.info(
            f"[TOOL_CALL] Raw truncated arguments ({len(arguments)} chars) saved to {dump_file}"
        )
    except Exception as exc:
        logger.warning(f"[TOOL_CALL] Failed to dump raw arguments: {exc}")


# ── OpenAI Chat Completions format conversion ────────────


def convert_tools_to_anthropic(tools: list[Tool]) -> list[dict]:
    """Convert internal tool definitions to Anthropic format (internal is already Anthropic-like)."""
    _KNOWN_TOOL_NAMES.update(t.name for t in tools)
    return [tool.to_dict() for tool in tools]


def convert_tools_to_openai(tools: list[Tool]) -> list[dict]:
    """Convert internal tool definitions to OpenAI format."""
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
    """Convert OpenAI tool definitions to the internal format."""
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
    """Convert OpenAI tool calls to the internal format.

    OpenAI format:
    {
        "id": "call_xxx",
        "type": "function",
        "function": {
            "name": "get_weather",
            "arguments": "{\"location\": \"Beijing\"}"  # JSON string
        }
    }

    Internal format:
    {
        "type": "tool_use",
        "id": "call_xxx",
        "name": "get_weather",
        "input": {"location": "Beijing"}  # JSON object
    }
    """
    result = []
    for tc in tool_calls:
        # Compatibility: some OpenAI-compatible gateways omit tc.type but still provide function{name,arguments}
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
                            f"❌ The argument JSON for tool '{tool_name}' was auto-repaired after API truncation, "
                            f"but content may be incomplete (recovered keys: {recovered_keys}).\n"
                            f"Original argument length: {arg_len} characters.\n"
                            "Please shorten the arguments and retry:\n"
                            "- write_file / edit_file: split large files into multiple smaller writes\n"
                            "- Other tools: trim arguments, avoid embedding very long text"
                        )
                        input_dict = {PARSE_ERROR_KEY: err_msg}
                        logger.warning(
                            f"[TOOL_CALL] JSON repair succeeded for tool '{tool_name}' "
                            f"(recovered keys: {recovered_keys}), treating as truncation "
                            f"error. Raw args ({arg_len} chars) dumped to data/llm_debug/."
                        )
                        # After write_file truncation repair, if 'path' is missing, inject a truncation hint instead of passing incomplete args
                        if (
                            tool_name == "write_file"
                            and "content" in input_dict
                            and "path" not in input_dict
                        ):
                            content_len = len(str(input_dict.get("content", "")))
                            logger.warning(
                                f"[TOOL_CALL] write_file JSON repaired but 'path' is missing "
                                f"(content length={content_len}). Likely truncated by output token limit."
                            )
                            input_dict = {
                                PARSE_ERROR_KEY: (
                                    f"⚠️ Your write_file call was truncated by the API because the content was too long ({content_len} characters), "
                                    f"and the 'path' argument was lost. Try one of the following:\n"
                                    "1. Split the large content into multiple smaller writes (each < 8000 characters)\n"
                                    "2. Or use run_shell + a Python script to generate the large file\n"
                                    "3. Write a skeleton file first, then fill in the content with multiple appends"
                                )
                            }
                    else:
                        err_msg = (
                            f"❌ The argument JSON for tool '{tool_name}' was truncated by the API and could not be repaired"
                            f" (total {arg_len} characters).\n"
                            "Please shorten the arguments and retry:\n"
                            "- write_file / edit_file: split large files into multiple smaller writes\n"
                            "- Other tools: trim arguments, avoid embedding very long text"
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
    """Convert internal tool calls to OpenAI format."""
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
    """Convert a tool result to an OpenAI-format message."""
    return {
        "role": "tool",
        "tool_call_id": tool_use_id,
        "content": content,
    }


def convert_tool_result_from_openai(msg: dict) -> dict | None:
    """Convert an OpenAI tool result message to the internal format."""
    if msg.get("role") != "tool":
        return None

    return {
        "type": "tool_result",
        "tool_use_id": msg.get("tool_call_id", ""),
        "content": msg.get("content", ""),
    }


# ── Text-format tool call parsing (fallback) ────────────
#
# Registry-driven: each format is described by _TextToolFormat(name, detect_re, parse).
# The parse function receives the full text and returns (cleaned_text, tool_call_list).
# Parsing and cleanup happen in the same function, eliminating sync risks.
# To add a new format, register an entry and provide a parse function.


@dataclass(frozen=True)
class _TextToolFormat:
    """Describes a single text-format tool call spec."""

    name: str
    detect_re: re.Pattern
    parse: Callable[[str], tuple[str, list[ToolUseBlock]]]
    fallback: bool = False


# ── Shared: <invoke> block parser ────────────────────────


def _parse_invoke_blocks(content: str) -> list[ToolUseBlock]:
    """Parse tool calls from <invoke> blocks (shared by several XML wrapper formats)."""
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
            f"[TEXT_TOOL_PARSE] Extracted tool call: {tool_name} with params: {list(params.keys())}"
        )

    return tool_calls


def _make_invoke_wrapper_parser(
    open_tag: str,
    close_tag: str,
) -> Callable[[str], tuple[str, list[ToolUseBlock]]]:
    """Create a parser for XML wrapper formats built around <invoke> blocks.

    function_calls and minimax:tool_call share the same structure (both wrap
    <invoke> blocks); only the outer tag differs, so this factory generates them.
    """
    _open_esc = re.escape(open_tag)
    _close_esc = re.escape(close_tag)
    _complete_re = re.compile(
        f"{_open_esc}\\s*(.*?)\\s*{_close_esc}",
        re.DOTALL | re.IGNORECASE,
    )
    _incomplete_re = re.compile(
        f"{_open_esc}\\s*(.*?)$",
        re.DOTALL | re.IGNORECASE,
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


# ── Kimi K2 format ────────────────────────────────────


def _parse_kimi_k2(text: str) -> tuple[str, list[ToolUseBlock]]:
    """Parse tool calls in the Kimi K2 format.

    Format:
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

            tool_calls.append(
                ToolUseBlock(
                    id=f"kimi_call_{tool_id.replace('.', '_').replace(':', '_')}",
                    name=func_name,
                    input=arguments,
                )
            )
            logger.info(
                f"[KIMI_TOOL_PARSE] Extracted tool call: {func_name} "
                f"with args: {list(arguments.keys())}"
            )

    if not tool_calls:
        return text, []

    clean = re.sub(
        r"<<\|tool_calls_section_begin\|>>.*?<<\|tool_calls_section_end\|>>",
        "",
        text,
        flags=re.DOTALL,
    ).strip()
    clean = re.sub(
        r"<<\|tool_calls_section_begin\|>>.*$",
        "",
        clean,
        flags=re.DOTALL,
    ).strip()
    return clean, tool_calls


# ── <tool_call><function=...> format ──────────────────────
#
# Some models output tool calls in this format:
# <tool_call>
# <function=tool_name>
# <parameter=key>value</parameter>
# </function>
# </tool_call>

_FUNC_PARAM_COMPLETE_RE = re.compile(
    r"<tool_call>\s*(.*?)\s*</tool_call>",
    re.DOTALL | re.IGNORECASE,
)
_FUNC_PARAM_INCOMPLETE_RE = re.compile(
    r"<tool_call>\s*(.*?)$",
    re.DOTALL | re.IGNORECASE,
)
_FUNC_NAME_RE = re.compile(
    r"<function=([^>]+)>",
    re.IGNORECASE,
)
_FUNC_PARAM_RE = re.compile(
    r"<parameter=([^>]+)>(.*?)</parameter>",
    re.DOTALL | re.IGNORECASE,
)


def _parse_function_param(text: str) -> tuple[str, list[ToolUseBlock]]:
    """Parse the <tool_call><function=name><parameter=key>value</parameter></function></tool_call> format."""
    blocks = _FUNC_PARAM_COMPLETE_RE.findall(text) or _FUNC_PARAM_INCOMPLETE_RE.findall(text)
    if not blocks:
        return text, []

    has_func_tag = any(_FUNC_NAME_RE.search(b) for b in blocks)
    if not has_func_tag:
        return text, []

    tool_calls: list[ToolUseBlock] = []
    for body in blocks:
        fn_match = _FUNC_NAME_RE.search(body)
        if not fn_match:
            continue
        tool_name = fn_match.group(1).strip()

        params: dict = {}
        for pm in _FUNC_PARAM_RE.finditer(body):
            key = pm.group(1).strip()
            val = pm.group(2).strip()
            try:
                params[key] = json.loads(val)
            except (json.JSONDecodeError, ValueError):
                params[key] = val

        tool_calls.append(
            ToolUseBlock(
                id=f"func_param_{uuid.uuid4().hex[:8]}",
                name=tool_name,
                input=params,
            )
        )
        logger.info(
            f"[FUNC_PARAM_PARSE] Extracted tool call: {tool_name} "
            f"with params: {list(params.keys())}"
        )

    clean = _FUNC_PARAM_COMPLETE_RE.sub("", text).strip()
    clean = _FUNC_PARAM_INCOMPLETE_RE.sub("", clean).strip()
    return clean, tool_calls


# ── GLM format ────────────────────────────────────────

_GLM_COMPLETE_RE = re.compile(
    r"<tool_call>\s*(.*?)\s*</tool_call>",
    re.DOTALL | re.IGNORECASE,
)
_GLM_INCOMPLETE_RE = re.compile(
    r"<tool_call>\s*(.*?)$",
    re.DOTALL | re.IGNORECASE,
)
_GLM_KV_RE = re.compile(
    r"<arg_key>\s*(.*?)\s*</arg_key>\s*<arg_value>\s*(.*?)\s*</arg_value>",
    re.DOTALL,
)


def _parse_glm(text: str) -> tuple[str, list[ToolUseBlock]]:
    """Parse the <tool_call> format used by GLM models.

    Format:
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

        tool_calls.append(
            ToolUseBlock(
                id=f"glm_call_{uuid.uuid4().hex[:8]}",
                name=tool_name,
                input=params,
            )
        )
        logger.info(
            f"[GLM_TOOL_PARSE] Extracted tool call: {tool_name} with params: {list(params.keys())}"
        )

    # Strip the tags even if no tool was extracted, to prevent raw tags leaking into the UI
    clean = _GLM_COMPLETE_RE.sub("", text).strip()
    clean = _GLM_INCOMPLETE_RE.sub("", clean).strip()
    return clean, tool_calls


# ── [TOOL_CALL] tag format ────────────────────────────────
#
# Models such as kimi-k2-thinking wrap tool calls in [TOOL_CALL]...[/TOOL_CALL] tags.
# The inner format is not fixed; the following variants have been observed:
#
# A. arrow + --keys:
#    [TOOL_CALL] {tool => "web_search", "args": {--query "test", --max_results 10}}[/TOOL_CALL]
# B. standard JSON:
#    [TOOL_CALL] { "tool": "get_org", "args": { "id": "abc" } } [/TOOL_CALL]
# C. equals syntax:
#    [TOOL_CALL] {tool = "setup_organization", args = {"action": "get_org"}}[/TOOL_CALL]
# D. compact multi-line JSON:
#    [TOOL_CALL]{ "tool": "name", "args": {...} }[/TOOL_CALL]
#
# The closing tag may be [/TOOL_CALL], </invoke>, or missing entirely.

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
    """Find the '}' that matches the '{' at start, correctly skipping braces inside quoted strings."""
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
    """Extract tool name and arguments from an already-parsed dict."""
    name = obj.get("tool") or obj.get("name") or obj.get("function")
    if not name or not isinstance(name, str):
        return None
    args = (
        obj.get("args") or obj.get("arguments") or obj.get("parameters") or obj.get("input") or {}
    )
    return name, args if isinstance(args, dict) else {}


def _normalize_tag_body(body: str) -> str:
    """Normalize arrow/equals/--key syntax into a JSON-compatible form."""
    s = body
    s = re.sub(r"(\w+)\s*=>\s*", r'"\1": ', s)
    s = re.sub(r"(\w+)\s*=\s*(?=[\"'{[\d])", r'"\1": ', s)
    s = re.sub(r"--(\w+)\s+", r'"\1": ', s)
    return s


def _parse_tag_args_block(body: str) -> dict:
    """Extract the args section from a [TOOL_CALL] body and try to parse it as a dict."""
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
    """Parse the contents inside a [TOOL_CALL] tag and extract tool name and arguments."""
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
    """Parse tool calls in the [TOOL_CALL]...[/TOOL_CALL] format."""
    tool_calls: list[ToolUseBlock] = []
    spans_to_remove: list[tuple[int, int]] = []

    for m in _TOOL_CALL_TAG_BLOCK_RE.finditer(text):
        result = _parse_tool_call_tag_body(m.group(1))
        if result:
            name, args = result
            tool_calls.append(
                ToolUseBlock(
                    id=f"tag_call_{uuid.uuid4().hex[:12]}",
                    name=name,
                    input=args,
                )
            )
            spans_to_remove.append((m.start(), m.end()))

    if not tool_calls:
        for m in _TOOL_CALL_TAG_UNCLOSED_RE.finditer(text):
            result = _parse_tool_call_tag_body(m.group(1))
            if result:
                name, args = result
                tool_calls.append(
                    ToolUseBlock(
                        id=f"tag_call_{uuid.uuid4().hex[:12]}",
                        name=name,
                        input=args,
                    )
                )
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


# ── JSON tool call detection and parsing ────────────────
# Some models (e.g., Qwen 2.5) emit tool calls as raw JSON in the text
# response during failover rather than going through structured tool_use.
# Typical formats:
#   {{"name": "browser_open", "arguments": {"visible": true}}}
#   {"name": "web_search", "arguments": {"query": "test"}}

_JSON_TOOL_CALL_HEADER_RE = re.compile(
    r'\{+\s*"name"\s*:\s*"([a-z_][a-z0-9_]*)"\s*,\s*"arguments"\s*:\s*',
)


def _extract_balanced_braces(text: str, start: int) -> str | None:
    """Extract a brace-balanced JSON object starting from the ``{`` at position start."""
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
    """Extract JSON-format tool calls from text.

    Matches {"name": "xxx", "arguments": {...}} or double-braced variants.
    Uses brace counting to correctly handle deeply nested argument JSON.
    Returns (cleaned_text, tool_call_list).
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
                    f"❌ The argument JSON for tool '{tool_name}' was auto-repaired after truncation, "
                    f"but content may be incomplete (recovered keys: {recovered_keys}).\n"
                    f"Original argument length: {arg_len} characters.\n"
                    "Please shorten the arguments and retry:\n"
                    "- write_file / edit_file: split large files into multiple smaller writes\n"
                    "- Other tools: trim arguments, avoid embedding very long text"
                )
                arguments = {PARSE_ERROR_KEY: err_msg}
                logger.warning(
                    f"[JSON_TOOL_PARSE] JSON repair succeeded for '{tool_name}' "
                    f"(recovered keys: {recovered_keys}), treating as truncation. "
                    f"Raw args ({arg_len} chars) dumped."
                )
            else:
                err_msg = (
                    f"❌ The argument JSON for tool '{tool_name}' was truncated and could not be repaired"
                    f" (total {arg_len} characters).\n"
                    "Please shorten the arguments and retry:\n"
                    "- write_file / edit_file: split large files into multiple smaller writes\n"
                    "- Other tools: trim arguments, avoid embedding very long text"
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


# ── Dot-style format (.tool_name(kwargs)) ────────────────

_KNOWN_TOOL_NAMES: set[str] = set()
"""Auto-populated by convert_tools_to_openai / convert_tools_to_responses.

Tool definitions passed to each LLM request are auto-registered into this set.
When parsing text-format tool calls from LLM responses, only names in this set
are accepted. To register manually, call register_tool_names().
"""


def register_tool_names(names: Iterable[str]) -> None:
    """Manually register tool names into the text-tool-call parsing allowlist."""
    _KNOWN_TOOL_NAMES.update(names)


_DOT_STYLE_RE = re.compile(r"\.([a-z][a-z0-9_]{2,})\s*\(")


def _find_matching_paren(text: str, start: int) -> int:
    """Find the ')' that matches the '(' at position start, accounting for parens inside quoted strings."""
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
    """Parse a Python-style kwargs string into a dict."""
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
    """Parse tool calls in the .tool_name(kwargs) format (common for Qwen and similar models)."""
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
        tool_calls.append(
            ToolUseBlock(
                id=f"dot_{uuid.uuid4().hex[:12]}",
                name=tool_name,
                input=arguments,
            )
        )
        spans_to_remove.append((m.start(), paren_end + 1))
        logger.info(
            f"[DOT_TOOL_PARSE] Extracted tool call: {tool_name} with args: {list(arguments.keys())}"
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


# ── Bracket format [tool_name(kwargs)] ──────────────────
#
# Some models (e.g., Qwen3-coder-plus), when native function calling is unavailable,
# wrap tool calls in square brackets:
#   [create_plan(id="my-plan", description="...", steps=[...])]
#   [delegate_to_agent(agent_id="office-doc", message="...")]
#   [list_skills()]
#
# Similar to dot_style (.tool_name), but wrapped in [ ] rather than prefixed with ".".
# Safety: must match _KNOWN_TOOL_NAMES to avoid misidentifying Markdown links, etc.

_BRACKET_CALL_RE = re.compile(r"\[([a-z_][a-z0-9_]{2,})\s*\(")


def _parse_bracket_calls(text: str) -> tuple[str, list[ToolUseBlock]]:
    """Parse tool calls in the [tool_name(kwargs)] format."""
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

        # ')' must be followed immediately by ']' (whitespace allowed); otherwise it's not a tool call
        closing_bracket = -1
        for i in range(paren_end + 1, min(paren_end + 6, len(text))):
            if text[i] == "]":
                closing_bracket = i
                break
            if text[i] not in " \t\n\r":
                break
        if closing_bracket < 0:
            continue

        # Exclude Markdown links [text](url): ']' immediately followed by '(' means it's a link, not a tool call
        after_bracket = closing_bracket + 1
        if after_bracket < len(text) and text[after_bracket] == "(":
            continue

        args_str = text[paren_start + 1 : paren_end]
        arguments = _parse_python_kwargs(args_str)

        tool_calls.append(
            ToolUseBlock(
                id=f"bracket_{uuid.uuid4().hex[:12]}",
                name=tool_name,
                input=arguments,
            )
        )
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


# ── Fenced code block format ```json { function_call } ``` ──
#
# Some models emit tool calls inside Markdown fenced code blocks. Two common variants:
#
# Variant 1 (OpenAI style):
#   ```json
#   {"type": "function_call", "function_call": {"name": "xxx", "arguments": "..."}}
#   ```
#
# Variant 2 (simplified style):
#   ```json
#   {"function": "xxx", "params": {"key": "value"}}
#   ```
#
# Safety:
# - Must be inside a fenced code block
# - JSON must contain the characteristic field combination (type+function_call / function+params)
# - Tool name must be in _KNOWN_TOOL_NAMES

_FENCED_FUNC_DETECT_RE = re.compile(
    r"```(?:json)?\s*\n\s*\{.*?\"(?:function_call|function)\"\s*:",
    re.DOTALL,
)
_FENCED_CODE_BLOCK_RE = re.compile(
    r"```(?:json)?\s*\n(.*?)\n\s*```",
    re.DOTALL,
)


def _parse_fenced_json_tool_calls(text: str) -> tuple[str, list[ToolUseBlock]]:
    """Parse JSON-format tool calls inside fenced code blocks."""
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

        # Variant 1: {"type": "function_call", "function_call": {"name": ..., "arguments": ...}}
        # Also accepts "function" as the inner key name
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

        # Variant 2: {"function": "xxx", "params": {...}}
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

        tool_calls.append(
            ToolUseBlock(
                id=f"fenced_{uuid.uuid4().hex[:12]}",
                name=tool_name,
                input=arguments,
            )
        )
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


# ── Format registry + public API ─────────────────────────
#
# Order matters: JSON comes last because its detection pattern is the broadest.
# Preceding formats use exact XML tag matching and won't produce false positives.

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
        "func_param",
        re.compile(r"<tool_call>", re.IGNORECASE),
        _parse_function_param,
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
    # ↓ The following are fallback formats, tried only when the precise formats above didn't match
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
    """Check whether the text contains any text-format tool calls."""
    return any(fmt.detect_re.search(text) for fmt in _TEXT_TOOL_FORMATS)


def parse_text_tool_calls(text: str) -> tuple[str, list[ToolUseBlock]]:
    """Parse tool calls from text (fallback path).

    When the LLM doesn't support native tool calls or occasionally falls back
    to text format, iterate over all registered format parsers, extracting
    tool calls and cleaning up residual markers.

    Args:
        text: The text content returned by the LLM.

    Returns:
        (clean_text, tool_calls): the cleaned text and the list of parsed tool calls.
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
                logger.info(f"[TEXT_TOOL_PARSE] {fmt.name}: extracted {len(tools)} tool calls")
    return clean, all_tools


# ── Responses API format conversion ─────────────────────────
#
# The OpenAI Responses API uses an internally-tagged format, different from
# the externally-tagged format used by Chat Completions. The functions below
# are only used by endpoints with api_type="openai_responses" and do not
# affect the existing Chat Completions path.


def convert_tools_to_responses(tools: list[Tool]) -> list[dict]:
    """Convert internal tool definitions to the Responses API format.

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
    """Extract tool calls from Responses API output items.

    Responses format:
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
                        f"❌ The argument JSON for tool '{tool_name}' was auto-repaired after API truncation, "
                        f"but content may be incomplete. Please shorten the arguments and retry."
                    )
                    input_dict = {PARSE_ERROR_KEY: err_msg}
                else:
                    err_msg = (
                        f"❌ The argument JSON for tool '{tool_name}' was truncated by the API and could not be repaired. "
                        "Please shorten the arguments and retry."
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
    """Convert a tool execution result into a Responses API function_call_output item.

    Chat Completions: {"role": "tool", "tool_call_id": ..., "content": ...}
    Responses API:    {"type": "function_call_output", "call_id": ..., "output": ...}
    """
    return {
        "type": "function_call_output",
        "call_id": call_id,
        "output": content,
    }
