"""Sentinel #9 -- v1 orgs source surface retired (P-RC-9 P9.9eta-1).

Ninth P-RC-9 sentinel; joins the 6 parity slots
(P9.1c..P9.6gamma) + 7th REST contract sentinel
(``test_rest_contract_sentinel.py``; P9.7gamma-2) + 8th
frontend stale-path sentinel
(``test_frontend_stale_paths_sentinel.py``; P9.8delta-1) as
another **active** (non-xfail) collection-time invariant. Locks
in the two invariants P-RC-9 P9.9epsilon established by
physically retiring the v1 src surface:

* P9.9epsilon-2a (``857a5a35``) -- ``git rm`` on
  ``src/openakita/api/routes/orgs.py`` (-2 533 LOC; 89 v1
  endpoints) + 2 dev scripts + OpenAPI snapshot regen.
* P9.9epsilon-2b (``90a7d77f``) -- atomic
  ``git rm -r src/openakita/orgs/`` (-20 237 LOC; 26 files);
  largest single deletion of P-RC-9.

Two invariants:

1. **v1 src directory retired** -- ``src/openakita/orgs/``
   MUST NOT exist (or, defensively, exist empty). Recreating
   the directory -- even as an empty package or an
   ``__init__.py`` re-export shim -- re-opens the door this
   phase closed.

2. **production sources are v1-import-free** -- the strict
   regex ``^\\s*(?:from|import)\\s+openakita\\.orgs(?:\\.|$|\\s)``
   (multi-line; ``re.MULTILINE``) MUST find **zero hits** when
   walking ``*.py`` / ``*.pyi`` under ``src/openakita/``,
   ``apps/``, ``scripts/``, ``identity/``, and ``tests/``. The
   regex anchors at line start (with optional leading
   whitespace) so docstring narrative referring to
   ``openakita.orgs`` does **not** match -- that exemption was
   audited in epsilon-AUDIT sec 2.1 (auditable non-imports
   under ``runtime/orgs/`` and a handful of v2 schema/route
   docstrings).

Allowed exemptions (audited; do **NOT** add new entries
without a charter row):

* ``src/openakita/runtime/orgs/`` and ``tests/runtime/orgs/``
  -- v2 paths. The strict regex would only match a top-level
  import line and v2 modules do not import v1 by construction
  (P9.9alpha-1 inventory + gamma-1/gamma-2/gamma-3 sweep), but
  the exclusion is belt-and-suspenders against future
  docstring examples that *happen* to begin with a
  ``from openakita.orgs.X`` line.
* ``apps/setup-center/src-tauri/`` -- Tauri Rust build
  outputs. The release packaging step lands a bundled
  openakita-server snapshot under ``resources/`` (source
  resources) and ``target/<profile>/resources/`` (Rust
  build output); both directories use the PyInstaller
  ``_internal`` layout and are gitignored
  (``git ls-files apps/setup-center/src-tauri/`` returns 0
  Python files). Excluding the parent ``src-tauri/`` path
  keeps the sentinel from tripping on stale local artefacts
  left by prior packaging or ``cargo build`` runs.

Charter cross-refs:

* ``docs/revamp/P-RC-9-P9.9-CHARTER.md`` sec 5.7 (eta-1
  outlook + 9th sentinel adoption) and sec 7.2 (sentinel
  rationale; ADR-0011 Protocol-ceiling unaffected; recommend
  ADOPT (Y)).
* ``docs/revamp/P-RC-9-P9.9-eps-CHARTER.md`` sec 0 + sec 8
  (eta-1 hand-off sequence).
* ``docs/revamp/P-RC-9-P9.9-eps-AUDIT.md`` sec 2.1 (audited
  docstring back-references that the strict regex correctly
  exempts).

The sentinel does **not** activate via ``@pytest.mark.xfail``
-- in the P9.x convention "sentinel" means **active
assertion**; xfail markers are removed when the invariant is
met (which is now, post-epsilon-2b).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# tests/parity/orgs/test_*.py -> parents[3] == repo root.
_REPO = Path(__file__).resolve().parents[3]

# v1 directory under guard against regrowth.
_V1_DIR = _REPO / "src" / "openakita" / "orgs"

# Roots to walk for the import-time scan. Mirrors the brief's
# spec verbatim: src/openakita + apps + scripts + identity + tests.
_SCAN_ROOTS: tuple[str, ...] = (
    "src/openakita",
    "apps",
    "scripts",
    "identity",
    "tests",
)

# Path fragments that, if present anywhere in a file's relative
# path (posix slashes), exempt the file from the import scan.
# Substring match is intentional: catches both repo-root-relative
# paths and any nested occurrence (e.g. a vendored ``runtime/orgs``
# would also be exempt by construction).
_EXEMPT_FRAGMENTS: tuple[str, ...] = (
    "runtime/orgs",  # v2 subsystem (brief)
    "apps/setup-center/src-tauri/",  # Tauri Rust build outputs (gitignored)
)

# File extensions to scan: source Python files only. Skips docs
# (md/rst), data (json/yaml), and frontend assets.
_FILE_GLOBS: tuple[str, ...] = ("*.py", "*.pyi")

# Strict v1-import regex.
#
# * ``re.MULTILINE`` so ``^`` matches every line start, not just
#   buffer start.
# * ``\s*`` allows leading indentation (catches imports inside
#   ``def``/``class`` bodies, ``if TYPE_CHECKING:`` blocks, etc.).
# * ``(?:from|import)\s+`` requires the literal ``from``/``import``
#   keyword followed by whitespace -- this is the exemption
#   mechanism for docstring back-references which describe the
#   module name without the import keyword.
# * ``openakita\.orgs`` is the literal v1 module path.
# * ``(?:\.|$|\s)`` requires the next character to be ``.``,
#   end-of-line, or whitespace -- this discriminates the v1
#   module from sibling names like ``openakita.orgs_v2`` (no
#   such module today, but the regex is future-proof).
# Fast pre-filter for the per-file scan: skip the UTF-8 decode +
# per-line regex unless the file contains this raw bytes substring.
# Cheap O(n) bytes search vs O(n log n) UTF-8 decode + regex.
_V1_BYTES_NEEDLE = b"openakita.orgs"


_V1_IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+openakita\.orgs(?:\.|$|\s)",
    re.MULTILINE,
)


def _is_exempt(rel_posix: str) -> bool:
    """Return ``True`` if ``rel_posix`` matches any audited exemption."""
    return any(frag in rel_posix for frag in _EXEMPT_FRAGMENTS)


def _iter_python_files(root: Path) -> list[Path]:
    """Yield ``*.py``/``*.pyi`` under ``root`` with directory-level pruning.

    Uses ``os.walk`` and mutates ``dirnames`` in place so exempt
    sub-trees (e.g. the Tauri Rust build output under
    ``apps/setup-center/src-tauri/``) are skipped before descent.
    A pure ``Path.rglob`` walk crosses ~22 000 stale ``.py`` files
    in those build artefacts; pruning keeps the sentinel under the
    ~1 s wall-clock budget mandated by the charter.
    """
    suffixes = {ext.lstrip("*").lower() for ext in _FILE_GLOBS}
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Build the posix-style relative dir path once for prune check.
        # Sort dirnames in place for deterministic ordering across runs.
        dirnames.sort()
        # Mutate dirnames in place to prune exempt subdirs from the walk.
        kept: list[str] = []
        for d in dirnames:
            sub = (Path(dirpath) / d).relative_to(_REPO).as_posix()
            if not _is_exempt(sub):
                kept.append(d)
        dirnames[:] = kept

        for fn in sorted(filenames):
            ext = Path(fn).suffix.lower()
            if ext not in suffixes:
                continue
            out.append(Path(dirpath) / fn)
    return out


def _scan_v1_imports() -> list[tuple[str, int, str]]:
    """Walk ``_SCAN_ROOTS`` and return ``(rel_posix, line_no, line)``.

    Files are read with ``encoding="utf-8"``; non-text or
    non-UTF-8 files are skipped silently (the v1-import scan
    only cares about Python source). Per-line walk pins the
    line number for the failure message; the regex anchors at
    line start so a per-line ``match`` is exact.
    """
    hits: list[tuple[str, int, str]] = []
    for root_rel in _SCAN_ROOTS:
        root = _REPO / root_rel
        if not root.is_dir():
            continue
        for file in _iter_python_files(root):
            rel = file.relative_to(_REPO).as_posix()
            # Belt-and-suspenders: file-level check stays even after
            # directory-level pruning (a future contributor might add
            # a fragment that names a file rather than a directory).
            if _is_exempt(rel):
                continue
            # Fast path: read bytes once and skip the decode + regex
            # for files that do not even contain the literal substring
            # ``openakita.orgs``. Eliminates ~99% of the work on a clean
            # tree (1 174 prod .py files, only ~30 contain the substring
            # at any P-RC-9 milestone).
            try:
                blob = file.read_bytes()
            except OSError:
                continue
            if _V1_BYTES_NEEDLE not in blob:
                continue
            try:
                content = blob.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if not _V1_IMPORT_RE.search(content):
                continue
            for n, line in enumerate(content.splitlines(), 1):
                if _V1_IMPORT_RE.match(line):
                    hits.append((rel, n, line.rstrip()))
    return hits


# ---------------------------------------------------------------------------
# Test 1 -- v1 src directory retired.
# ---------------------------------------------------------------------------


def test_v1_src_directory_retired() -> None:
    """``src/openakita/orgs/`` MUST NOT exist (or be empty if it does).

    P-RC-9 P9.9epsilon-2b atomically deleted the v1 subsystem
    (-20 237 LOC; 26 files; commit ``90a7d77f``). Recreating
    the directory -- even as an empty package or as an
    ``__init__.py`` re-export shim onto ``openakita.runtime.orgs``
    -- re-opens the door this phase closed.

    The 308 shim under
    ``src/openakita/api/routes/_orgs_v2_legacy_redirects.py``
    is the only v1-tagged surface that legitimately survives
    P-RC-9 (per ADR-0015 option (b) LOCKED), and it lives under
    ``api/routes/`` (NOT under ``orgs/``); see ADR-0015 for the
    v2.1.0 retirement task list.
    """
    if not _V1_DIR.exists():
        return

    children = sorted(p.relative_to(_REPO).as_posix() for p in _V1_DIR.iterdir())
    assert not children, (
        "v1 orgs source directory ``src/openakita/orgs/`` has been "
        "recreated -- this is a regression of P-RC-9 P9.9epsilon-2b "
        "(commit 90a7d77f, atomic ``git rm -r src/openakita/orgs/``).\n"
        "Found children:\n  "
        + "\n  ".join(children)
        + "\n\nFix: delete the directory; if a v2 module mistakenly "
        "imports ``openakita.orgs.X`` the correct fix is to rewrite "
        "to ``openakita.runtime.orgs.X`` per the P9.9alpha-1 import "
        "inventory + gamma-1/gamma-2/gamma-3 sweep matrices. The 308 "
        "shim under ``api/routes/_orgs_v2_legacy_redirects.py`` is "
        "the only v1-tagged surface that legitimately survives, and "
        "it lives under ``api/routes/`` -- not under ``orgs/``."
    )


# ---------------------------------------------------------------------------
# Test 2 -- production sources are v1-import-free.
# ---------------------------------------------------------------------------


def test_production_imports_v1_free() -> None:
    """Zero ``from`` / ``import openakita.orgs`` lines under production trees.

    Walks ``*.py`` + ``*.pyi`` under ``src/openakita/``,
    ``apps/``, ``scripts/``, ``identity/``, and ``tests/``
    (excluding the audited exemption set ``_EXEMPT_FRAGMENTS``)
    and matches a strict line-start regex. A failure surface is
    per-file:line:line-text so a regression can be located
    instantly.

    Audited exemptions (not regressions; do NOT extend without
    a P-RC-9 charter row):

    * ``runtime/orgs`` -- v2 subsystem; v2 modules do not
      import v1 by construction.
    * ``apps/setup-center/src-tauri/resources`` -- Tauri
      bundled openakita-server snapshot (gitignored
      build artefact).
    """
    hits = _scan_v1_imports()
    assert not hits, (
        "Stale ``openakita.orgs`` import statement(s) found in the "
        "production source trees -- this is a regression of the "
        "P-RC-9 P9.9 import sweep (beta-1 channels, gamma-1 api, "
        "gamma-2 core, delta-2/delta-3 tests, epsilon-2a router + "
        "dev scripts):\n"
        + "\n".join(f"  {rel}:{ln}: {line}" for rel, ln, line in hits)
        + "\n\nFix: rewrite to ``openakita.runtime.orgs.X`` per "
        "the 1-to-1 + absorbed-not-1-to-1 mapping in "
        "docs/revamp/P-RC-9-P9.9-IMPORT-INVENTORY.md.\n"
        "Audited exemptions (NOT regressions; do NOT add new "
        "entries without a charter row): " + ", ".join(_EXEMPT_FRAGMENTS)
    )
