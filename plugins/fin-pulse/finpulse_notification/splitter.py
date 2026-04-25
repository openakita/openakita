"""Line-boundary content splitter.

Host IM adapters do **not** auto-chunk long messages; a 25 KB digest
silently gets truncated by Feishu / DingTalk. We therefore split the
markdown blob along ``\\n`` boundaries so no line is ever cut in the
middle (which would leave half-rendered links / orphan scores / etc).

The algorithm is intentionally conservative, with two small hardenings:

* Each chunk emitted **after the first** is prefixed with the caller's
  ``base_header`` (e.g. ``"[早报 续 2/3]\n"``) so recipients can tell
  a mid-stream chunk apart from a fresh push.
* A lone over-sized line (``len(line) > max_bytes``) becomes its own
  chunk (force-split) rather than being dropped — we never silently
  eat content.
"""

from __future__ import annotations

from typing import Iterable

DEFAULT_BATCH_BYTES: dict[str, int] = {
    "dingtalk": 18000,
    "feishu": 25000,
    "wework": 4000,
    "wework_ws": 4000,
    "wechat": 4000,
    "telegram": 3800,
    "qqbot": 4000,
    "onebot": 4000,
    "email": 100_000,
    "default": 4000,
}


def _encoded_len(s: str) -> int:
    return len(s.encode("utf-8"))


def split_by_lines(
    content: str,
    *,
    footer: str = "",
    max_bytes: int,
    base_header: str = "",
) -> list[str]:
    """Split ``content`` on ``\\n`` boundaries into chunks whose UTF-8
    byte length (plus ``footer``) never exceeds ``max_bytes``.

    Rules
    -----
    * ``max_bytes`` must exceed ``len(footer)`` + ``len(base_header)``
      plus a small 32-byte safety margin — a :class:`ValueError` is
      raised otherwise so misconfiguration is loud.
    * Empty ``content`` returns ``[]`` (not ``[""]``).
    * Lines longer than ``max_bytes`` by themselves become a chunk on
      their own (force-split) — we never drop content.
    * ``base_header`` is prepended to every chunk **after** the first.
    """
    if not content:
        return []
    footer_size = _encoded_len(footer)
    header_size = _encoded_len(base_header)
    if max_bytes <= max(footer_size + header_size, 32):
        raise ValueError(
            f"max_bytes={max_bytes} must exceed footer({footer_size})"
            f" + base_header({header_size}) + safety margin"
        )

    out: list[str] = []
    cur = ""
    for line in content.split("\n"):
        line_with_nl = line + "\n"
        cand = cur + line_with_nl
        cand_size = _encoded_len(cand) + footer_size

        if cand_size <= max_bytes:
            cur = cand
            continue

        if cur.strip():
            out.append(cur.rstrip("\n") + ("\n" + footer if footer else ""))
            cur = base_header + line_with_nl
            if _encoded_len(cur) + footer_size > max_bytes:
                out.append(cur.rstrip("\n") + ("\n" + footer if footer else ""))
                cur = ""
        else:
            out.append(
                line_with_nl.rstrip("\n") + ("\n" + footer if footer else "")
            )
            cur = ""

    if cur.strip():
        out.append(cur.rstrip("\n") + ("\n" + footer if footer else ""))
    return out


def concat_with_footer(chunks: Iterable[str], *, footer: str = "") -> str:
    """Re-join chunks and strip the repeating footer — useful in tests
    or when a caller wants to round-trip the splitter output back to
    the original blob.
    """
    joined = "\n".join(chunks)
    if not footer:
        return joined
    return joined.replace("\n" + footer, "")


__all__ = ["DEFAULT_BATCH_BYTES", "concat_with_footer", "split_by_lines"]
