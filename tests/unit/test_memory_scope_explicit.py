"""Regression tests for P0-1 add_memory scope handling.

These tests pin down the contract between the model, the user, and the memory
handler so that:
1. ``add_memory(scope="global")`` always wins over the heuristic.
2. ``add_memory(scope="session")`` is honored (and downgrades to global only
   when there is no active session).
3. When the LLM passes ``scope="auto"`` (or omits it), and the user message
   contains an explicit cross-session intent ("永久保存", "下次新会话也能查到",
   "长期记住", ...), the memory is stored as owner-scoped ``user`` instead of being
   auto-downgraded to session.
4. The new broader ``_STABLE_FACT_RE`` matches LLM-rewritten phrasings such as
   "用户陈彦廷居住在重庆" (which the previous narrow regex missed).
"""

from openakita.tools.handlers.memory import MemoryHandler


class _StubManager:
    """Memory manager double that mirrors MemoryManager._current_write_scope."""

    def __init__(self, session_id: str = "session-test"):
        self._current_session_id = session_id

    def _current_write_scope(self) -> tuple[str, str]:
        sid = (self._current_session_id or "").strip()
        if sid:
            return "session", sid
        return "user", ""


def test_explicit_scope_global_always_wins():
    scope, owner, tags, _note = MemoryHandler._memory_scope_for_manual_add(
        "用户喜欢吃辣",
        "fact",
        _StubManager(),
        explicit_scope="global",
    )
    assert scope == "user"
    assert owner == ""
    assert "explicit-global" in tags


def test_explicit_scope_session_with_active_session():
    scope, owner, _tags, _note = MemoryHandler._memory_scope_for_manual_add(
        "用户的临时任务备忘",
        "fact",
        _StubManager(session_id="conv-42"),
        explicit_scope="session",
    )
    assert scope == "session"
    assert owner == "conv-42"


def test_explicit_scope_session_no_active_session_falls_back_to_global():
    scope, owner, _tags, _note = MemoryHandler._memory_scope_for_manual_add(
        "用户的临时任务备忘",
        "fact",
        _StubManager(session_id=""),
        explicit_scope="session",
    )
    # No live session → don't drop the memory; persist as current-user memory.
    assert scope == "user"
    assert owner == ""


def test_user_persistence_intent_upgrades_to_global():
    scope, _owner, tags, _note = MemoryHandler._memory_scope_for_manual_add(
        "用户的项目代号叫 SEAGULL",
        "fact",
        _StubManager(),
        explicit_scope="auto",
        user_intent_hint="把这个永久保存下来，下次新会话也能查到",
    )
    assert scope == "user"
    assert "user-requested-global" in tags


def test_long_term_intent_keyword_upgrades_to_global():
    scope, _owner, _tags, _note = MemoryHandler._memory_scope_for_manual_add(
        "项目要按月发布",
        "fact",
        _StubManager(),
        explicit_scope="auto",
        user_intent_hint="请长期记住这件事",
    )
    assert scope == "user"


def test_stable_fact_regex_matches_llm_rewritten_phrasings():
    # P0-1 case: "用户陈彦廷居住在重庆" — the old regex required a verb right
    # after "用户", missing this LLM-rewrite that puts a name in between.
    pattern = MemoryHandler._STABLE_FACT_RE
    assert pattern.search("用户陈彦廷居住在重庆")
    assert pattern.search("用户彦廷在重庆工作")
    assert pattern.search("项目代号叫 SEAGULL")
    # And the durable adverbs are still matched.
    assert pattern.search("用户喜欢中文回复")
    # Negative: pure one-off task narration should NOT match _STABLE_FACT_RE.
    assert not pattern.search("当前需要下载这个文件")


def test_durable_type_defaults_to_user_scope_even_without_explicit_scope():
    scope, _owner, _tags, _note = MemoryHandler._memory_scope_for_manual_add(
        "对所有失败请求都要重试两次",
        "rule",
        _StubManager(),
    )
    assert scope == "user"


def test_one_off_task_falls_back_to_session_with_actionable_note():
    scope, _owner, tags, note = MemoryHandler._memory_scope_for_manual_add(
        "用户当前希望整理工作区文件",
        "fact",
        _StubManager(session_id="conv-1"),
    )
    assert scope == "session"
    assert "session-only" in tags
    assert note  # non-empty hint that the model can act on
