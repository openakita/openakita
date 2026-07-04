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

Two invariants (P-RC-10 P10.4 reverses Test 2 polarity; P10.6
tightens Test 2 further -- see Test 2 docstring for the
post-shim-removal reality):

1. **v1 src directory retired (Option-Z augmented at P10.2)** --
   ``src/openakita/orgs/`` is either gone (original P-RC-9
   state) or hosts the post-P10.1 v2 flatten (structural
   markers required; v1 regrowth still blocked).

2. **production sources are LEGACY-import-free (post-P10.6)**
   -- strict regex ``^\\s*(?:from|import)\\s+openakita\\.runtime\\.orgs(?:\\.|$|\\s)``
   MUST find zero hits under ``src/openakita/``, with NO
   whitelist. P10.6 (``git rm``) removed the lone P10.2
   deprecation shim file at
   ``src/openakita/runtime/orgs/__init__.py`` and the
   companion directory; the whitelist tuple has been dropped
   (was length 1). Test 2 additionally asserts that the
   ``src/openakita/runtime/orgs/`` directory itself no longer
   exists -- a hard post-removal invariant.

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
* ``docs/revamp/P-RC-10-CHARTER.md`` sec 3 P10.4 + RECON sec 4
  (polarity flip after P10.1 flatten) + sec 2 P10.6 (shim
  removal trigger conditions and post-removal directory
  invariant).

The sentinel does **not** activate via ``@pytest.mark.xfail``
-- in the P9.x convention "sentinel" means **active
assertion**; xfail markers are removed when the invariant is
met (which is now, post-epsilon-2b, post-P10.4 and
post-P10.6).
"""

from __future__ import annotations

import re
from pathlib import Path

# tests/parity/orgs/test_*.py -> parents[3] == repo root.
_REPO = Path(__file__).resolve().parents[3]

# v1 directory under guard against regrowth (Test 1).
_V1_DIR = _REPO / "src" / "openakita" / "orgs"

# Production source root scanned by Test 2 (post-P10.4 polarity).
_SRC_ROOT = _REPO / "src" / "openakita"

# P-RC-10 P10.6 invariant: the deprecation shim location is GONE.
# Test 2 asserts non-existence of this directory before scanning for
# residual imports. The companion ``__init__.py`` file was removed
# via ``git rm`` and the directory itself was pruned (any leftover
# ``__pycache__`` from a pre-removal interpreter run was wiped).
_SHIM_DIR = _REPO / "src" / "openakita" / "runtime" / "orgs"

# Strict legacy-import regex (post-P10.4 polarity; whitelist dropped
# at P10.6). Same shape as the historical v1 regex: ``re.MULTILINE``
# + leading ``\s*`` for indented (deferred) imports + literal
# ``from``/``import`` keyword (so docstring back-references do not
# match) + terminator alt that discriminates the legacy module from
# any future sibling.
_LEGACY_IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+openakita\.runtime\.orgs(?:\.|$|\s)",
    re.MULTILINE,
)

# Fast pre-filter for the per-file scan (cheap O(n) bytes search).
_LEGACY_BYTES_NEEDLE = b"openakita.runtime.orgs"


def _scan_legacy_imports() -> list[tuple[str, int, str]]:
    """Walk ``src/openakita/`` for legacy ``openakita.runtime.orgs`` imports.

    Returns ``(rel_posix, line_no, line_text)`` for every matching line.
    Post-P10.6 there is **no whitelist** -- the lone shim file that
    was previously exempt (``src/openakita/runtime/orgs/__init__.py``)
    has been physically removed and any production import of the
    deprecated dotted path is therefore a hard failure.
    """
    hits: list[tuple[str, int, str]] = []
    files: list[Path] = sorted(
        list(_SRC_ROOT.rglob("*.py")) + list(_SRC_ROOT.rglob("*.pyi"))
    )
    for file in files:
        rel = file.relative_to(_REPO).as_posix()
        try:
            blob = file.read_bytes()
        except OSError:
            continue
        if _LEGACY_BYTES_NEEDLE not in blob:
            continue
        try:
            content = blob.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if not _LEGACY_IMPORT_RE.search(content):
            continue
        for n, line in enumerate(content.splitlines(), 1):
            if _LEGACY_IMPORT_RE.match(line):
                hits.append((rel, n, line.rstrip()))
    return hits


# ---------------------------------------------------------------------------
# Test 1 -- v1 src directory retired.
# ---------------------------------------------------------------------------


# P-RC-10 P10.2 Option-Z augment: structural markers that distinguish
# the post-flatten v2 occupant from a v1 regrowth attempt. These private
# subsystem files were introduced under runtime/orgs/ during P-RC-9 and
# moved 1:1 into orgs/ at P10.1; the v1 layout (retired by epsilon-2b)
# never contained them.
_V2_FLATTEN_MARKERS: tuple[str, ...] = (
    "_runtime_templates.py",
    "_runtime_dispatch.py",
    "_runtime_event_bus.py",
    "_runtime_lifecycle.py",
    "_runtime_node_lifecycle.py",
)


def test_v1_src_directory_retired() -> None:
    """``src/openakita/orgs/`` is either gone or the post-P10.1 v2 surface.

    P-RC-9 P9.9epsilon-2b (``90a7d77f``) atomically deleted the v1 subsystem
    (-20 237 LOC; 26 files). P-RC-10 P10.1 re-occupies the same on-disk path
    with the *flattened* v2 surface (atomic git mv from
    ``src/openakita/runtime/orgs/``; 25 files; no semantic v1 regrowth).

    Discriminator: post-flatten the directory MUST contain the v2-private
    subsystem markers in ``_V2_FLATTEN_MARKERS`` -- those files were
    introduced under ``runtime/orgs/`` during P-RC-9 (P9.4 / P9.5 / P9.6)
    and the v1 layout never had them. A v1 regrowth attempt (recreating
    the dir as an empty package, an ``__init__.py``-only stub, or a paste
    of the deleted v1 file set) trips this assertion.

    Test 2 (``test_production_imports_v1_free``) holds the strict
    "no abs ``from openakita.runtime.orgs.X`` import" invariant
    (post-P10.4 polarity; tightened at P10.6 with shim removal +
    directory non-existence guard). The 308 shim under
    ``src/openakita/api/routes/_orgs_v2_legacy_redirects.py``
    remains the only v1-tagged surface (ADR-0015 option (b); v2.1.0).
    """
    if not _V1_DIR.exists():
        return  # original P-RC-9 P9.9epsilon-2b state -- still acceptable

    missing = [m for m in _V2_FLATTEN_MARKERS if not (_V1_DIR / m).is_file()]
    assert not missing, (
        "``src/openakita/orgs/`` exists but lacks the P-RC-10 P10.1 v2 "
        "flatten markers -- this looks like a v1 regrowth attempt rather "
        "than the legitimate post-P10.1 v2 surface.\n"
        f"Missing markers: {missing}\n\n"
        "Fix: if you intended to flatten ``runtime/orgs/`` -> ``orgs/`` per "
        "P-RC-10 P10.1, rerun the atomic ``git mv`` so all 25 v2 files "
        "(13 public + 12 private incl. ``__init__.py``) land together. "
        "If you intended to revert to a v1-style layout, that is forbidden "
        "by P-RC-9 P9.9epsilon-2b (commit ``90a7d77f``); the 308 shim "
        "under ``api/routes/_orgs_v2_legacy_redirects.py`` is the only "
        "v1-tagged surface that legitimately survives (ADR-0015)."
    )


# ---------------------------------------------------------------------------
# Test 2 -- production sources are deprecated-import-free
# (P-RC-10 P10.4: polarity reversed; P10.6: shim removal + directory
# non-existence guard + whitelist tuple dropped; test name kept
# byte-stable so CI / sentinel tracking dashboards follow the same
# checkpoint identifier across both flips).
# ---------------------------------------------------------------------------


def test_production_imports_v1_free() -> None:
    """Zero ``openakita.runtime.orgs.*`` imports under ``src/openakita/``.

    **Polarity reversed in P-RC-10 P10.4 + tightened in P10.6**
    (post-flatten + post-shim-removal reality):

    * P-RC-9 era this test banned ``openakita.orgs.*`` (then the
      retired v1 surface) while ``openakita.runtime.orgs.*`` was
      the canonical v2 path.
    * P10.1 (``37536a62``) atomically flattened
      ``runtime/orgs/`` -> ``orgs/``.
    * P10.2 (``d8275080``) turned the old location into a
      one-release deprecation shim
      (``src/openakita/runtime/orgs/__init__.py``).
    * P10.3a-f (``5ac2c786`` .. ``e1680941``) swept all
      production + test + script + docstring sites to the new
      canonical path.
    * P10.4 (``eb96fc15``) reversed sentinel #9 Test 2 to ban
      the deprecated path, whitelisting only the shim file.
    * **P10.6 (this commit) ``git rm``ed the shim file and
      pruned the directory.** The shim allowlist tuple
      (previously of length 1) has been dropped entirely and
      replaced with the stronger post-removal invariant that
      the directory ``src/openakita/runtime/orgs/`` itself
      MUST NOT exist.

    Net effect: production code under ``src/openakita/`` MUST
    NOT regrow imports of the deprecated
    ``openakita.runtime.orgs.*`` path under ANY circumstance.
    Legacy callers now receive a loud ``ModuleNotFoundError``
    at import time, which is the intended P10.6 behavior.

    Test name kept byte-stable across the polarity flip + the
    shim removal so CI / sentinel tracking dashboards follow
    the same checkpoint identifier.
    """
    # ---- Post-P10.6 directory invariant -----------------------------
    # The deprecation shim file was removed via ``git rm`` and the
    # parent directory pruned. Any reappearance (including a stray
    # ``__pycache__`` left behind by a stale interpreter) is a
    # regression: either someone re-added the shim or an external
    # caller is still importing the legacy dotted path and Python is
    # materializing a phantom package directory.
    assert not _SHIM_DIR.exists(), (
        "``src/openakita/runtime/orgs/`` still exists post-P-RC-10 P10.6 -- "
        "the deprecation shim should have been removed via "
        "``git rm src/openakita/runtime/orgs/__init__.py`` AND the "
        "directory pruned (no stale ``__pycache__``). Listing: "
        + str(sorted(p.name for p in _SHIM_DIR.iterdir()))
        + "\n\nFix: ``Remove-Item -Path src/openakita/runtime/orgs/ "
        "-Recurse -Force`` (Windows) or ``rm -rf "
        "src/openakita/runtime/orgs/`` (POSIX); if the dir was "
        "regenerated by a phantom import, audit ``src/`` for any "
        "``openakita.runtime.orgs`` reference Test 2's import scan "
        "may have missed (e.g. dynamic ``importlib.import_module`` "
        "or ``__import__`` calls)."
    )

    # ---- Strict legacy-import scan (no whitelist post-P10.6) --------
    hits = _scan_legacy_imports()
    assert not hits, (
        "Stale ``openakita.runtime.orgs`` import statement(s) found in "
        "production source under ``src/openakita/`` -- this is the "
        "post-P10.1 flatten polarity guard tightened at P10.6 (the "
        "P10.2 deprecation shim has been removed; there is NO "
        "whitelist). The legacy path no longer resolves at runtime "
        "and would raise ``ModuleNotFoundError`` if imported; new "
        "code MUST import from ``openakita.orgs.*`` directly:\n"
        + "\n".join(f"  {rel}:{ln}: {line}" for rel, ln, line in hits)
        + "\n\nFix: rewrite ``openakita.runtime.orgs.X`` -> "
        "``openakita.orgs.X`` (1:1 prefix swap; see "
        "docs/revamp/P-RC-10-RECON.md section 1 for the 25-file "
        "mapping)."
    )
