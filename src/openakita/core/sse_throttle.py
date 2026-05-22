"""SSE delta event coalescer.

v1.27.15 (S2 P0-2, plan: conversation concurrency v1.28).

Purpose
=======

OpenAkita's SSE pipeline used to emit one HTTP chunk per LLM token. On
long conversations (8k+ tokens of context) the front-end React renderer
saturates: every ``text_delta`` triggers a re-render of the running
message bubble, and per-frame React reconciliation cost dominates
end-to-end latency.  Users described this as "the answer crawls out a
character at a time, feels really laggy" (multiple feedback in
v1.27.10–v1.27.12).

Industry parallels — openclaw's ``emitChatDelta`` (gateway-side 150ms
throttle + ``deltaText`` merging), openfang's WS ``DEBOUNCE_MS=100 /
DEBOUNCE_CHARS=200`` and ``bufferedAgentEvents`` aggregation — both
land on time + char co-thresholds.  We follow the same recipe.

Contract
========

* Delta-style events (``text_delta``, ``chain_text``, ``thinking_delta``,
  ``reasoning_delta``) accumulate in per-channel buckets and are
  flushed as **a single merged event** when ANY of:

  * the bucket's accumulated ``content`` length exceeds ``max_chars``
    (default 2000 — well above one paragraph; tuned not to delay first
    paint beyond a sentence);
  * ``interval_ms`` (default 50ms) has elapsed since this bucket's
    last flush (or first push);
  * a non-delta event arrives (always flushes all buckets first, in
    insertion order, to preserve `text_delta` → `tool_call_start`
    ordering downstream consumers rely on);
  * :meth:`drain` is called (end of stream / cancellation).

* Non-delta events bypass the bucket entirely.  This keeps
  ``tool_call_start``, ``tool_call_end``, ``ask_user``, ``done`` etc.
  on the same low-latency path as before — the optimisation only
  targets the *high-frequency text streams*, which are the actual
  bottleneck.

* The coalescer is **content-additive**: it does NOT alter the
  payload schema or `type` field — front-end consumers see exactly
  the same event types, just fewer of them with longer `content`
  per event.  Existing UI logic that processes `text_delta.content`
  as "append to bubble" needs no change.

Threading / asyncio
===================

Single-event-loop only.  Each ``_stream_chat`` invocation has its own
coalescer instance, so there is no cross-coroutine contention.  We
deliberately do not add an internal lock — the caller serialises
``offer`` / ``tick`` / ``drain`` from one task.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# Event types whose ``content`` we coalesce.  Add new delta types here
# as more streaming channels are introduced (e.g. ``tool_args_delta``).
COALESCED_TYPES: frozenset[str] = frozenset(
    {
        "text_delta",
        "chain_text",
        "thinking_delta",
        "reasoning_delta",
    }
)

DEFAULT_INTERVAL_MS: int = 50
DEFAULT_MAX_CHARS: int = 2000


@dataclass
class _Bucket:
    """Per-channel accumulator.  Channel key is ``event_type``."""

    event_type: str
    parts: list[str] = field(default_factory=list)
    # The first-seen ``data`` dict for this bucket.  We preserve any
    # non-``content`` keys it carries (e.g. ``source``, ``role``,
    # ``model``); only ``content`` is overwritten on flush with the
    # concatenated value.  This keeps schema-rich deltas faithful.
    template: dict[str, Any] | None = None
    char_count: int = 0
    first_push_ts: float = 0.0
    last_flush_ts: float = 0.0


class DeltaCoalescer:
    """Time + size windowed coalescer for high-frequency delta events.

    Per-conversation, per-stream instance — do not share between
    concurrent ``_stream_chat`` runs.

    Example::

        coalescer = DeltaCoalescer(interval_ms=50, max_chars=2000)
        # On every event from the agent queue:
        for merged in coalescer.offer(event_type, data):
            yield format(merged)  # one or more flushed events

        # Periodically (e.g. every 50ms when the queue is idle):
        for merged in coalescer.tick():
            yield format(merged)

        # End of stream / on cancel:
        for merged in coalescer.drain():
            yield format(merged)
    """

    def __init__(
        self,
        *,
        interval_ms: int = DEFAULT_INTERVAL_MS,
        max_chars: int = DEFAULT_MAX_CHARS,
        coalesced_types: frozenset[str] = COALESCED_TYPES,
    ) -> None:
        self._interval = interval_ms / 1000.0
        self._max_chars = max_chars
        self._coalesced_types = coalesced_types
        # OrderedDict-ish via plain dict (Python 3.7+ preserves insertion order)
        # — insertion order = arrival order = the order we flush in when
        # a non-delta event arrives.
        self._buckets: dict[str, _Bucket] = {}

    # ── public API ────────────────────────────────────────────────────

    def offer(
        self, event_type: str, data: dict[str, Any] | None
    ) -> list[tuple[str, dict[str, Any]]]:
        """Push one upstream event; return a list of ready-to-emit events.

        Returns a list of ``(event_type, data)`` pairs in emission
        order.  Most calls return either an empty list (still
        accumulating) or a single-item list (one merged flush).
        Non-delta events return all pending bucket flushes followed by
        the original event itself.
        """
        data = data or {}
        if event_type not in self._coalesced_types:
            # Non-delta: flush everything in arrival order, then the event itself.
            out: list[tuple[str, dict[str, Any]]] = self._flush_all_buckets()
            out.append((event_type, dict(data)))
            return out

        content = self._extract_content(data)
        if not content:
            # Empty delta — nothing to add but the event type is
            # still legit; emit as-is to preserve any schema flags.
            return [(event_type, dict(data))]

        now = time.monotonic()
        bucket = self._buckets.get(event_type)
        if bucket is None:
            bucket = _Bucket(
                event_type=event_type,
                template={k: v for k, v in data.items() if k != "content"},
                first_push_ts=now,
                last_flush_ts=now,
            )
            self._buckets[event_type] = bucket

        bucket.parts.append(content)
        bucket.char_count += len(content)

        flushed: list[tuple[str, dict[str, Any]]] = []
        # Size-based flush: emit synchronously without waiting for the timer.
        if bucket.char_count >= self._max_chars:
            flushed.append(self._build_merged_event(bucket, now))
            self._reset_bucket(bucket, now)
        return flushed

    def tick(
        self, *, now: float | None = None
    ) -> list[tuple[str, dict[str, Any]]]:
        """Check time-based flushes.  Call this periodically when idle.

        Args:
            now: monotonic timestamp; defaults to ``time.monotonic()``.
                Exposed for tests that need deterministic timing.

        Returns due flushes in bucket arrival order (oldest first).
        """
        if not self._buckets:
            return []
        cur = now if now is not None else time.monotonic()
        out: list[tuple[str, dict[str, Any]]] = []
        # Snapshot keys; we mutate during iteration.
        for key in list(self._buckets.keys()):
            bucket = self._buckets[key]
            if not bucket.parts:
                continue
            elapsed = cur - bucket.last_flush_ts
            if elapsed >= self._interval:
                out.append(self._build_merged_event(bucket, cur))
                self._reset_bucket(bucket, cur)
        return out

    def drain(self) -> list[tuple[str, dict[str, Any]]]:
        """Flush every non-empty bucket.  Used at end-of-stream / cancel.

        After ``drain`` the coalescer is fully empty and can be reused
        for a new stream if desired.
        """
        return self._flush_all_buckets()

    def has_pending(self) -> bool:
        """True if any bucket has content waiting to be flushed."""
        return any(b.parts for b in self._buckets.values())

    # ── internals ─────────────────────────────────────────────────────

    @staticmethod
    def _extract_content(data: dict[str, Any]) -> str:
        v = data.get("content")
        if isinstance(v, str):
            return v
        if v is None:
            return ""
        # Be tolerant: some upstream events stringify content lazily.
        try:
            return str(v)
        except Exception:  # pragma: no cover
            return ""

    def _build_merged_event(
        self, bucket: _Bucket, now: float
    ) -> tuple[str, dict[str, Any]]:
        merged_content = "".join(bucket.parts)
        data = dict(bucket.template or {})
        data["content"] = merged_content
        # Diagnostic header — small enough to never matter for payload
        # size, useful in dev to verify coalescing is taking effect.
        # Stamped only if the merge actually saved frames (>1 part).
        if len(bucket.parts) > 1:
            data.setdefault("_coalesced_parts", len(bucket.parts))
        return (bucket.event_type, data)

    @staticmethod
    def _reset_bucket(bucket: _Bucket, now: float) -> None:
        bucket.parts.clear()
        bucket.char_count = 0
        bucket.first_push_ts = now
        bucket.last_flush_ts = now

    def _flush_all_buckets(self) -> list[tuple[str, dict[str, Any]]]:
        if not self._buckets:
            return []
        now = time.monotonic()
        out: list[tuple[str, dict[str, Any]]] = []
        # Iterate in insertion order; reset each bucket after emit.
        for key in list(self._buckets.keys()):
            bucket = self._buckets[key]
            if not bucket.parts:
                continue
            out.append(self._build_merged_event(bucket, now))
            self._reset_bucket(bucket, now)
        return out


__all__ = [
    "COALESCED_TYPES",
    "DEFAULT_INTERVAL_MS",
    "DEFAULT_MAX_CHARS",
    "DeltaCoalescer",
]
