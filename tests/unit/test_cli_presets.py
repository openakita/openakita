"""Tests for CLI-backed system presets."""
from __future__ import annotations

from openakita.agents.presets import SYSTEM_PRESETS, get_preset_by_id
from openakita.agents.profile import (
    AgentProfile,
    AgentType,
    CliPermissionMode,
)
from openakita.agents.cli_detector import CliProviderId


def test_claude_code_pair_preset_exists():
    preset = get_preset_by_id("claude-code-pair")
    assert preset is not None
    assert preset.type == AgentType.EXTERNAL_CLI
    assert preset.cli_provider_id == CliProviderId.CLAUDE_CODE
    assert preset.cli_permission_mode == CliPermissionMode.WRITE
    assert preset.fallback_profile_id == "codex-writer"
    assert preset.category == "cli-agents"
    assert preset.created_by == "system"
    assert preset.icon  # non-empty


def test_codex_writer_preset_exists():
    preset = get_preset_by_id("codex-writer")
    assert preset is not None
    assert preset.type == AgentType.EXTERNAL_CLI
    assert preset.cli_provider_id == CliProviderId.CODEX
    assert preset.cli_permission_mode == CliPermissionMode.WRITE
    assert preset.fallback_profile_id == "local-goose"
    assert preset.category == "cli-agents"


def test_local_goose_preset_exists():
    preset = get_preset_by_id("local-goose")
    assert preset is not None
    assert preset.type == AgentType.EXTERNAL_CLI
    assert preset.cli_provider_id == CliProviderId.GOOSE
    assert preset.cli_permission_mode == CliPermissionMode.WRITE
    assert preset.fallback_profile_id == "default"  # goose has no further CLI sibling
    assert preset.category == "cli-agents"


def test_cli_preset_fallback_chain_forms_a_line():
    """claude-code-pair -> codex-writer -> local-goose -> default -- no cycles."""
    chain = []
    current = "claude-code-pair"
    seen = set()
    while current and current not in seen:
        seen.add(current)
        chain.append(current)
        p = get_preset_by_id(current)
        current = p.fallback_profile_id if p else None
    assert chain == ["claude-code-pair", "codex-writer", "local-goose", "default"]
