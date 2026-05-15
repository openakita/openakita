"""C15 §17.3 — Skill / MCP self-declared ``approval_class`` strictness rule.

Motivation
==========

Third-party capability providers (Skill packages, MCP servers, plugins)
can declare their own :class:`ApprovalClass` in their manifest:

- ``SKILL.md`` frontmatter: ``approval_class: readonly_global``
- MCP ``tool.annotations``: ``{"approval_class": "readonly_global"}``

This declaration is convenient but **not authenticated** — a malicious
or buggy skill could claim ``readonly_global`` while actually performing
``rm -rf``.

R4-12 / R4-13 / R5-21 want a strictness rule that survives a lying
declaration without forcing the user to manually classify every third-
party tool.

Rule
====

Each declaration is paired with a :class:`DeclaredClassTrust` value:

- ``TRUSTED``: the operator has vetted the source (or it's a first-party
  built-in / locally-authored skill). Declared ``approval_class`` is
  honored verbatim.
- ``DEFAULT``: declared ``approval_class`` is treated as a **hint**.
  The classifier additionally computes the heuristic class from the
  tool name (``delete_*`` → ``DESTRUCTIVE`` etc.) and the effective
  class is ``most_strict([declared, heuristic])``.

So a default-trusted skill that names its tool ``delete_workspace`` and
declares ``approval_class: readonly_global`` ends up classified as
``DESTRUCTIVE`` (heuristic prefix wins).

Default trust inference
=======================

When a caller does not pass an explicit :class:`DeclaredClassTrust`,
we infer one from the source:

- Skill: ``builtin`` / ``local`` / ``marketplace`` → TRUSTED
  (consistent with the pre-existing :pyattr:`SkillEntry.is_trusted`
  derivation — only ``remote`` git/URL skills downgrade by default).
- MCP: server config's ``trust_level`` field on
  :class:`MCPServerConfig`. Missing or ``"default"`` → DEFAULT;
  ``"trusted"`` → TRUSTED. New field defaults to ``"default"`` so
  pre-C15 configs automatically get the safer treatment.

Operators can flip individual entries to TRUSTED via setup-center
(persistence wiring is a follow-up; this module only encodes the rule
so the policy decision is consistent regardless of where the operator
override is stored).
"""

from __future__ import annotations

import logging
from enum import StrEnum

from .classifier import heuristic_classify
from .enums import ApprovalClass, DecisionSource, most_strict

logger = logging.getLogger(__name__)


class DeclaredClassTrust(StrEnum):
    """How much the operator trusts a third-party class declaration."""

    DEFAULT = "default"
    """Take the strict-max of the declaration and a heuristic — protects
    against a lying skill / mcp manifest."""

    TRUSTED = "trusted"
    """Honor the declaration verbatim — for first-party / vetted sources."""


def compute_effective_class(
    tool_name: str,
    declared: ApprovalClass,
    trust: DeclaredClassTrust,
    *,
    source: DecisionSource = DecisionSource.SKILL_METADATA,
) -> tuple[ApprovalClass, DecisionSource]:
    """Apply the §17.3 strictness rule to a self-declared class.

    Args:
        tool_name: The exposed tool name (used to derive the heuristic
            class when the declaration is not fully trusted).
        declared: The class the manifest / annotation declared.
        trust: Operator trust level for this source.
        source: The :class:`DecisionSource` to propagate. Defaults to
            ``SKILL_METADATA``; pass ``MCP_ANNOTATION`` /
            ``PLUGIN_PREFIX`` as appropriate.

    Returns:
        Tuple of ``(effective_class, source)``. When the heuristic wins
        over a default-trusted declaration the source is still the
        *original* declaration source — the heuristic is invisible to
        downstream auditors because it merely *constrained* the
        declaration. This keeps audit trails consistent with the
        identity of the manifest that triggered classification, while
        the strictness guarantee is enforced by the returned class.

    Raises:
        TypeError: ``declared`` is not an :class:`ApprovalClass` —
            caller should validate the manifest value before calling.
    """
    if not isinstance(declared, ApprovalClass):
        raise TypeError(
            f"declared must be ApprovalClass, got {type(declared).__name__}"
        )

    if trust is DeclaredClassTrust.TRUSTED:
        return declared, source

    heuristic = heuristic_classify(tool_name)
    if heuristic is None:
        return declared, source

    effective, _ = most_strict([(declared, source), (heuristic, source)])
    return effective, source


def infer_skill_declared_trust(
    *,
    trust_level: str | None,
    override: DeclaredClassTrust | None = None,
) -> DeclaredClassTrust:
    """Decide :class:`DeclaredClassTrust` for a skill source.

    The ``trust_level`` argument is the source-of-skill value already
    tracked by :pyattr:`SkillEntry.trust_level` (``"builtin"`` /
    ``"local"`` / ``"marketplace"`` / ``"remote"``). The mapping
    mirrors :pyattr:`SkillEntry.is_trusted` — only ``"remote"`` skills
    drop to DEFAULT — so the C15 rule is opt-in: pre-existing
    behaviours for locally-shipped skills are unchanged.

    Args:
        trust_level: Existing skill source label.
        override: Operator override (from setup-center). Takes
            precedence when non-None. Future commit will wire
            persistent override storage; right now callers can pass
            ``None`` to defer.

    Returns:
        DEFAULT for ``"remote"`` (or unknown values) when no override
        is supplied; TRUSTED for the three vetted sources.
    """
    if override is not None:
        return override

    if trust_level in ("builtin", "local", "marketplace"):
        return DeclaredClassTrust.TRUSTED
    return DeclaredClassTrust.DEFAULT


def infer_mcp_declared_trust(
    *,
    server_trust_level: str | None,
    override: DeclaredClassTrust | None = None,
) -> DeclaredClassTrust:
    """Decide :class:`DeclaredClassTrust` for an MCP server.

    MCP has no built-in trust signal (every server is a third-party
    process spawned via stdio / HTTP), so the default is conservative:
    DEFAULT unless the operator explicitly marked the server config as
    ``trust_level: "trusted"``.

    Args:
        server_trust_level: Value of
            :pyattr:`MCPServerConfig.trust_level`. Pre-C15 configs
            lack the field → caller passes ``None`` → DEFAULT.
        override: Per-tool operator override (future setup-center
            feature). Wins over ``server_trust_level``.

    Returns:
        TRUSTED only when ``server_trust_level == "trusted"`` (or the
        override says so); DEFAULT for everything else.
    """
    if override is not None:
        return override

    if isinstance(server_trust_level, str) and server_trust_level.strip().lower() == "trusted":
        return DeclaredClassTrust.TRUSTED
    return DeclaredClassTrust.DEFAULT


__all__ = [
    "DeclaredClassTrust",
    "compute_effective_class",
    "infer_mcp_declared_trust",
    "infer_skill_declared_trust",
]
