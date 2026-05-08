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
    source = CHAT_HELPERS.read_text(encoding="utf-8")

    assert "const usedBackendMessages = new Set<BackendHistoryMessage>()" in source
    assert "let fallbackAssistantIdx = 0" in source
    assert "while (fallbackAssistantIdx < backendAssistant.length)" in source
    assert "usedBackendMessages.add(candidate)" in source
