from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_RS = ROOT / "apps" / "setup-center" / "src-tauri" / "src" / "main.rs"


# Commands in this list may touch a Tauri handle synchronously from the invoke
# path. Long-running commands must not be added here; move progress/state through
# plain data instead of holding AppHandle/Window across worker boundaries.
ALLOWED_TAURI_HANDLE_COMMANDS = {
    "autostart_is_enabled",
    "autostart_set_enabled",
    "notify_system",
    "set_tray_backend_status",
    "start_dragging",
    "toggle_pet_window",
}

BACKGROUND_CALLS = (
    "std::thread::spawn",
    "thread::spawn",
    "spawn_blocking_result",
    "tauri::async_runtime::spawn_blocking",
    "tauri::async_runtime::spawn",
)

BACKGROUND_TAURI_PATTERNS = (
    ("tauri::AppHandle", "Tauri AppHandle type"),
    ("tauri::Window", "Tauri Window type"),
    ("AppHandle", "Tauri AppHandle type"),
    ("run_on_main_thread", "Tauri main-thread marshal"),
    ("get_webview_window", "Tauri window access"),
    ("emit_if_ui_live", "Tauri event emit"),
    ("app_handle", "captured Tauri app handle"),
    ("app_for_ui", "captured Tauri app handle"),
)


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def mask_span(chars: list[str], start: int, end: int) -> None:
    for i in range(start, end):
        if chars[i] != "\n":
            chars[i] = " "


def mask_rust_non_code(text: str) -> str:
    """Mask comments and string literals while preserving offsets/newlines."""
    chars = list(text)
    i = 0
    n = len(text)
    while i < n:
        if text.startswith("//", i):
            start = i
            i = text.find("\n", i)
            if i == -1:
                i = n
            mask_span(chars, start, i)
            continue

        if text.startswith("/*", i):
            start = i
            i += 2
            depth = 1
            while i < n and depth:
                if text.startswith("/*", i):
                    depth += 1
                    i += 2
                elif text.startswith("*/", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
            mask_span(chars, start, i)
            continue

        raw = re.match(r"br?(\#*)\"", text[i:])
        if raw:
            hashes = raw.group(1)
            start = i
            i += raw.end()
            end_marker = '"' + hashes
            end = text.find(end_marker, i)
            i = n if end == -1 else end + len(end_marker)
            mask_span(chars, start, i)
            continue

        if text[i] == '"':
            start = i
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == '"':
                    i += 1
                    break
                i += 1
            mask_span(chars, start, i)
            continue

        i += 1
    return "".join(chars)


def find_matching(text: str, open_index: int, open_char: str, close_char: str) -> int | None:
    depth = 0
    for i in range(open_index, len(text)):
        if text[i] == open_char:
            depth += 1
        elif text[i] == close_char:
            depth -= 1
            if depth == 0:
                return i
    return None


def iter_calls(clean_text: str, name: str):
    pos = 0
    while True:
        pos = clean_text.find(name, pos)
        if pos == -1:
            return
        before = clean_text[pos - 1] if pos > 0 else ""
        if before.isalnum() or before == "_":
            pos += len(name)
            continue
        after = pos + len(name)
        while after < len(clean_text) and clean_text[after].isspace():
            after += 1
        if after >= len(clean_text) or clean_text[after] != "(":
            pos += len(name)
            continue
        end = find_matching(clean_text, after, "(", ")")
        if end is None:
            yield pos, clean_text[pos:]
            return
        yield pos, clean_text[pos : end + 1]
        pos = end + 1


def iter_tauri_commands(clean_text: str):
    attr_re = re.compile(r"#\s*\[\s*tauri::command\s*\]")
    fn_re = re.compile(r"\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z0-9_]+)")
    for attr in attr_re.finditer(clean_text):
        fn_match = fn_re.match(clean_text, attr.end())
        if not fn_match:
            continue
        name = fn_match.group(1)
        paren = clean_text.find("(", fn_match.end())
        if paren == -1:
            continue
        end = find_matching(clean_text, paren, "(", ")")
        if end is None:
            continue
        yield attr.start(), name, clean_text[paren + 1 : end]


def background_tauri_reasons(call_text: str) -> list[str]:
    reasons = [
        reason
        for pattern, reason in BACKGROUND_TAURI_PATTERNS
        if pattern in call_text
    ]
    if re.search(r"\.\s*emit\s*\(", call_text):
        reasons.append("Tauri event emit")
    if re.search(r"\bapp\s*\.\s*clone\s*\(", call_text):
        reasons.append("captured Tauri app handle")
    return sorted(set(reasons))


def main() -> int:
    text = MAIN_RS.read_text(encoding="utf-8")
    clean_text = mask_rust_non_code(text)
    issues: list[str] = []

    for offset, name, args in iter_tauri_commands(clean_text):
        takes_tauri_handle = (
            "tauri::AppHandle" in args
            or "tauri::Window" in args
            or re.search(r"\bAppHandle\b", args) is not None
            or re.search(r"\bWindow\b", args) is not None
        )
        if takes_tauri_handle and name not in ALLOWED_TAURI_HANDLE_COMMANDS:
            issues.append(
                f"{MAIN_RS}:{line_number(text, offset)}: command {name} takes a Tauri handle"
            )

    for call_name in BACKGROUND_CALLS:
        for offset, call_text in iter_calls(clean_text, call_name):
            reasons = background_tauri_reasons(call_text)
            if not reasons:
                continue
            snippet = " ".join(text[offset : offset + 220].split())
            issues.append(
                f"{MAIN_RS}:{line_number(text, offset)}: {call_name} uses "
                f"{', '.join(reasons)}: {snippet}"
            )

    for match in re.finditer(r"\.\s*emit\s*\(", clean_text):
        line = line_number(text, match.start())
        source_line = text.splitlines()[line - 1]
        if "if let Err(e) = app.emit(" in source_line:
            continue
        issues.append(f"{MAIN_RS}:{line}: direct Tauri emit; use emit_if_ui_live")

    if issues:
        print("Tauri lifecycle audit failed:")
        for issue in issues:
            print(f"  - {issue}")
        return 1

    print("Tauri lifecycle audit passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
