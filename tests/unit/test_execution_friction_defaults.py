"""Defaults that keep execution permissive and avoid unnecessary prompts."""

from openakita.config import Settings


def test_force_tool_call_defaults_trust_model_judgment(tmp_path):
    settings = Settings(project_root=tmp_path)

    assert settings.force_tool_call_max_retries == 0
    assert settings.force_tool_call_im_floor == 0
    assert settings.confirmation_text_max_retries == 1
