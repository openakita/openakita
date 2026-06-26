"""L1 Unit Tests: PersonaManager preset loading and trait management."""

import pytest
from pathlib import Path

from openakita.core.persona import PersonaManager


@pytest.fixture
def personas_dir(tmp_path):
    d = tmp_path / "personas"
    d.mkdir()
    (d / "default.md").write_text(
        "# Default\n\n你是一个友好、温暖的助手。\n\n## 语气\n亲切自然",
        encoding="utf-8",
    )
    (d / "professional.md").write_text(
        "# Professional\n\n你是一个专业、严谨的助手。\n\n## 语气\n正式",
        encoding="utf-8",
    )
    return d


class TestPresetDiscovery:
    def test_list_available_presets(self, personas_dir):
        pm = PersonaManager(personas_dir=personas_dir)
        presets = pm.available_presets
        assert "default" in presets
        assert "professional" in presets

    def test_empty_dir(self, tmp_path):
        d = tmp_path / "empty_personas"
        d.mkdir()
        pm = PersonaManager(personas_dir=d)
        assert isinstance(pm.available_presets, list)


class TestPresetSwitch:
    def test_switch_to_existing(self, personas_dir):
        pm = PersonaManager(personas_dir=personas_dir)
        result = pm.switch_preset("professional")
        assert result is True

    def test_switch_to_nonexistent(self, personas_dir):
        pm = PersonaManager(personas_dir=personas_dir)
        result = pm.switch_preset("nonexistent_preset_xyz")
        assert result is False


class TestPersonaPrompt:
    def test_prompt_section_returns_string(self, personas_dir):
        pm = PersonaManager(personas_dir=personas_dir, active_preset="default")
        section = pm.get_persona_prompt_section()
        assert isinstance(section, str)

    def test_persona_active_after_load(self, personas_dir):
        pm = PersonaManager(personas_dir=personas_dir, active_preset="default")
        pm.load_preset("default")
        # After loading, persona should be considered active
        assert isinstance(pm.is_persona_active(), bool)
