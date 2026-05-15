"""Defaults that keep execution permissive and avoid unnecessary prompts."""

from openakita.config import Settings


def test_force_tool_call_defaults_trust_model_judgment(tmp_path):
    settings = Settings(project_root=tmp_path)

    assert settings.force_tool_call_max_retries == 0
    assert settings.force_tool_call_im_floor == 0
    assert settings.confirmation_text_max_retries == 1


def test_org_command_watchdog_defaults_are_soft_progress_based(tmp_path):
    settings = Settings(project_root=tmp_path)

    assert settings.org_command_stuck_warn_secs == 900
    assert settings.org_command_stuck_autostop_secs == 3600
    assert settings.org_command_timeout_secs == 0
    # Deadlock early-stop is independent of the no-progress paths above:
    # once the org goes "全员 IDLE + open chain" we don't want to wait the
    # 1-hour autostop, 90 s is enough to be sure no agent is going to wake.
    assert settings.org_command_deadlock_grace_secs == 90
