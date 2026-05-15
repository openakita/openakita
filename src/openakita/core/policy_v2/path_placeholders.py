"""Path template placeholder resolution (single source of truth).

Before this module existed, three different call-sites each had their own
"expand ``${CWD}`` / ``~``" logic with subtly different behaviour:

- ``schema.PolicyConfigV2.expand_placeholders`` used **strict equality**
  ``if p == "${CWD}"``, which meant ``"${CWD}/secrets/**"`` was **silently
  not expanded** and entered the engine as the literal string. Prefix-
  matching in ``_check_safety_immune`` against real absolute paths would
  then never hit, so user-defined ``safety_immune.paths`` containing
  ``${CWD}/...`` were a **silent no-op**. This was a real security hole.
- ``safety_immune_defaults.expand_builtin_immune_paths`` correctly used
  ``replace("${CWD}", ...)`` and so worked for the built-in 9 categories.

Centralising here:

- One canonical resolver shared by ``schema.expand_placeholders`` and
  ``safety_immune_defaults.expand_builtin_immune_paths``.
- Future placeholders (``${HOME}`` / ``${WORKSPACE}``) plug in here.
- Windows path normalisation (backslash → forward slash) lives in one
  place.

Supported placeholders
======================

- ``${CWD}`` — current working directory; resolved via the explicit
  ``cwd`` argument (engine init time) or ``Path.cwd()`` as last resort.
- ``${HOME}`` — user's home directory; alias for ``~`` so users can pick
  whichever syntax they prefer.
- ``${WORKSPACE}`` — reserved for setup-center's workspace concept
  (distinct from process CWD). When the ``workspace`` argument is
  ``None`` we fall back to ``cwd`` so existing configs keep working.
- ``~`` / ``~/`` prefix — handled via ``Path.expanduser``.

Order matters: ``~`` is detected first (must be a prefix); then
``${HOME}`` / ``${CWD}`` / ``${WORKSPACE}`` are substituted anywhere in
the string via ``.replace()``. This handles both ``"${CWD}"`` (exact
match) and ``"${CWD}/sub/path"`` correctly — that's the bug fix.

Outputs use forward slashes regardless of platform so downstream prefix-
match logic (e.g. ``_path_under``) doesn't need to special-case ``\\``
vs ``/``.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

# Supported placeholder tokens (for documentation / validation).
# Order does not matter for substitution but matches the docstring.
SUPPORTED_PLACEHOLDERS: tuple[str, ...] = ("${CWD}", "${HOME}", "${WORKSPACE}")


def _norm(p: str) -> str:
    """Normalise Windows backslashes to forward slashes."""
    return p.replace("\\", "/")


def resolve_path_template(
    raw: str,
    *,
    cwd: Path,
    home: Path | None = None,
    workspace: Path | None = None,
) -> str:
    """Expand placeholders in a single path template.

    Args:
        raw: Path string possibly containing ``${CWD}`` / ``${HOME}`` /
            ``${WORKSPACE}`` / leading ``~``.
        cwd: Workspace root used for ``${CWD}`` expansion. Required so
            tests can pin a deterministic root; production callers pass
            ``Path.cwd()`` (or the engine's recorded init-time cwd).
        home: User home directory for ``${HOME}``; defaults to
            ``Path.home()``.
        workspace: Setup-center workspace root for ``${WORKSPACE}``;
            falls back to ``cwd`` when ``None`` so older configs keep
            working until the setup-center workspace concept lands.

    Returns:
        The fully resolved path string with forward slashes. Literal
        paths (``/etc/passwd`` etc.) are returned unchanged (still
        normalised to forward slashes for consistency).
    """
    cwd_str = _norm(str(cwd))
    home_path = home if home is not None else Path.home()
    home_str = _norm(str(home_path))
    workspace_str = _norm(str(workspace)) if workspace is not None else cwd_str

    out = raw
    # ``~`` / ``~/`` must be a prefix — expanduser handles both.
    if out.startswith("~"):
        out = _norm(str(Path(out).expanduser()))
    # ``${...}`` placeholders can appear anywhere in the string.
    if "${CWD}" in out:
        out = out.replace("${CWD}", cwd_str)
    if "${HOME}" in out:
        out = out.replace("${HOME}", home_str)
    if "${WORKSPACE}" in out:
        out = out.replace("${WORKSPACE}", workspace_str)
    return _norm(out)


def resolve_path_list(
    paths: Iterable[str],
    *,
    cwd: Path,
    home: Path | None = None,
    workspace: Path | None = None,
) -> list[str]:
    """Apply :func:`resolve_path_template` to every entry in ``paths``."""
    return [
        resolve_path_template(p, cwd=cwd, home=home, workspace=workspace)
        for p in paths
    ]


__all__ = [
    "SUPPORTED_PLACEHOLDERS",
    "resolve_path_list",
    "resolve_path_template",
]
