"""C18 Phase C — ENV variable overrides for PolicyConfigV2.

Why a dedicated module instead of inlining in loader
====================================================

Looking at the 4 reference projects:

* **claude-code**: command-line + ENV applied as a final layer after
  config-file merge; precedence is `CLI > ENV > config > defaults`.
* **hermes**: hard-codes a handful of envs (``HERMES_HOST`` etc.) in the
  config loader.
* **QwenPaw**: ``getenv`` calls scattered through ``settings.py`` (rough).
* **openclaw**: registry of `(key → field → coerce_fn)` applied after
  YAML parse, with an audit row enumerating the *names* of overridden
  fields.

We adopt openclaw's pattern because it's the easiest to audit. Each
override is declared as a registry entry that says:

1. ``ENV_NAME`` — environment variable
2. ``cfg_path`` — dotted attribute path on PolicyConfigV2
3. ``coerce`` — string → typed value (with validation)
4. ``redact`` — whether the value is sensitive (path values aren't, but
   future secrets/tokens would be)

When applied, returns ``OverrideReport`` listing the cfg_paths that were
overridden + the *coerced* values (post-redaction). global_engine writes
this report into the audit chain so future verify_chain runs catch
tampering with override history.

Why this set of 5 vars
======================

Each ENV is operator-facing: "I want to flip one knob without editing
the YAML in the container image":

1. ``OPENAKITA_POLICY_FILE`` — alternate POLICIES.yaml path (helm
   ConfigMap mount + sidecar render workflows). Resolved BEFORE loading,
   not via this module.
2. ``OPENAKITA_POLICY_HOT_RELOAD`` — force enable/disable hot-reload
   regardless of POLICIES.yaml (rolling deploy: enable hot-reload
   without rewriting the config file).
3. ``OPENAKITA_AUTO_CONFIRM`` — CI/automation: bypass non-destructive
   confirms. Phase D's ``--auto-confirm`` CLI flag sets this var.
   Critically: destructive (``mutating_global``) tools and
   ``safety_immune`` paths are NOT bypassed (enforced in classifier,
   not here).
4. ``OPENAKITA_UNATTENDED_STRATEGY`` — override
   ``unattended.default_strategy`` (e.g. CI sets ``deny_all`` to fail
   loud, prod long-runs use ``ask_owner``).
5. ``OPENAKITA_AUDIT_LOG_PATH`` — relocate ``audit.log_path`` to a
   shared volume / persistent disk without rewriting POLICIES.yaml.

We intentionally do NOT expose ``workspace.paths`` /
``safety_immune.paths`` / ``user_allowlist`` via ENV — those are
security-critical and should live only in POLICIES.yaml under VCS.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .schema import PolicyConfigV2

logger = logging.getLogger(__name__)


# Mirror the ``Literal[...]`` choices in ``UnattendedConfig.default_strategy``.
# Hard-coded here so ENV coerce validates without a runtime peek into the
# Pydantic field. If you add a new strategy to the schema, mirror it here
# (the C18 audit script catches drift via list comparison).
_VALID_UNATTENDED_STRATEGIES: frozenset[str] = frozenset(
    {"deny", "auto_approve", "defer_to_owner", "defer_to_inbox", "ask_owner"}
)


# ---------------------------------------------------------------------------
# Coerce helpers — string → typed Python value (with explicit errors)
# ---------------------------------------------------------------------------


_TRUE_STRINGS = frozenset({"1", "true", "yes", "on", "y", "enable", "enabled"})
_FALSE_STRINGS = frozenset({"0", "false", "no", "off", "n", "disable", "disabled"})


def _coerce_bool(raw: str) -> bool:
    """Parse boolean ENV with sane sloppy defaults.

    Reject ambiguous values (empty / random text) instead of silently
    falsy — operators making typos like ``OPENAKITA_HOT_RELOAD=yes-please``
    would otherwise get the default-off behavior with no warning.
    """
    norm = raw.strip().lower()
    if norm in _TRUE_STRINGS:
        return True
    if norm in _FALSE_STRINGS:
        return False
    raise ValueError(
        f"expected boolean (true/false/1/0/yes/no/on/off); got {raw!r}"
    )


def _coerce_unattended_strategy(raw: str) -> str:
    """Validate UnattendedConfig.default_strategy choice."""
    norm = raw.strip()
    if norm not in _VALID_UNATTENDED_STRATEGIES:
        allowed = ", ".join(sorted(_VALID_UNATTENDED_STRATEGIES))
        raise ValueError(f"expected one of [{allowed}]; got {raw!r}")
    return norm


def _coerce_str_path(raw: str) -> str:
    """Trim whitespace; reject empty after trim."""
    s = raw.strip()
    if not s:
        raise ValueError("path must be non-empty")
    return s


# ---------------------------------------------------------------------------
# Override registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OverrideSpec:
    env_name: str
    cfg_path: str  # dotted path on PolicyConfigV2 (e.g. "audit.log_path")
    coerce: Callable[[str], Any]
    redact: bool = False  # log "<redacted>" instead of the value
    doc: str = ""


_REGISTRY: tuple[OverrideSpec, ...] = (
    # OPENAKITA_POLICY_FILE is handled by global_engine._resolve_yaml_path
    # — listed here for documentation completeness only. Setting it via
    # this layer would be a chicken-and-egg.
    OverrideSpec(
        env_name="OPENAKITA_POLICY_HOT_RELOAD",
        cfg_path="hot_reload.enabled",
        coerce=_coerce_bool,
        doc="Force-enable/disable POLICIES.yaml hot-reload (Phase A).",
    ),
    OverrideSpec(
        env_name="OPENAKITA_AUTO_CONFIRM",
        cfg_path="confirmation.mode",
        # ConfirmationMode v2 enum: "trust" is the auto-allow mode (was
        # "yolo" pre-migration). When user sets OPENAKITA_AUTO_CONFIRM=1
        # we map to "trust"; falsy resets to "default" (= ask).
        coerce=lambda raw: "trust" if _coerce_bool(raw) else "default",
        doc=(
            "When truthy, switch confirmation.mode to 'trust' (non-destructive "
            "tools auto-allow). Destructive (mutating_global) + safety_immune "
            "still require explicit confirm — that gate is in classifier."
        ),
    ),
    OverrideSpec(
        env_name="OPENAKITA_UNATTENDED_STRATEGY",
        cfg_path="unattended.default_strategy",
        coerce=_coerce_unattended_strategy,
        doc="Override unattended.default_strategy (deny_all / ask_owner / etc.).",
    ),
    OverrideSpec(
        env_name="OPENAKITA_AUDIT_LOG_PATH",
        cfg_path="audit.log_path",
        coerce=_coerce_str_path,
        doc="Relocate audit.log_path (operator-managed shared volume etc.).",
    ),
)


@dataclass
class OverrideReport:
    """One entry per ENV var that was both set AND successfully applied."""

    applied: list[dict[str, Any]] = field(default_factory=list)
    """Each entry: ``{env, path, value, redacted}``.
    ``value`` is the coerced typed value (or "<redacted>" when redact=True).
    """

    skipped_errors: list[dict[str, str]] = field(default_factory=list)
    """Each entry: ``{env, path, error}`` for ENV vars set but with
    invalid values (coerce raised). The override is NOT applied; the
    YAML value remains. Caller logs at WARN level."""

    def has_any(self) -> bool:
        return bool(self.applied) or bool(self.skipped_errors)


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def list_override_envs() -> list[str]:
    """Names of all registered ENV vars (for docs / audit script)."""
    return [s.env_name for s in _REGISTRY]


def apply_env_overrides(
    cfg: PolicyConfigV2,
    *,
    environ: dict[str, str] | None = None,
) -> tuple[PolicyConfigV2, OverrideReport]:
    """Apply registered ENV overrides on top of ``cfg``.

    Returns a *new* PolicyConfigV2 (cfg is not mutated — pydantic models
    are immutable-by-convention here) plus a report listing what was
    applied. When no ENV var is set, returns ``cfg`` unchanged (same
    object identity, so LKG comparisons can fast-path).

    ``environ`` injection is for tests; production passes ``os.environ``.
    """
    env = environ if environ is not None else os.environ

    # Collect overrides first; only re-validate when we have at least one
    # to preserve the same-identity contract for no-op invocations.
    raw_overrides: list[tuple[OverrideSpec, str]] = []
    report = OverrideReport()
    for spec in _REGISTRY:
        if spec.env_name not in env:
            continue
        raw_value = env.get(spec.env_name, "")
        raw_overrides.append((spec, raw_value))

    if not raw_overrides:
        return cfg, report

    # Build patched dict, dotted-path replace.
    patched = cfg.model_dump(mode="python")
    for spec, raw in raw_overrides:
        try:
            value = spec.coerce(raw)
        except Exception as exc:  # noqa: BLE001 — coerce errors are user-facing
            report.skipped_errors.append(
                {
                    "env": spec.env_name,
                    "path": spec.cfg_path,
                    "error": str(exc),
                }
            )
            logger.warning(
                "[PolicyV2] ENV %s invalid value (%s); keeping YAML default",
                spec.env_name,
                exc,
            )
            continue
        _dotted_set(patched, spec.cfg_path, value)
        report.applied.append(
            {
                "env": spec.env_name,
                "path": spec.cfg_path,
                "value": "<redacted>" if spec.redact else value,
                "redacted": spec.redact,
            }
        )

    if not report.applied:
        # Every ENV var failed to coerce; keep original cfg (already
        # logged each failure above).
        return cfg, report

    try:
        new_cfg = PolicyConfigV2.model_validate(patched)
    except Exception as exc:
        # Validation of the patched config failed (e.g. an enum value
        # was rejected by a field validator). Log and fall back to the
        # original cfg — the operator gets a loud signal but isn't
        # locked out.
        logger.error(
            "[PolicyV2] ENV overrides produced an invalid config; "
            "keeping pre-override cfg. Error: %s",
            exc,
        )
        report.skipped_errors.append(
            {
                "env": "<validation>",
                "path": "<aggregate>",
                "error": f"post-override validation failed: {exc}"[:300],
            }
        )
        return cfg, report

    logger.info(
        "[PolicyV2] applied %d ENV override(s): %s",
        len(report.applied),
        ", ".join(o["env"] for o in report.applied),
    )
    return new_cfg, report


def _dotted_set(target: dict[str, Any], path: str, value: Any) -> None:
    """Set ``target[a][b][c] = value`` for path ``"a.b.c"`` in place.

    Creates intermediate dicts as needed. The last segment is overwritten
    even if its existing value is a dict (the typed coerce already
    produced the desired terminal value).
    """
    parts = path.split(".")
    cur = target
    for segment in parts[:-1]:
        nxt = cur.get(segment)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[segment] = nxt
        cur = nxt
    # Enum → string for model_validate compatibility (StrEnum has a
    # .value but pydantic accepts the enum instance too; cast here so
    # the dotted-set dict is JSON-stable for audit serialization).
    if hasattr(value, "value") and not isinstance(value, (str, int, float, bool)):
        value = value.value
    cur[parts[-1]] = value


__all__ = [
    "OverrideReport",
    "OverrideSpec",
    "apply_env_overrides",
    "list_override_envs",
]
