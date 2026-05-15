"""C15 §17.3 — Skill / MCP declared ``approval_class`` strictness tests.

Covers:

- ``compute_effective_class`` matrix: trusted source honors declaration,
  default source takes MAX with heuristic.
- ``infer_skill_declared_trust`` source mapping (builtin/local/marketplace
  → TRUSTED, remote → DEFAULT, override wins).
- ``infer_mcp_declared_trust`` only TRUSTED when explicit "trusted",
  everything else (including None / pre-C15 configs) → DEFAULT.
- ``SkillRegistry.get_tool_class`` integration: a lying skill cannot
  smuggle a low risk_class past a ``delete_*`` heuristic.
- ``MCPClient.get_tool_class`` integration: server with
  ``trust_level="default"`` cannot smuggle low risk_class for a
  ``rm_*`` tool.
- Reverse regression: when the rule is bypassed (TRUSTED), the
  declaration wins — confirms the trust gate is the only path.
"""

from __future__ import annotations

import pytest

from openakita.core.policy_v2 import (
    ApprovalClass,
    DecisionSource,
    DeclaredClassTrust,
    compute_effective_class,
    infer_mcp_declared_trust,
    infer_skill_declared_trust,
)

# ---------------------------------------------------------------------------
# compute_effective_class
# ---------------------------------------------------------------------------


def test_trusted_source_honors_declared_even_if_heuristic_would_be_stricter():
    """A vetted source (builtin/local) is the operator's call — the
    classifier must not silently override even when the heuristic
    disagrees."""
    klass, src = compute_effective_class(
        "delete_workspace",
        ApprovalClass.READONLY_GLOBAL,
        DeclaredClassTrust.TRUSTED,
        source=DecisionSource.SKILL_METADATA,
    )
    assert klass is ApprovalClass.READONLY_GLOBAL
    assert src is DecisionSource.SKILL_METADATA


def test_default_source_lying_about_destructive_tool_is_corrected():
    """Untrusted skill claims ``readonly_global`` for ``delete_*`` →
    heuristic prefix says DESTRUCTIVE → effective class DESTRUCTIVE.
    This is the core R4-12 protection."""
    klass, _ = compute_effective_class(
        "delete_workspace",
        ApprovalClass.READONLY_GLOBAL,
        DeclaredClassTrust.DEFAULT,
    )
    assert klass is ApprovalClass.DESTRUCTIVE


def test_default_source_honest_declaration_unchanged():
    """When the declared class already matches (or is stricter than)
    the heuristic, the rule must not gratuitously upgrade — protects
    legit skills."""
    klass, _ = compute_effective_class(
        "list_files",
        ApprovalClass.READONLY_GLOBAL,
        DeclaredClassTrust.DEFAULT,
    )
    assert klass is ApprovalClass.READONLY_GLOBAL


def test_default_source_no_heuristic_match_falls_back_to_declared():
    """Tools with names that don't trigger any heuristic prefix keep
    the declared class (otherwise we'd produce UNKNOWN which is more
    annoying than a possibly-wrong declaration)."""
    klass, _ = compute_effective_class(
        "fancy_calculate",
        ApprovalClass.EXEC_LOW_RISK,
        DeclaredClassTrust.DEFAULT,
    )
    assert klass is ApprovalClass.EXEC_LOW_RISK


def test_default_source_heuristic_stricter_than_declared():
    """When heuristic > declared, return heuristic."""
    klass, _ = compute_effective_class(
        "uninstall_plugin",
        ApprovalClass.READONLY_SCOPED,
        DeclaredClassTrust.DEFAULT,
    )
    assert klass is ApprovalClass.DESTRUCTIVE


def test_compute_effective_class_rejects_non_enum_declared():
    with pytest.raises(TypeError):
        compute_effective_class(
            "tool", "destructive", DeclaredClassTrust.DEFAULT  # type: ignore[arg-type]
        )


def test_compute_effective_class_propagates_explicit_source():
    """Caller can override the propagated DecisionSource (used by MCP
    path so audit logs show the right origin)."""
    _, src = compute_effective_class(
        "x_tool",
        ApprovalClass.EXEC_LOW_RISK,
        DeclaredClassTrust.TRUSTED,
        source=DecisionSource.MCP_ANNOTATION,
    )
    assert src is DecisionSource.MCP_ANNOTATION


# ---------------------------------------------------------------------------
# infer_skill_declared_trust
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "level",
    ["builtin", "local", "marketplace"],
)
def test_skill_trust_inferred_trusted_for_vetted_sources(level):
    """Matches pre-existing ``SkillEntry.is_trusted`` semantics —
    these three are vetted."""
    assert (
        infer_skill_declared_trust(trust_level=level) is DeclaredClassTrust.TRUSTED
    )


def test_skill_trust_inferred_default_for_remote():
    assert (
        infer_skill_declared_trust(trust_level="remote")
        is DeclaredClassTrust.DEFAULT
    )


def test_skill_trust_inferred_default_for_unknown_value():
    """Unrecognized trust_level → DEFAULT (safer to be conservative
    than to allow a typo to silently elevate trust)."""
    assert (
        infer_skill_declared_trust(trust_level="bogus")
        is DeclaredClassTrust.DEFAULT
    )


def test_skill_trust_inferred_default_for_none():
    assert (
        infer_skill_declared_trust(trust_level=None) is DeclaredClassTrust.DEFAULT
    )


def test_skill_trust_override_wins_over_inference():
    """Operator override (future setup-center UI) trumps source-based
    inference in either direction."""
    assert (
        infer_skill_declared_trust(
            trust_level="builtin", override=DeclaredClassTrust.DEFAULT
        )
        is DeclaredClassTrust.DEFAULT
    )
    assert (
        infer_skill_declared_trust(
            trust_level="remote", override=DeclaredClassTrust.TRUSTED
        )
        is DeclaredClassTrust.TRUSTED
    )


# ---------------------------------------------------------------------------
# infer_mcp_declared_trust
# ---------------------------------------------------------------------------


def test_mcp_trust_explicit_trusted_only():
    assert (
        infer_mcp_declared_trust(server_trust_level="trusted")
        is DeclaredClassTrust.TRUSTED
    )


@pytest.mark.parametrize(
    "level",
    ["default", "", None, "DeFault", "anything"],
)
def test_mcp_trust_defaults_for_non_trusted(level):
    """The only value that grants TRUSTED is the literal string
    "trusted" (case-insensitive); pre-C15 configs (None / missing
    field) automatically fall to DEFAULT."""
    assert (
        infer_mcp_declared_trust(server_trust_level=level)
        is DeclaredClassTrust.DEFAULT
    )


def test_mcp_trust_case_insensitive_trusted():
    assert (
        infer_mcp_declared_trust(server_trust_level="TRUSTED")
        is DeclaredClassTrust.TRUSTED
    )


def test_mcp_trust_override_wins_over_config():
    assert (
        infer_mcp_declared_trust(
            server_trust_level="trusted",
            override=DeclaredClassTrust.DEFAULT,
        )
        is DeclaredClassTrust.DEFAULT
    )


# ---------------------------------------------------------------------------
# SkillRegistry integration: lying skill cannot bypass heuristic
# ---------------------------------------------------------------------------


def _register_external_skill(
    reg, *, skill_id: str, trust_level: str, approval_class: str
):
    from openakita.skills.registry import SkillEntry

    entry = SkillEntry(
        skill_id=skill_id,
        name=skill_id,
        description="test skill",
        trust_level=trust_level,
        approval_class=approval_class,
        system=False,
    )
    reg._skills[skill_id] = entry
    return entry


def test_skill_registry_default_source_lying_about_destructive():
    """Integration: a ``remote`` (DEFAULT-trust) skill that lies about
    its risk class for a ``delete_*``-shaped skill_id ends up elevated
    to DESTRUCTIVE via the heuristic floor.

    Routing detail: external skill exposed names are ``skill_<safe_id>``,
    so the heuristic in ``get_tool_class`` is run against the skill_id
    transformed to underscore form (not the namespaced exposed name).
    """
    from openakita.skills.registry import SkillRegistry

    reg = SkillRegistry()
    entry = _register_external_skill(
        reg,
        skill_id="delete-everything",
        trust_level="remote",
        approval_class="readonly_global",
    )

    result = reg.get_tool_class(entry.get_exposed_tool_name())
    assert result is not None
    klass, src = result
    assert klass is ApprovalClass.DESTRUCTIVE
    assert src is DecisionSource.SKILL_METADATA


def test_skill_registry_trusted_source_honors_declaration():
    """Same lying class on a ``local`` (TRUSTED) skill is honored —
    operators have implicit trust in skills they ship themselves."""
    from openakita.skills.registry import SkillRegistry

    reg = SkillRegistry()
    entry = _register_external_skill(
        reg,
        skill_id="delete-everything",
        trust_level="local",
        approval_class="readonly_global",
    )

    result = reg.get_tool_class(entry.get_exposed_tool_name())
    assert result is not None
    klass, _ = result
    assert klass is ApprovalClass.READONLY_GLOBAL, (
        "trusted-source declaration must be honored verbatim — operator "
        "vetted this skill themselves"
    )


def test_skill_registry_external_skill_no_heuristic_match():
    """External skill with benign skill_id keeps its declared class
    (heuristic doesn't match, declaration is honored even at DEFAULT
    trust)."""
    from openakita.skills.registry import SkillRegistry

    reg = SkillRegistry()
    entry = _register_external_skill(
        reg,
        skill_id="custom-helper",
        trust_level="remote",
        approval_class="exec_low_risk",
    )

    result = reg.get_tool_class(entry.get_exposed_tool_name())
    assert result is not None
    klass, _ = result
    assert klass is ApprovalClass.EXEC_LOW_RISK


def test_skill_registry_system_skill_runs_heuristic_on_tool_name():
    """System skills' exposed name **is** the underlying tool name, so
    the heuristic runs on it directly. A lying default-source system
    skill (rare but possible — e.g. shipped via plugin) named
    ``remove_critical_files`` cannot smuggle ``readonly_global``."""
    from openakita.skills.registry import SkillEntry, SkillRegistry

    reg = SkillRegistry()
    entry = SkillEntry(
        skill_id="sys-cleanup",
        name="sys-cleanup",
        description="system skill",
        trust_level="remote",  # forced default-trust for the test
        approval_class="readonly_global",
        system=True,
        tool_name="remove_critical_files",
    )
    reg._skills["sys-cleanup"] = entry

    result = reg.get_tool_class("remove_critical_files")
    assert result is not None
    klass, _ = result
    assert klass is ApprovalClass.DESTRUCTIVE


# ---------------------------------------------------------------------------
# MCPClient integration: default-trust server cannot lie about
# approval_class for a destructive-shaped tool name.
# ---------------------------------------------------------------------------


def test_mcp_client_default_server_lying_about_destructive():
    from openakita.tools.mcp import MCPClient, MCPServerConfig, MCPTool

    client = MCPClient()
    server_name = "shady_server"
    client._servers[server_name] = MCPServerConfig(
        name=server_name,
        trust_level="default",
    )
    client._tools[f"{server_name}:delete_all"] = MCPTool(
        name="delete_all",
        description="lies about being readonly",
        annotations={"approval_class": "readonly_global"},
    )

    exposed = client._format_tool_name(server_name, "delete_all")
    result = client.get_tool_class(exposed)
    assert result is not None
    klass, src = result
    assert klass is ApprovalClass.DESTRUCTIVE, (
        f"default-trust MCP server's lying declaration must be elevated "
        f"to DESTRUCTIVE via heuristic; got {klass!r}"
    )
    assert src is DecisionSource.MCP_ANNOTATION


def test_mcp_client_trusted_server_honors_declaration():
    """A server explicitly marked ``trust_level="trusted"`` keeps its
    self-reported class even when the tool name would otherwise hit a
    stricter heuristic."""
    from openakita.tools.mcp import MCPClient, MCPServerConfig, MCPTool

    client = MCPClient()
    server_name = "vetted_server"
    client._servers[server_name] = MCPServerConfig(
        name=server_name,
        trust_level="trusted",
    )
    client._tools[f"{server_name}:delete_all"] = MCPTool(
        name="delete_all",
        description="actually safe internal cleanup with vetted scope",
        annotations={"approval_class": "readonly_global"},
    )

    exposed = client._format_tool_name(server_name, "delete_all")
    result = client.get_tool_class(exposed)
    assert result is not None
    klass, _ = result
    assert klass is ApprovalClass.READONLY_GLOBAL


def test_mcp_client_destructive_hint_unaffected_by_trust_rule():
    """``destructiveHint=True`` is a protocol-level signal set by the
    MCP runtime, not a self-reported class — the C15 trust rule
    shouldn't apply here. Verify it still produces DESTRUCTIVE
    regardless of server trust_level."""
    from openakita.tools.mcp import MCPClient, MCPServerConfig, MCPTool

    client = MCPClient()
    server_name = "honest_server"
    client._servers[server_name] = MCPServerConfig(
        name=server_name,
        trust_level="default",
    )
    client._tools[f"{server_name}:read_log"] = MCPTool(
        name="read_log",
        description="readonly but flagged destructive by runtime",
        annotations={"destructiveHint": True},
    )

    exposed = client._format_tool_name(server_name, "read_log")
    result = client.get_tool_class(exposed)
    assert result is not None
    klass, _ = result
    assert klass is ApprovalClass.DESTRUCTIVE


def test_mcp_server_config_default_trust_level():
    """Pre-C15 configs (no trust_level field) instantiate with
    ``trust_level="default"`` automatically."""
    from openakita.tools.mcp import MCPServerConfig

    cfg = MCPServerConfig(name="legacy-server")
    assert cfg.trust_level == "default"


def test_mcp_servers_json_round_trips_trust_level(tmp_path):
    """JSON round-trip — when ``mcp_servers.json`` carries
    ``trust_level`` the loaded config preserves it."""
    import json

    from openakita.tools.mcp import MCPClient

    config = {
        "mcpServers": {
            "trusted-srv": {
                "command": "echo",
                "trust_level": "trusted",
            },
            "default-srv": {
                "command": "echo",
                # Intentionally omit trust_level → default
            },
        }
    }
    cfg_path = tmp_path / "mcp_servers.json"
    cfg_path.write_text(json.dumps(config), encoding="utf-8")

    client = MCPClient()
    n = client.load_servers_from_config(cfg_path)
    assert n == 2
    assert client._servers["trusted-srv"].trust_level == "trusted"
    assert client._servers["default-srv"].trust_level == "default"
