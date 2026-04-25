"""Notification helpers — fin-pulse delegates every IM payload to the
host gateway (``PluginAPI.send_message``). The only logic that still
needs to live plugin-side is **line-boundary batching**: host adapters
do not auto-chunk long markdown, so a 25 KB daily brief would be
silently truncated by Feishu / DingTalk.

See :func:`splitter.split_by_lines` — a line-boundary splitter with two
hardenings required by the plan §10.4 (``base_header`` prepended to every follow-up chunk; lone
oversize lines emit as their own chunk instead of being dropped).
"""

from __future__ import annotations

from finpulse_notification.splitter import (
    DEFAULT_BATCH_BYTES,
    concat_with_footer,
    split_by_lines,
)

__all__ = [
    "DEFAULT_BATCH_BYTES",
    "concat_with_footer",
    "split_by_lines",
]
