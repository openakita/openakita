"""Frontend stale v1 ``/api/orgs/`` HTTP-path sentinel (P-RC-9 P9.8delta-1).

Eighth P-RC-9 sentinel; joins the 6 parity slots (P9.1c-P9.6gamma) +
the 7th REST contract sentinel (P9.7gamma-2,
``test_rest_contract_sentinel.py``) as another **active**
(non-xfail) collection-time invariant. Asserts that the P9.8
caller migration has fully rewired the frontend off the v1
``/api/orgs/...`` surface and onto the v2 mint + Group A
``/api/v2/orgs-spec/...`` relocation surfaces.

Three invariants:

1. **No stale v1 HTTP literals (Group C closed at P10.5e)** --
   no ``/api/orgs/...`` HTTP path literal appears under
   ``apps/setup-center/src/`` (``*.ts`` + ``*.tsx``). The 3 Group C
   debug-only endpoints (``reset`` / ``heartbeat/trigger`` /
   ``standup/trigger``) that previously rode the allowlist were
   deleted at P-RC-10 P10.5e together with the closing of nit
   GroupC; the allowlist is now empty but kept as a drift guard
   slot. Collection-time grep; ~30 ms on the current tree.
2. **Group C allowlist still present** -- the 3 allowlisted
   paths must still exist in ``OrgEditorView.tsx`` at (or near)
   their recorded line numbers. When P9.9 deletes the v1
   surface, this test fails loud and reminds the maintainer to
   strip ``GROUP_C_ALLOWLIST`` too.
3. **TS module-import discriminator self-test** -- the 4 known
   relative-path TS imports (``from "../api/orgs"`` /
   ``from "../../api/orgs"`` / ``vi.mock("../../api/orgs", ...)``)
   are still present AND do NOT match the sentinel's regex. The
   regex uses negative lookbehind ``(?<!\\.)/api/orgs`` to
   distinguish HTTP literals (preceded by ``${apiBaseUrl}``,
   ``"``, ``'``, ``` ` ```, ``}``, ``(``, etc.) from TS module
   specifiers (preceded by ``.`` as part of ``../`` or ``../../``).
   If a regex false-positive ever lands on an import, fix the
   regex -- do **not** add it to ``GROUP_C_ALLOWLIST`` (the
   allowlist is reserved for genuine v1 HTTP literals).

Charter cross-refs: ``docs/revamp/P-RC-9-P9.8-CHARTER.md`` sec 7
(8th sentinel decision: ADOPT in P9.8delta-1); sec 9 gate
criterion 1 (zero v1 literal count). Inventory cross-ref:
``docs/revamp/P-RC-9-P9.8-CALLER-INVENTORY.md`` sec 1.2
(Group C source) + sec 4.3 (TS-module-import no-op list).
"""

from __future__ import annotations

import re
from pathlib import Path

# tests/parity/orgs/test_*.py -> parents[3] == repo root.
_REPO = Path(__file__).resolve().parents[3]
_FRONTEND_SRC = _REPO / "apps" / "setup-center" / "src"

# Match a v1 ``/api/orgs[...]`` HTTP literal but exclude TS module
# specifiers like ``../api/orgs`` (preceded by ``.``). Negative
# lookbehind on ``.`` is the cheapest discriminator: HTTP literals
# are always preceded by ``{``, ``}``, ``"``, ``'``, ``` ` ```, ``(``,
# whitespace, or BOL; TS imports always by ``../`` (i.e. by ``.``).
_V1_HTTP_RE = re.compile(r"(?<!\.)/api/orgs")

# Group C HTTP path allowlist -- CLOSED at P-RC-10 P10.5e. The three
# v1 debug-only literals (reset / heartbeat trigger / standup trigger)
# that previously rode the allowlist have been deleted from
# ``OrgEditorView.tsx`` now that their owning v1 router was retired at
# P-RC-9 P9.9eta-2 (v1 src deletion). An empty list keeps the drift
# test (``test_group_c_allowlist_paths_still_present``) wired as a
# no-op guard against future re-addition of v1 HTTP literals.
GROUP_C_ALLOWLIST: list[tuple[str, int, str]] = []

# Four TS module-import paths that look superficially like
# ``/api/orgs`` but are relative-path module specifiers. Pinned
# here so a future drift (e.g. someone turning ``from "../api/orgs"``
# into a literal URL string) trips the discriminator self-test.
TS_MODULE_IMPORTS: list[tuple[str, int, str]] = [
    ("apps/setup-center/src/components/TemplatePickerDialog.tsx", 48, '"../api/orgs"'),
    (
        "apps/setup-center/src/components/__tests__/TemplatePickerDialog.test.tsx",
        7,
        '"../../api/orgs"',
    ),
    (
        "apps/setup-center/src/components/__tests__/TemplatePickerDialog.test.tsx",
        43,
        '"../../api/orgs"',
    ),
    ("apps/setup-center/src/views/OrgEditorView.tsx", 66, '"../api/orgs"'),
]


def _scan_v1_http_hits() -> list[tuple[Path, int, str]]:
    """Return (file, line_number, stripped_line) for every regex hit."""
    hits: list[tuple[Path, int, str]] = []
    for ext in ("*.ts", "*.tsx"):
        for file in sorted(_FRONTEND_SRC.rglob(ext)):
            try:
                text = file.read_text(encoding="utf-8")
            except OSError:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if _V1_HTTP_RE.search(line):
                    hits.append((file, n, line.strip()))
    return hits


# Test 1 -- no stale v1 HTTP paths outside the Group C allowlist.


def test_no_stale_v1_http_paths_outside_allowlist() -> None:
    """Zero ``/api/orgs/`` HTTP literals remain except the 3 Group C debug paths.

    Allowed: any path under ``/api/v2/orgs/...`` (P9.7 mint) or
    ``/api/v2/orgs-spec/...`` (P9.7a-2a Group A relocation), plus
    the 3 Group C paths in ``GROUP_C_ALLOWLIST``.
    """
    allowed_keys = {(rel, ln) for rel, ln, _ in GROUP_C_ALLOWLIST}
    stale: list[tuple[str, int, str]] = []
    for file, ln, snippet in _scan_v1_http_hits():
        rel = file.relative_to(_REPO).as_posix()
        if (rel, ln) not in allowed_keys:
            stale.append((rel, ln, snippet))
    assert not stale, (
        "Stale v1 ``/api/orgs/...`` HTTP path literal(s) found in "
        "apps/setup-center/src/ that are not on the Group C allowlist:\n"
        + "\n".join(f"  {rel}:{ln}  {snippet}" for rel, ln, snippet in stale)
        + "\n\nFix: swap to ``/api/v2/orgs/...`` (mint) per "
        "docs/revamp/P-RC-9-P9.8-CALLER-INVENTORY.md sec 1; or, if a "
        "new debug-only v1 path scheduled for P9.9 deletion, add it "
        "to GROUP_C_ALLOWLIST with rationale + P9.9 reference."
    )


# Test 2 -- Group C allowlist not stale (P9.9 deletion alarm).


def test_group_c_allowlist_paths_still_present() -> None:
    """The 3 allowlisted v1 paths must still exist in OrgEditorView.tsx.

    When P9.9 deletes the v1 router and these debug paths, this
    test fails -- the alarm reminding the maintainer to also
    strip ``GROUP_C_ALLOWLIST`` (otherwise the sentinel silently
    permits paths that no longer exist anywhere in the tree).
    """
    missing: list[tuple[str, int, str]] = []
    for rel, ln, suffix in GROUP_C_ALLOWLIST:
        path = _REPO / rel
        if not path.is_file():
            missing.append((rel, ln, f"file missing: {path}"))
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        start = max(0, ln - 5)
        end = min(len(lines), ln + 5)
        if suffix not in "\n".join(lines[start:end]):
            missing.append((rel, ln, f"suffix not in window L{start + 1}..L{end}: {suffix}"))
    assert not missing, (
        "Group C allowlist drift detected -- a deprecated v1 HTTP path is "
        "no longer at its recorded location in OrgEditorView.tsx. If P9.9 "
        "has deleted these endpoints, also remove the corresponding entry "
        "from GROUP_C_ALLOWLIST. Otherwise re-pin the line number:\n"
        + "\n".join(f"  {rel}:{ln}  {reason}" for rel, ln, reason in missing)
    )


# Test 3 -- TS module-import discriminator self-test.


def test_module_imports_use_relative_path() -> None:
    """The 4 TS module imports stay in relative-path form and are not regex hits.

    Double-purpose: (1) confirms the 4 known imports are still in
    ``../api/orgs`` / ``../../api/orgs`` shape; (2) confirms the
    sentinel regex correctly excludes them (negative lookbehind
    on ``.``). If any import line matches ``_V1_HTTP_RE``, the
    discriminator has drifted -- fix the regex, NOT the allowlist.
    """
    drift: list[tuple[str, int, str]] = []
    for rel, ln, expected in TS_MODULE_IMPORTS:
        path = _REPO / rel
        if not path.is_file():
            drift.append((rel, ln, f"file missing: {path}"))
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        start = max(0, ln - 3)
        end = min(len(lines), ln + 3)
        window_lines = lines[start:end]
        if expected not in "\n".join(window_lines):
            drift.append((rel, ln, f"expected {expected!r} not in window L{start + 1}..L{end}"))
            continue
        false_positives = [
            line.strip() for line in window_lines if expected in line and _V1_HTTP_RE.search(line)
        ]
        if false_positives:
            drift.append(
                (rel, ln, "regex false-positive on import line: " + "; ".join(false_positives))
            )
    assert not drift, (
        "TS module-import discriminator drift detected. The sentinel's "
        "negative-lookbehind regex must exclude relative-path module "
        "specifiers; if a discriminator false-positives, fix the regex "
        "(do NOT add entries to GROUP_C_ALLOWLIST):\n"
        + "\n".join(f"  {rel}:{ln}  {reason}" for rel, ln, reason in drift)
    )


# ---------------------------------------------------------------------------
# Sentinel #8 augment (smoke-blocker-v2-create, P9.8gamma):
# guard against unauthorized ``/api/v2/orgs-spec/`` HTTP literals.
#
# The Group A relocation (``/api/v2/orgs-spec/...``, P9.7a-2a) is a
# *separate* persistence sub-app from the P9.7 mint runtime
# (``/api/v2/orgs/...``). When a frontend caller meant to create
# orgs lands on ``orgs-spec`` instead of mint, the new org goes to
# the wrong store and the sidebar (which reads from mint) never
# shows it -- the BLOCKER discovered during smoke UX investigation.
#
# This augment locks down the post-fix state: the only legitimate
# Group A frontend callers are the SSE stream client + its unit
# test (the SSE route is genuinely served by the orgs-spec router,
# see ``api/routes/orgs_v2_stream.py``). All other ``orgs-spec``
# HTTP literals must use the mint runtime instead.
# ---------------------------------------------------------------------------

_ORGS_SPEC_HTTP_RE = re.compile(r"/api/v2/orgs-spec")

# Strict allowlist of legitimate Group A ``/api/v2/orgs-spec/``
# HTTP literals in the frontend tree. Each entry: (repo-relative
# path, line, justification).
# NOTE (2026-06 refresh): commit ``04b00c4f`` migrated the SSE client's
# DEFAULT path off Group A (``/api/v2/orgs-spec/{id}/stream``) onto the
# Sprint-9 mint alias ``/api/v2/orgs/{id}/events/stream`` (served by
# ``api/routes/orgs_v2_runtime_dispatch.py``). The orgs-spec literals that
# remain are therefore (a) docstrings documenting the still-served legacy
# route for backward-compat, and (b) the unit test that exercises the
# ``apiPath`` override targeting that legacy route. Re-pinned to their
# current line numbers; these are legitimate (the legacy route is still
# mounted), so they stay on the allowlist rather than being deleted.
LEGITIMATE_ORGS_SPEC_CALLERS: list[tuple[str, int, str]] = [
    (
        "apps/setup-center/src/api/v2Stream.ts",
        7,
        "Module docstring documenting the still-served legacy Group A SSE "
        "endpoint (`GET /api/v2/orgs-spec/{id}/stream`) the client can target "
        "via apiPath override.",
    ),
    (
        "apps/setup-center/src/api/v2Stream.ts",
        119,
        "V2StreamOptions.apiPath doc showing the legacy orgs-spec override "
        "alternative to the default events/stream alias.",
    ),
    (
        "apps/setup-center/src/api/__tests__/v2Stream.test.ts",
        77,
        "Unit-test apiPath override input pinning the legacy orgs-spec SSE "
        "URL shape (legacy route still served by orgs_v2_stream.py).",
    ),
    (
        "apps/setup-center/src/api/__tests__/v2Stream.test.ts",
        80,
        "Unit-test assertion on the resolved legacy orgs-spec SSE URL.",
    ),
    (
        "apps/setup-center/src/api/orgs.ts",
        2,
        "Doc-only NOTE comment explaining the orgs-spec namespace is "
        "intentionally NOT called here (mint runtime is canonical); no "
        "actual HTTP call — a documentation reference, not a caller.",
    ),
]


def _scan_orgs_spec_http_hits() -> list[tuple[Path, int, str]]:
    """Return (file, line, stripped_line) for every regex hit."""
    hits: list[tuple[Path, int, str]] = []
    for ext in ("*.ts", "*.tsx"):
        for file in sorted(_FRONTEND_SRC.rglob(ext)):
            try:
                text = file.read_text(encoding="utf-8")
            except OSError:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if _ORGS_SPEC_HTTP_RE.search(line):
                    hits.append((file, n, line.strip()))
    return hits


def test_frontend_no_unauthorized_orgs_spec_paths() -> None:
    """Zero ``/api/v2/orgs-spec/`` HTTP literals outside the Group A SSE allowlist.

    The mint runtime at ``/api/v2/orgs/...`` is the canonical v2
    orgs surface; orgs-spec is a parallel Group A sub-app reserved
    for the SSE stream + the original spec serializer. Any *new*
    frontend literal targeting orgs-spec is almost certainly a
    routing bug (orgs go to a separate store, sidebar misses them).
    Add to ``LEGITIMATE_ORGS_SPEC_CALLERS`` only when there is a
    real Group A endpoint behind the URL with a justification.
    """
    allowed_keys = {(rel, ln) for rel, ln, _ in LEGITIMATE_ORGS_SPEC_CALLERS}
    unauthorized: list[tuple[str, int, str]] = []
    for file, ln, snippet in _scan_orgs_spec_http_hits():
        rel = file.relative_to(_REPO).as_posix()
        if (rel, ln) not in allowed_keys:
            unauthorized.append((rel, ln, snippet))
    assert not unauthorized, (
        "Unauthorized ``/api/v2/orgs-spec/`` HTTP literal(s) found in "
        "apps/setup-center/src/. The mint runtime at /api/v2/orgs/... "
        "is the canonical v2 orgs surface; orgs-spec is a separate "
        "Group A sub-app whose only legitimate frontend caller is the "
        "SSE stream client. Switch to /api/v2/orgs/... or, if the new "
        "site is a genuine Group A endpoint, add it to "
        "LEGITIMATE_ORGS_SPEC_CALLERS with rationale.\n"
        + "\n".join(f"  {rel}:{ln}  {snippet}" for rel, ln, snippet in unauthorized)
    )


def test_legitimate_orgs_spec_callers_still_present() -> None:
    """Allowlisted Group A SSE literals must still be at their recorded sites.

    Drift alarm twin to ``test_group_c_allowlist_paths_still_present``:
    if v2Stream.ts gets refactored or the SSE endpoint relocates, the
    allowlist becomes stale and the augment silently permits paths
    that no longer exist anywhere in the tree.
    """
    missing: list[tuple[str, int, str]] = []
    for rel, ln, justification in LEGITIMATE_ORGS_SPEC_CALLERS:
        path = _REPO / rel
        if not path.is_file():
            missing.append((rel, ln, f"file missing: {path}"))
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        start = max(0, ln - 5)
        end = min(len(lines), ln + 5)
        if not any(_ORGS_SPEC_HTTP_RE.search(s) for s in lines[start:end]):
            missing.append(
                (
                    rel,
                    ln,
                    f"no orgs-spec literal in window L{start + 1}..L{end} ({justification!r})",
                )
            )
    assert not missing, (
        "LEGITIMATE_ORGS_SPEC_CALLERS drift: an allowlisted Group A "
        "SSE literal is no longer at its recorded location. Re-pin "
        "the line number, or remove the entry if the SSE client has "
        "migrated off orgs-spec:\n"
        + "\n".join(f"  {rel}:{ln}  {reason}" for rel, ln, reason in missing)
    )
