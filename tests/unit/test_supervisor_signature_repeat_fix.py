"""Regression tests for the supervisor "5-call hard TERMINATE" bug.

Background:
    Historically ``_check_signature_repeat`` triggered TERMINATE when the
    *tool name* (with parameters stripped) appeared >= 5 times in the recent
    window. That meant:

      * 5 different ``write_file`` calls (different paths)
      * 5 different ``run_shell`` calls (different commands)
      * 5 different ``update_todo_step`` calls (different step ids / status)

    were all force-terminated as a "dead loop" even though every invocation
    had unique parameters.

    The fix collapses TERMINATE / STRATEGY_SWITCH down to **exact-signature**
    repetition only (``most_common_count``). Tool-name repetition with varying
    parameters now caps at NUDGE.

These tests pin the new behaviour so future refactors do not reopen the bug.
"""

from __future__ import annotations

from openakita.core.supervisor import (
    InterventionLevel,
    RuntimeSupervisor,
    SIGNATURE_REPEAT_STRATEGY_SWITCH,
    SIGNATURE_REPEAT_TERMINATE,
    UNPRODUCTIVE_ADMIN_TOOLS,
)


# ---------------------------------------------------------------------------
# A. Tool name repeats with VARYING parameters must NOT TERMINATE
# ---------------------------------------------------------------------------


class TestVaryingParamsDoNotTerminate:
    def test_run_shell_with_distinct_commands_does_not_intervene(self):
        sup = RuntimeSupervisor(enabled=True)
        for i in range(8):
            sup.record_tool_signature(f"run_shell(hash_{i})")
        out = sup._check_signature_repeat(iteration=8)
        assert out is None, (
            "varying-arg run_shell is normal progress and should not be nudged "
            f"as repetition, got {out}"
        )

    def test_run_powershell_with_distinct_commands_does_not_intervene(self):
        sup = RuntimeSupervisor(enabled=True)
        for i in range(8):
            sup.record_tool_signature(f"run_powershell(hash_{i})")
        out = sup._check_signature_repeat(iteration=8)
        assert out is None, (
            "varying-arg run_powershell is normal progress and should not be "
            f"nudged as repetition, got {out}"
        )

    def test_write_file_to_distinct_paths_does_not_intervene(self):
        sup = RuntimeSupervisor(enabled=True)
        for i in range(8):
            sup.record_tool_signature(f"write_file(path_hash_{i})")
        out = sup._check_signature_repeat(iteration=8)
        assert out is None

    def test_update_todo_step_with_distinct_steps_does_not_terminate(self):
        """The original symptom: plan推进每步 in_progress + completed 两次
        update_todo_step，参数全不同，不应被当死循环。"""
        sup = RuntimeSupervisor(enabled=True)
        for i in range(8):
            sup.record_tool_signature(f"update_todo_step(step_{i})")
        out = sup._check_signature_repeat(iteration=8)
        assert out is None

    def test_web_search_with_distinct_queries_does_not_intervene(self):
        sup = RuntimeSupervisor(enabled=True)
        for i in range(8):
            sup.record_tool_signature(f"web_search(query_hash_{i})")
        out = sup._check_signature_repeat(iteration=8)
        assert out is None

    def test_any_tool_with_distinct_args_does_not_intervene(self):
        sup = RuntimeSupervisor(enabled=True)
        for i in range(8):
            sup.record_tool_signature(f"custom_tool(arg_hash_{i})")
        out = sup._check_signature_repeat(iteration=8)
        assert out is None

    def test_terminal_file_polling_is_capped_at_nudge(self):
        """Monitoring a background command by repeatedly reading its terminal
        file is expected and must not terminate the task."""
        sup = RuntimeSupervisor(enabled=True)
        for _ in range(8):
            sup.record_tool_signature("read_file_terminal(same_hash)")

        out = sup._check_signature_repeat(iteration=8)

        assert out is not None
        assert out.level == InterventionLevel.NUDGE
        assert out.should_inject_prompt is False
        assert out.prompt_injection == ""
        assert not out.should_terminate
        assert not out.should_rollback


# ---------------------------------------------------------------------------
# B. Exact signature repeats MUST still TERMINATE (real dead loop)
# ---------------------------------------------------------------------------


class TestExactRepeatStillTerminates:
    def test_same_exact_signature_repeated_terminates(self):
        sup = RuntimeSupervisor(enabled=True)
        for _ in range(SIGNATURE_REPEAT_TERMINATE):
            sup.record_tool_signature("read_file(same_hash)")
        out = sup._check_signature_repeat(iteration=SIGNATURE_REPEAT_TERMINATE)
        assert out is not None
        assert out.level == InterventionLevel.TERMINATE
        assert out.should_terminate

    def test_alternating_two_signatures_strategy_switch(self):
        """1-2 种签名 ping-pong 可能是正常检查/执行流，只记录软事件。"""
        sup = RuntimeSupervisor(enabled=True)
        for i in range(8):
            sup.record_tool_signature(
                "tool_a(h1)" if i % 2 == 0 else "tool_b(h2)"
            )
        out = sup._check_signature_repeat(iteration=8)
        assert out is not None
        assert out.level == InterventionLevel.NUDGE
        assert out.should_inject_prompt is False
        assert not out.should_rollback
        assert not out.should_terminate

    def test_non_consecutive_repeats_do_not_strategy_switch(self):
        """最近窗口内累计重复不等于连续重复，不应回滚或注入提示。"""
        sup = RuntimeSupervisor(enabled=True)
        for sig in (
            "read_file(a)",
            "write_file(b)",
            "read_file(a)",
            "grep(c)",
            "read_file(a)",
            "glob(d)",
            "read_file(a)",
            "list_directory(e)",
        ):
            sup.record_tool_signature(sig)

        out = sup._check_signature_repeat(iteration=8)

        assert out is None

    def test_consecutive_exact_repeat_strategy_switch_injects_prompt(self):
        sup = RuntimeSupervisor(enabled=True)
        for _ in range(4):
            sup.record_tool_signature("read_file(same_hash)")

        out = sup._check_signature_repeat(iteration=4)

        assert out is not None
        assert out.level == InterventionLevel.STRATEGY_SWITCH
        assert out.should_rollback
        assert out.should_inject_prompt
        assert "完全相同参数连续重复" in out.prompt_injection

    def test_repeated_web_fetch_strategy_switch_throttles_network_tool(self):
        sup = RuntimeSupervisor(enabled=True)
        for _ in range(SIGNATURE_REPEAT_STRATEGY_SWITCH):
            sup.record_tool_signature("web_fetch(same_url_hash)")

        out = sup._check_signature_repeat(iteration=SIGNATURE_REPEAT_STRATEGY_SWITCH)

        assert out is not None
        assert out.level == InterventionLevel.STRATEGY_SWITCH
        assert out.throttled_tool_names == ["web_fetch"]
        assert "缓存摘要" in out.prompt_injection


# ---------------------------------------------------------------------------
# C. UNPRODUCTIVE_ADMIN_TOOLS only contains read-only / query tools
# ---------------------------------------------------------------------------


class TestUnproductiveAdminPruned:
    def test_progress_tools_not_in_unproductive_set(self):
        for tool in (
            "create_todo",
            "update_todo_step",
            "complete_todo",
            "add_memory",
        ):
            assert tool not in UNPRODUCTIVE_ADMIN_TOOLS, (
                f"{tool} 是状态推进工具，不应被列为 unproductive"
            )

    def test_pure_query_tools_remain_in_unproductive_set(self):
        for tool in ("get_todo_status", "search_memory", "list_directory"):
            assert tool in UNPRODUCTIVE_ADMIN_TOOLS

    def test_five_consecutive_update_todo_step_not_unproductive(self):
        """连续 5 次 update_todo_step（plan 推进）不再触发 unproductive 回滚。"""
        sup = RuntimeSupervisor(enabled=True)
        for i in range(5):
            sup.record_tool_call(
                tool_name="update_todo_step",
                params={"step_id": f"s{i}", "status": "completed"},
                success=True,
                iteration=i,
                result_text="✅ updated",
            )
        out = sup._check_unproductive_loop(iteration=5)
        assert out is None, (
            f"连续 5 次 update_todo_step 不应被判 unproductive，got {out}"
        )

    def test_five_consecutive_get_todo_status_still_unproductive(self):
        """纯查询工具连续 5 次 仍然触发 unproductive。"""
        sup = RuntimeSupervisor(enabled=True)
        for i in range(5):
            sup.record_tool_call(
                tool_name="get_todo_status",
                params={},
                success=True,
                iteration=i,
                result_text="ok",
            )
        out = sup._check_unproductive_loop(iteration=5)
        assert out is not None
        assert out.level == InterventionLevel.STRATEGY_SWITCH


class TestNudgesDoNotInjectPrompt:
    def test_exact_repeat_nudge_is_log_only(self):
        sup = RuntimeSupervisor(enabled=True, signature_repeat_terminate=99)
        for _ in range(3):
            sup.record_tool_signature("read_file(same_hash)")

        out = sup._check_signature_repeat(iteration=3)

        assert out is not None
        assert out.level == InterventionLevel.NUDGE
        assert out.should_inject_prompt is False
        assert out.prompt_injection == ""

    def test_edit_thrashing_nudge_is_log_only(self):
        sup = RuntimeSupervisor(enabled=True)
        for i in range(3):
            sup.record_tool_call("read_file", {"path": "a.py"}, iteration=i * 2)
            sup.record_tool_call("write_file", {"path": "a.py"}, iteration=i * 2 + 1)

        out = sup._check_edit_thrashing(iteration=6)

        assert out is not None
        assert out.level == InterventionLevel.NUDGE
        assert out.should_inject_prompt is False
        assert out.prompt_injection == ""

    def test_unproductive_three_call_nudge_is_log_only(self):
        sup = RuntimeSupervisor(enabled=True)
        for i in range(3):
            sup.record_tool_call("get_todo_status", {}, iteration=i)

        out = sup._check_unproductive_loop(iteration=3)

        assert out is not None
        assert out.level == InterventionLevel.NUDGE
        assert out.should_inject_prompt is False
        assert out.prompt_injection == ""

