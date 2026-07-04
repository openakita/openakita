"""Lightweight territory guard for finance-auto commits.

Round-2 optimisation #4 (audit Section 11 item 4): the commit
``38b46b3f orgs_v2`` (9 files, +2278 lines) drifted into the
fix-round-1 audit window even though it had nothing to do with
finance-auto.  This script reads ``git log`` for a commit range,
classifies every commit by its subject scope, and flags two patterns:

* **ERROR** (exit 1) — a commit declared a ``finance-auto*`` scope
  (``feat(finance-auto)``, ``fix(finance-auto-ui)``, ...) but
  modified files OUTSIDE the territory documented in
  ``plugins/finance-auto/CONTRIBUTING.md``.

* **WARNING** (does not affect exit code) — a commit WITHOUT a
  ``finance-auto*`` scope touched files inside
  ``plugins/finance-auto/**``.  That is exactly the
  ``orgs_v2 drifted in`` pattern; whether it is intentional is a
  human judgement call so we do not auto-fail.

Usage::

    d:\\OpenAkita\\.venv\\Scripts\\python.exe ^
        plugins/finance-auto/scripts/check_territory.py ^
        [--commit-range A..B] [--verbose]

Default range is ``HEAD~1..HEAD`` -- check the single most recent
commit, suitable for a manual smoke before push or wiring into a
pre-push hook.

Exit codes
==========
* 0 — no ERROR-class territory violations
* 1 — at least one ERROR-class violation (the offending commit(s) and
  files are printed to stderr)
* 2 — argument / git error
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# Path globs (relative to repo root) allowed for finance-auto-scope
# commits.  Mirrors plugins/finance-auto/CONTRIBUTING.md section 1.
ALLOWED_PATTERNS: tuple[str, ...] = (
    "plugins/finance-auto/finance_auto_backend/**",
    "plugins/finance-auto/tests/**",
    "plugins/finance-auto/scripts/**",
    "plugins/finance-auto/ui/dist/**",
    "plugins/finance-auto/CHANGELOG.md",
    "plugins/finance-auto/CONTRIBUTING.md",
    "plugins/finance-auto/README.md",
    # Tauri native bridge for the plugin's native commands (single-file
    # scope -- broader Tauri shell changes belong to a separate
    # setup-center-tauri commit).
    "apps/setup-center/src-tauri/src/finance*.rs",
    # Finance-specific Tauri TypeScript bridge under setup-center src/
    # tree.  Historically lives there because the plugin manager loads
    # bridge modules from the host app; only finance-prefixed files
    # are in finance-auto territory.  Plugin-host shared infrastructure
    # (``plugin-bridge-host.ts``, ``plugin-router.ts``, etc.) is NOT.
    "apps/setup-center/src/lib/native/finance-*.ts",
    # Workspace-level finance-auto audit / status reports.  These live
    # at repo root by convention (``_finance_plugin_*.md`` and
    # ``_fix_round*_*.md``) -- they are plugin-status artefacts, not
    # plugin code, but they ARE finance-auto territory in spirit.
    "_finance_plugin_*.md",
    "_fix_round*.md",
    "_fix_round*_*.md",
)

FINANCE_AUTO_PATH_PREFIX = "plugins/finance-auto/"

# Recognised conventional-commit scopes that signal "this is a
# finance-auto commit".  Sub-scopes give a stricter intent signal but
# share the same territory.
SCOPE_RE = re.compile(
    r"^(?P<type>feat|fix|chore|docs|refactor|perf|test|build|ci|revert)"
    r"\((?P<scope>[^)]+)\)"
)
FINANCE_AUTO_SCOPE_PREFIX = "finance-auto"


def _match_allowed(path: str) -> bool:
    """Posix-style path matcher against ALLOWED_PATTERNS."""
    p = path.replace("\\", "/")
    for pattern in ALLOWED_PATTERNS:
        if fnmatch.fnmatch(p, pattern):
            return True
        # fnmatch's ``**`` semantics differ from globstar; treat
        # ``foo/**`` as "anything under foo/" so nested directories
        # match too.
        if pattern.endswith("/**") and p.startswith(pattern[: -len("/**")] + "/"):
            return True
    return False


def _scope_of(subject: str) -> str | None:
    m = SCOPE_RE.match(subject.strip())
    return m.group("scope") if m else None


def _is_finance_auto_scope(scope: str | None) -> bool:
    return bool(scope) and (
        scope == FINANCE_AUTO_SCOPE_PREFIX
        or scope.startswith(FINANCE_AUTO_SCOPE_PREFIX + "-")
    )


def _run_git(args: list[str]) -> str:
    proc = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (rc={proc.returncode}):\n"
            f"{proc.stderr.strip()}"
        )
    return proc.stdout


def _commits_in_range(commit_range: str) -> list[str]:
    out = _run_git(["log", "--format=%H", commit_range])
    return [line.strip() for line in out.splitlines() if line.strip()]


def _commit_subject(sha: str) -> str:
    return _run_git(["log", "-1", "--format=%s", sha]).strip()


def _commit_files(sha: str) -> list[str]:
    out = _run_git(["show", "--no-renames", "--name-only", "--format=", sha])
    return [line.strip() for line in out.splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument(
        "--commit-range", default="HEAD~1..HEAD",
        help="git commit range to scan, e.g. origin/main..HEAD (default: HEAD~1..HEAD)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="print per-commit details even on PASS",
    )
    args = parser.parse_args()

    try:
        shas = _commits_in_range(args.commit_range)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if not shas:
        print(f"check_territory: no commits in range {args.commit_range}")
        return 0

    print(
        f"check_territory: scanning {len(shas)} commit(s) in {args.commit_range}"
    )

    errors: list[tuple[str, str, list[str]]] = []  # (sha, subject, bad_files)
    warnings: list[tuple[str, str, list[str]]] = []
    clean = 0

    for sha in shas:
        subject = _commit_subject(sha)
        scope = _scope_of(subject)
        files = _commit_files(sha)
        is_fa_scope = _is_finance_auto_scope(scope)

        outside_files = [f for f in files if not _match_allowed(f)]
        touches_fa = [f for f in files if f.startswith(FINANCE_AUTO_PATH_PREFIX)]

        if is_fa_scope and outside_files:
            errors.append((sha, subject, outside_files))
            print(
                f"  ERROR  {sha[:10]}  {subject}\n"
                f"         scope={scope}; "
                f"{len(outside_files)} file(s) outside territory:"
            )
            for f in outside_files:
                print(f"           - {f}")
        elif not is_fa_scope and touches_fa:
            warnings.append((sha, subject, touches_fa))
            print(
                f"  WARN   {sha[:10]}  {subject}\n"
                f"         scope={scope or '(none)'}; touches finance-auto "
                f"despite non-finance-auto scope ({len(touches_fa)} file(s)):"
            )
            for f in touches_fa:
                print(f"           - {f}")
        else:
            clean += 1
            if args.verbose:
                print(
                    f"  OK     {sha[:10]}  {subject}\n"
                    f"         scope={scope or '(none)'}; {len(files)} file(s)"
                )

    print("\ncheck_territory summary")
    print(f"  scanned         : {len(shas)}")
    print(f"  clean           : {clean}")
    print(f"  warnings (drift): {len(warnings)}")
    print(f"  errors          : {len(errors)}")

    if errors:
        print(
            "\nFAIL -- at least one commit declared a finance-auto scope "
            "but modified files outside the territory.\n"
            "Refactor or re-scope the commit; see "
            "plugins/finance-auto/CONTRIBUTING.md sections 1 and 3."
        )
        return 1

    if warnings:
        print(
            "\nPASS (with warnings) -- one or more commits touched "
            "finance-auto without declaring it in their scope.  This is "
            "the 'orgs_v2 drifted in' pattern from audit Section 11 item "
            "4; review whether each warning was intentional."
        )
    else:
        print("\nPASS -- no territory violations")

    return 0


if __name__ == "__main__":
    sys.exit(main())
