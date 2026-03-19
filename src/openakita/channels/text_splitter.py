"""
Markdown 感知的文本分块工具

将长回复文本按平台限制拆分为多条消息，同时保持 Markdown 语法完整性：
- 代码块围栏 (```) 不会被拆散到不同消息
- 优先在段落（空行）边界切分
- 超长单段落 / 代码块做二次拆分并补齐围栏
- 提供 UTF-8 字节安全切分（企微等按字节计算长度的平台）
"""

from __future__ import annotations

import re

_RE_FENCE = re.compile(r"^(`{3,}|~{3,})", re.MULTILINE)


def _find_segments(text: str) -> list[str]:
    """将文本拆分为「代码块」和「普通文本」两种 segment。

    保证每个代码块（含围栏行）是一个完整 segment，不会被进一步
    按段落切分时拆散。
    """
    segments: list[str] = []
    pos = 0
    in_fence = False
    fence_marker = ""

    for m in _RE_FENCE.finditer(text):
        marker = m.group(1)
        marker_start = m.start()

        if not in_fence:
            if marker_start > pos:
                segments.append(text[pos:marker_start])
            in_fence = True
            fence_marker = marker[0] * len(marker)
            pos = marker_start
        elif marker[0] == fence_marker[0] and len(marker) >= len(fence_marker):
            line_end = text.find("\n", m.end())
            if line_end == -1:
                line_end = len(text)
            else:
                line_end += 1
            segments.append(text[pos:line_end])
            pos = line_end
            in_fence = False
            fence_marker = ""

    if pos < len(text):
        segments.append(text[pos:])

    return [s for s in segments if s]


def _split_paragraph(text: str, max_length: int) -> list[str]:
    """按段落（双换行）→ 单行 → 字符 三级策略拆分普通文本。"""
    if len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    paragraphs = re.split(r"(\n\s*\n)", text)

    current = ""
    for para in paragraphs:
        candidate = current + para
        if len(candidate) <= max_length:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(para) <= max_length:
            current = para
        else:
            for piece in _split_by_lines(para, max_length):
                chunks.append(piece)

    if current:
        chunks.append(current)
    return chunks


def _split_by_lines(text: str, max_length: int) -> list[str]:
    """按行合并，超长行做字符级截断。"""
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = f"{current}{line}\n" if current else f"{line}\n"
        if len(candidate) <= max_length:
            current = candidate
            continue
        if current:
            chunks.append(current.rstrip("\n"))
            current = ""
        if len(line) + 1 > max_length:
            while line:
                chunks.append(line[:max_length])
                line = line[max_length:]
        else:
            current = line + "\n"
    if current:
        chunks.append(current.rstrip("\n"))
    return chunks


def _split_code_block(segment: str, max_length: int) -> list[str]:
    """拆分超长代码块，为每段补齐围栏行。"""
    lines = segment.split("\n")
    if not lines:
        return [segment]

    opening = lines[0]
    fence_char = opening.lstrip()[0] if opening.strip() else "`"
    fence_len = 0
    for ch in opening.lstrip():
        if ch == fence_char:
            fence_len += 1
        else:
            break
    fence = fence_char * max(fence_len, 3)
    lang_tag = opening.lstrip()[fence_len:].strip()

    closing = ""
    body_lines = lines[1:]
    if body_lines and body_lines[-1].strip().startswith(fence_char * fence_len):
        closing = body_lines[-1]
        body_lines = body_lines[:-1]

    body = "\n".join(body_lines)
    overhead = len(f"{fence} {lang_tag}\n") + len(f"\n{fence}\n") + 2
    inner_max = max(max_length - overhead, max_length // 2)

    body_chunks = _split_by_lines(body, inner_max)

    result: list[str] = []
    for chunk in body_chunks:
        open_line = f"{fence} {lang_tag}".rstrip() if lang_tag else fence
        result.append(f"{open_line}\n{chunk}\n{fence}")
    return result


def chunk_markdown_text(
    text: str,
    max_length: int = 4000,
) -> list[str]:
    """将 Markdown 文本按 max_length 拆分为多条消息。

    - 代码块（fenced code blocks）作为原子单元，不会在围栏中间被拆散
    - 普通文本优先在段落边界（双换行）处拆分
    - 超长代码块会二次拆分并为每段补齐围栏

    Args:
        text: 待拆分的 Markdown 文本
        max_length: 每条消息的最大字符长度

    Returns:
        拆分后的文本列表
    """
    if not text or not text.strip():
        return []
    if max_length <= 0 or len(text) <= max_length:
        return [text]

    segments = _find_segments(text)
    chunks: list[str] = []
    current = ""

    for seg in segments:
        is_code = seg.lstrip().startswith("```") or seg.lstrip().startswith("~~~")

        if is_code:
            if current:
                chunks.extend(_split_paragraph(current, max_length))
                current = ""
            if len(seg) <= max_length:
                chunks.append(seg)
            else:
                chunks.extend(_split_code_block(seg, max_length))
        else:
            candidate = current + seg
            if len(candidate) <= max_length:
                current = candidate
            else:
                if current:
                    chunks.extend(_split_paragraph(current, max_length))
                    current = ""
                if len(seg) <= max_length:
                    current = seg
                else:
                    chunks.extend(_split_paragraph(seg, max_length))

    if current:
        chunks.extend(_split_paragraph(current, max_length))

    return [c for c in chunks if c.strip()]


def utf8_safe_truncate(text: str, max_bytes: int) -> str:
    """将文本截断到不超过 max_bytes 个 UTF-8 字节，保证不截断多字节字符。"""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    truncated = encoded[:max_bytes]
    return truncated.decode("utf-8", errors="ignore")


def chunk_text_by_bytes(
    text: str,
    max_bytes: int,
) -> list[str]:
    """按 UTF-8 字节长度拆分文本。

    适用于企业微信等按字节计算消息长度的平台。
    优先在换行符处切分，超长行做字节级截断。

    Args:
        text: 待拆分文本
        max_bytes: 每条消息最大字节数

    Returns:
        拆分后的文本列表
    """
    if not text or not text.strip():
        return []
    if len(text.encode("utf-8")) <= max_bytes:
        return [text]

    chunks: list[str] = []
    current = ""

    for line in text.split("\n"):
        candidate = f"{current}{line}\n" if current else f"{line}\n"
        if len(candidate.encode("utf-8")) <= max_bytes:
            current = candidate
            continue

        if current:
            chunks.append(current.rstrip("\n"))
            current = ""

        line_bytes = len(line.encode("utf-8"))
        if line_bytes + 1 > max_bytes:
            while line:
                piece = utf8_safe_truncate(line, max_bytes)
                if not piece:
                    break
                chunks.append(piece)
                line = line[len(piece):]
        else:
            current = line + "\n"

    if current:
        chunks.append(current.rstrip("\n"))

    return [c for c in chunks if c.strip()]
