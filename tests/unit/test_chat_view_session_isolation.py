from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CHAT_VIEW = REPO_ROOT / "apps" / "setup-center" / "src" / "views" / "ChatView.tsx"
CHAT_HELPERS = (
    REPO_ROOT / "apps" / "setup-center" / "src" / "views" / "chat" / "utils" / "chatHelpers.ts"
)
QUERY_GUARD = (
    REPO_ROOT / "apps" / "setup-center" / "src" / "views" / "chat" / "hooks" / "useQueryGuard.ts"
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


def test_hydration_never_uses_rendered_messages_from_another_conversation():
    source = CHAT_VIEW.read_text(encoding="utf-8")

    assert "displayedMessagesConvIdRef.current !== convId" in source
    assert "displayedMessagesConvIdRef.current === convId" in source
    assert "const canUseActiveMsgs =" in source
    assert "const activeMsgs = canUseActiveMsgs" in source
    assert "displayedMessagesConvIdRef.current = activeConvId;" not in source


def test_stream_guard_is_keyed_by_conversation_id():
    source = CHAT_VIEW.read_text(encoding="utf-8")

    assert "queryGuard.startQuery(thisConvId)" in source
    assert "queryGuard.endQuery(guardHandle.generation, thisConvId)" in source
    assert "queryGuard.cancel(id)" in source
    assert "queryGuard.startQuery()" not in source
    assert "queryGuard.endQuery(guardHandle.generation);" not in source
    assert "queryGuard.cancel();" not in source


def test_query_guard_does_not_cancel_global_stream_when_canceling_one_conversation():
    source = QUERY_GUARD.read_text(encoding="utf-8")

    conv_branch = source.split("const cancel = useCallback((convId?: string) => {", 1)[1].split(
        "if (abortRef.current)",
        1,
    )[0]
    assert "slot.abort.abort(\"user_cancelled\")" in conv_branch
    assert "slotsRef.current.delete(convId)" in conv_branch
    assert "return;" in conv_branch

    start_branch = source.split("if (convId) {", 1)[1].split("} else {", 1)[0]
    assert "abortRef.current = ctrl" not in start_branch


def test_detached_running_conversation_polls_backend_history_for_todo_progress():
    source = CHAT_VIEW.read_text(encoding="utf-8")

    assert "pollDetachedRunningConversation" in source
    assert "/api/chat/busy?conversation_id=" in source
    assert "/api/sessions/${encodeURIComponent(convId)}/history?limit=${HISTORY_PAGE_LIMIT}" in source
    assert "mergeActiveTodo(chooseHydratedMessages(localMsgs, backendMsgs), data?.active_todo)" in source
    assert "streamContexts.current.get(activeConvId)?.isStreaming" in source


def test_backend_history_patch_prefers_stable_message_identity():
    source = CHAT_HELPERS.read_text(encoding="utf-8")

    assert "type BackendHistoryMessage" in source
    assert "backendByHistoryIndex" in source
    assert 'typeof m.historyIndex === "number"' in source
    assert "backendByHistoryIndex.get(m.historyIndex)" in source
    assert "backendById.get(m.id)" in source


def test_hydration_drops_cross_conversation_local_cache():
    source = CHAT_HELPERS.read_text(encoding="utf-8")

    assert "function firstUserContent" in source
    assert "function removeAdjacentDuplicateUserMessages" in source
    assert "localFirstUser !== backendFirstUser" in source
    assert "return cleanBackend" in source


def test_backend_todo_snapshot_refreshes_existing_plan_card():
    source = CHAT_HELPERS.read_text(encoding="utf-8")

    assert "backend.todo?.steps?.length" in source
    assert "JSON.stringify(m.todo) !== JSON.stringify(backend.todo)" in source
    assert "patches.todo = backend.todo" in source


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
