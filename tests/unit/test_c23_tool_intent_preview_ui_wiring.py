"""C23 P2-3: ChatView consumes ``tool_intent_preview`` SSE.

Background
==========

C9c-1 added the backend ``tool_intent_preview`` SSE emission in
``tool_executor._emit_tool_intent_previews`` so the UI can hint at
"engine is about to call X" before any tool actually runs. But from
C9c-1 to C20 the *frontend* never subscribed — the event got emitted,
serialized over WebSocket, and dropped on the floor.

C21 二轮架构审计标 P2-3. C23 wires ChatView to handle the event by
showing a 2.5s toast for non-readonly tools.

This test
=========

Pure structural grep guard — frontend has no Jest / Vitest harness in
this repo. We assert:

1. The chat event union type knows about ``tool_intent_preview``
2. ChatView has a ``case "tool_intent_preview":`` handler in its
   stream-event switch
3. The handler imports ``toast`` from sonner
4. The i18n strings exist in zh + en

Any future refactor that breaks one of these gets flagged immediately
without needing a JS runtime.
"""

from __future__ import annotations

import json
from pathlib import Path


SETUP_CENTER = Path("apps/setup-center")


def test_chat_types_union_includes_tool_intent_preview() -> None:
    chat_types = (SETUP_CENTER / "src/views/chat/utils/chatTypes.ts").read_text(
        encoding="utf-8"
    )
    assert 'type: "tool_intent_preview"' in chat_types, (
        "chatTypes.ts must declare a ``tool_intent_preview`` variant "
        "in the SSE event discriminated union (C23 P2-3)."
    )
    # 字段最小集
    for field in ("tool_use_id", "tool_name", "approval_class", "params", "batch_idx"):
        assert field in chat_types, (
            f"chatTypes.ts tool_intent_preview variant must include "
            f"field ``{field}`` to match the backend "
            f"_emit_tool_intent_previews schema."
        )


def test_chatview_handles_tool_intent_preview() -> None:
    chat_view = (SETUP_CENTER / "src/views/ChatView.tsx").read_text(encoding="utf-8")
    assert 'case "tool_intent_preview":' in chat_view, (
        "ChatView.tsx event switch must have a ``case "
        '"tool_intent_preview":`` arm (C23 P2-3). The backend has been '
        "emitting this event since C9c-1; if you removed the handler "
        "instead of fixing a bug in it, the UX regresses to silent "
        "tool execution."
    )
    assert 'import { toast } from "sonner";' in chat_view, (
        "ChatView.tsx must import sonner toast for tool_intent_preview "
        "rendering."
    )


def test_intent_preview_skips_readonly_classes() -> None:
    """The handler MUST skip noisy classes (readonly_*, interactive,
    unknown) — otherwise every list_directory / read_file call yields
    a toast and the UI becomes unusable. This grep guard pins the
    skip-set so a future refactor that "simplifies" by dropping the
    filter gets caught."""
    chat_view = (SETUP_CENTER / "src/views/ChatView.tsx").read_text(encoding="utf-8")
    # Look for the noisyClasses set definition
    for cls in (
        "readonly_scoped",
        "readonly_global",
        "readonly_search",
        "interactive",
        "unknown",
    ):
        assert f'"{cls}"' in chat_view, (
            f"tool_intent_preview filter must skip approval_class "
            f"``{cls}`` — otherwise the toast fires on every routine "
            "read and floods the UI."
        )


def test_i18n_strings_present() -> None:
    for locale in ("zh.json", "en.json"):
        data = json.loads(
            (SETUP_CENTER / f"src/i18n/{locale}").read_text(encoding="utf-8")
        )
        chat = data.get("chat", {})
        for key in ("toolIntentPreview", "toolIntentPreviewClass"):
            assert key in chat, (
                f"{locale} chat.{key} missing — toast falls back to the "
                "code default which is fine but loses translation."
            )


def test_backend_still_emits_tool_intent_preview() -> None:
    """Sanity: the backend emitter we're consuming still exists.
    If someone removes _emit_tool_intent_previews, the frontend
    wiring becomes dead code — flag it loudly here so we drop the
    handler in the same commit."""
    src = Path("src/openakita/core/tool_executor.py").read_text(encoding="utf-8")
    assert "_emit_tool_intent_previews" in src, (
        "Backend emitter `_emit_tool_intent_previews` is gone; either "
        "restore it or drop the frontend wiring + this test in the "
        "same commit."
    )
    assert 'fire_event("tool_intent_preview"' in src, (
        "tool_executor must still call fire_event(\"tool_intent_preview\", "
        "...) — without it the SSE never reaches the WebSocket and the "
        "frontend handler is dead code."
    )
