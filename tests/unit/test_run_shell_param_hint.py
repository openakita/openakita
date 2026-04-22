"""Tests for run_shell missing-command hint (A3).

Validates that ``FilesystemHandler._format_run_shell_missing_command`` returns
an instruction-rich error when ``command`` is missing, including detection of
common LLM mis-named alias keys (``script`` / ``cmd`` / ``shell`` / ``bash`` / ``code``).
"""

from openakita.tools.handlers.filesystem import FilesystemHandler


class TestRunShellParamHint:
    def test_no_params_hints_usage(self):
        msg = FilesystemHandler._format_run_shell_missing_command({})
        assert "❌ run_shell 缺少必要参数 'command'" in msg
        assert "Usage: run_shell(command=" in msg
        assert "You passed keys: []" in msg
        assert "常见误传字段" in msg

    def test_alias_script_detected(self):
        msg = FilesystemHandler._format_run_shell_missing_command(
            {"script": "ls -la"}
        )
        assert "你传了 'script'" in msg
        assert "改名为 'command'" in msg

    def test_alias_cmd_detected(self):
        msg = FilesystemHandler._format_run_shell_missing_command({"cmd": "pwd"})
        assert "你传了 'cmd'" in msg

    def test_alias_bash_detected(self):
        msg = FilesystemHandler._format_run_shell_missing_command({"bash": "echo"})
        assert "你传了 'bash'" in msg

    def test_alias_empty_value_does_not_count(self):
        # If user passes alias with empty value, treat as no alias.
        msg = FilesystemHandler._format_run_shell_missing_command({"script": ""})
        assert "你传了" not in msg
        assert "常见误传字段" in msg

    def test_unknown_keys_listed(self):
        msg = FilesystemHandler._format_run_shell_missing_command(
            {"foo": "bar", "baz": "qux"}
        )
        # Both keys should appear in the listing
        assert "foo" in msg
        assert "baz" in msg

    def test_non_dict_safe(self):
        # Defensive: should not raise on weird input
        msg = FilesystemHandler._format_run_shell_missing_command(None)  # type: ignore[arg-type]
        assert "❌ run_shell 缺少必要参数" in msg
