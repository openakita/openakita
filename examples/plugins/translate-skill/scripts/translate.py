#!/usr/bin/env python3
"""Demo-only \"translation\" via fixed string replacements (not a real translator)."""

from __future__ import annotations

import argparse
import sys

# Tiny phrasebook for demos — replace longer phrases first where needed.
_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("good morning", "早上好"),
    ("hello world", "你好世界"),
    ("hello", "你好"),
    ("world", "世界"),
    ("thanks", "谢谢"),
    ("yes", "是"),
    ("no", "否"),
)


def fake_translate(text: str, target: str = "zh") -> str:
    """Apply naive replacements; ``target`` is accepted for CLI compatibility only."""
    _ = target
    out = text
    for src, dst in _REPLACEMENTS:
        out = out.replace(src, dst)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Demo translate (string replacement).")
    parser.add_argument("text", nargs="*", help="Text to transform")
    parser.add_argument(
        "--target",
        "-t",
        default="zh",
        help="Ignored in this demo; kept for interface compatibility",
    )
    args = parser.parse_args()
    raw = " ".join(args.text).strip() or "hello world"
    print(fake_translate(raw, target=args.target))


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        sys.exit(0)
