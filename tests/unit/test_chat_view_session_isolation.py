from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CHAT_VIEW = REPO_ROOT / "apps" / "setup-center" / "src" / "views" / "ChatView.tsx"
CHAT_HELPERS = (
    REPO_ROOT
    / "apps"
    / "setup-center"
    / "src"
    / "views"
    / "chat"
    / "utils"
    / "chatHelpers.ts"
)


def test_background_queued_turn_does_not_render_into_active_chat():
    source = CHAT_VIEW.read_text(encoding="utf-8")

    assert "shouldRenderConversationMessages" in source
    assert "setMessages(sctx.messages)" not in source
    assert "saveMessagesToStorage(STORAGE_KEY_MSGS_PREFIX + thisConvId, sctx.messages)" in source


def test_conversation_render_guard_requires_exact_active_match():
    source = CHAT_HELPERS.read_text(encoding="utf-8")

    assert "export function shouldRenderConversationMessages" in source
    assert "return Boolean(conversationId) && conversationId === activeConversationId;" in source


def test_backend_history_patch_prefers_stable_message_identity():
    source = CHAT_HELPERS.read_text(encoding="utf-8")

    assert "type BackendHistoryMessage" in source
    assert "backendByHistoryIndex" in source
    assert "typeof m.historyIndex === \"number\"" in source
    assert "backendByHistoryIndex.get(m.historyIndex)" in source
    assert "backendById.get(m.id)" in source


def test_backend_history_patch_keeps_single_sequence_fallback():
    """Backend-history -> local-message patching must consume each backend
    assistant entry **at most once** (a single Set tracks claimed ones), and
    the positional fallback that picks an unclaimed backend entry MUST be
    restricted to the *last* local assistant message.

    The narrow fallback scope was introduced by ``perf(chat): tail render
    window and safer backend matching for long threads`` (9f59a34c) to stop
    older local-only messages from silently absorbing the wrong backend
    content when histories grow long; this test pins it down so a future
    revert won't reintroduce the regression.
    """
    source = CHAT_HELPERS.read_text(encoding="utf-8")

    # 1. One-to-one claim ledger — backend entries can only be assigned once.
    assert "const usedBackendMessages = new Set<BackendHistoryMessage>()" in source
    assert "usedBackendMessages.add(candidate)" in source
    assert "if (!usedBackendMessages.has(candidate))" in source

    # 2. Fallback iterates over backendAssistant looking for an unclaimed slot.
    assert "for (let i = backendAssistant.length - 1; i >= 0; i -= 1)" in source

    # 3. Hard guard: fallback only fires for the latest local assistant.
    assert "lastLocalAssistantIndex" in source
    assert "if (localIndex !== lastLocalAssistantIndex)" in source
