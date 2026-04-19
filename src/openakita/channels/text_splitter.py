"""
Markdown-aware text chunking tool

Split long reply text into multiple messages per platform limits while preserving Markdown syntax:
- Code block fences (```) won't be split across messages
- Prefer splitting at paragraph boundaries (blank lines)
- Over-long single paragraphs/code blocks are split and fences are re-added
- Provide UTF-8 byte-safe splitting (for platforms like WeChat that count by bytes)
- Fragment sequence markers ([1/N]) help users identify message order
- Markdown degradation to plaintext for text-only platforms (preserves structure)
"""

from __future__ import annotations

import re

_RE_FENCE = re.compile(r"^(`{3,}|~{3,})", re.MULTILINE)


def _find_segments(text: str) -> list[str]:
    """Split text into "code blocks" and "plain text" segments.

    Ensures each code block (including fence lines) is a complete segment that won't
    be split further when dividing by paragraphs.
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
    """Split plain text using three-tier strategy: paragraphs (double newlines) → lines → characters."""
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
    """Merge by lines, truncate over-long lines at character level."""
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
    """Split over-long code blocks, re-add fence lines for each chunk."""
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

    body_lines = lines[1:]
    if body_lines and body_lines[-1].strip().startswith(fence_char * fence_len):
        body_lines[-1]
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
    """Split Markdown text into multiple messages by max_length.

    - Fenced code blocks are atomic units and won't be split in the middle
    - Plain text is split preferentially at paragraph boundaries (double newlines)
    - Over-long code blocks are split and fences are re-added for each chunk

    Args:
        text: Markdown text to split
        max_length: Maximum character length per message

    Returns:
        List of split text
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
    """Truncate text to not exceed max_bytes of UTF-8 bytes, ensuring no multi-byte characters are split."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    truncated = encoded[:max_bytes]
    return truncated.decode("utf-8", errors="ignore")


def chunk_text_by_bytes(
    text: str,
    max_bytes: int,
) -> list[str]:
    """Split text by UTF-8 byte length.

    Suitable for platforms like WeChat that measure message length in bytes.
    Prefer splitting at newlines; over-long lines are truncated at byte level.

    Args:
        text: Text to split
        max_bytes: Maximum bytes per message

    Returns:
        List of split text
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
                line = line[len(piece) :]
        else:
            current = line + "\n"

    if current:
        chunks.append(current.rstrip("\n"))

    return [c for c in chunks if c.strip()]


# ---------------------------------------------------------------------------
# Fragment sequence markers
# ---------------------------------------------------------------------------

_DEFAULT_NUMBER_FMT = "[{i}/{n}] "

_NUMBER_FORMATS: dict[str, str] = {
    "bracket": "[{i}/{n}] ",
    "paren": "({i}/{n}) ",
    "emoji": "{emoji}/{n} ",
}

_EMOJI_DIGITS = ["0️⃣", "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]


def _emoji_number(n: int) -> str:
    return "".join(_EMOJI_DIGITS[int(d)] for d in str(n))


def add_fragment_numbers(
    chunks: list[str],
    *,
    fmt: str = "bracket",
) -> list[str]:
    """Add sequence number prefixes to fragmented messages.

    Only adds sequence numbers when ``len(chunks) > 1``; single messages are returned unchanged.

    Args:
        chunks: List of split messages
        fmt: Sequence format - ``"bracket"`` → ``[1/3]``,
             ``"paren"`` → ``(1/3)``, ``"emoji"`` → ``1️⃣/3``

    Returns:
        List of messages with sequence numbers added
    """
    if len(chunks) <= 1:
        return chunks

    total = len(chunks)
    template = _NUMBER_FORMATS.get(fmt, _DEFAULT_NUMBER_FMT)

    result: list[str] = []
    for idx, chunk in enumerate(chunks, 1):
        if "emoji" in fmt:
            prefix = template.replace("{emoji}", _emoji_number(idx)).replace("{n}", str(total))
        else:
            prefix = template.format(i=idx, n=total)
        result.append(prefix + chunk)

    return result


def estimate_number_prefix_len(total: int, fmt: str = "bracket") -> int:
    """Estimate maximum character length of fragment sequence prefix for pre-allocation."""
    if total <= 1:
        return 0
    template = _NUMBER_FORMATS.get(fmt, _DEFAULT_NUMBER_FMT)
    if "emoji" in fmt:
        sample = template.replace("{emoji}", _emoji_number(total)).replace("{n}", str(total))
    else:
        sample = template.format(i=total, n=total)
    return len(sample)


# ---------------------------------------------------------------------------
# Markdown → plaintext degradation (preserve code structure and link URLs)
# ---------------------------------------------------------------------------

_RE_MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
_RE_MD_IMG = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_RE_MD_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_RE_MD_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)")
_RE_MD_STRIKE = re.compile(r"~~(.+?)~~")
_RE_MD_INLINE_CODE = re.compile(r"`([^`]+)`")
_RE_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_RE_MD_HR = re.compile(r"^[-*_]{3,}\s*$", re.MULTILINE)
_RE_MD_BLOCKQUOTE = re.compile(r"^>\s?", re.MULTILINE)


def markdown_to_plaintext(text: str) -> str:
    """Convert Markdown to plaintext while preserving code indentation structure and link URLs.

    Smarter than simple strip: code blocks preserve indentation, links preserve URLs,
    lists preserve numbering/indentation structure.
    """
    if not text:
        return text

    lines = text.split("\n")
    result_lines: list[str] = []
    in_code = False
    fence_marker = ""

    for line in lines:
        stripped = line.lstrip()

        fence_match = _RE_FENCE.match(stripped)
        if fence_match:
            marker = fence_match.group(1)
            if not in_code:
                in_code = True
                fence_marker = marker[0] * len(marker)
                lang = stripped[len(marker) :].strip()
                result_lines.append(f"--- {lang} ---" if lang else "---")
                continue
            elif marker[0] == fence_marker[0] and len(marker) >= len(fence_marker):
                in_code = False
                fence_marker = ""
                result_lines.append("---")
                continue

        if in_code:
            result_lines.append(line)
            continue

        line = _RE_MD_IMG.sub(r"[image: \1](\2)", line)
        line = _RE_MD_LINK.sub(r"\1 (\2)", line)
        heading_match = _RE_MD_HEADING.match(line)
        if heading_match:
            level = len(heading_match.group(1))
            title = heading_match.group(2)
            line = f"{'=' * level} {title} {'=' * level}"
        line = _RE_MD_BOLD.sub(lambda m: m.group(1) or m.group(2), line)
        line = _RE_MD_ITALIC.sub(lambda m: m.group(1) or m.group(2) or "", line)
        line = _RE_MD_STRIKE.sub(r"\1", line)
        line = _RE_MD_INLINE_CODE.sub(r"\1", line)
        line = _RE_MD_BLOCKQUOTE.sub("  ", line)
        line = _RE_MD_HR.sub("────────────", line)

        result_lines.append(line)

    return "\n".join(result_lines)
