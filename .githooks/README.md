# `.githooks/` — Project-level git hooks (opt-in)

This directory holds git hooks that the project ships for contributors who want them.
Git intentionally does not version-control `.git/hooks/`, so these hooks live in `.githooks/`
and you must opt in to them per clone.

## Currently shipped

| Hook | Purpose |
|------|---------|
| [`prepare-commit-msg`](./prepare-commit-msg) | Strip AI co-author trailers (`Co-authored-by: Cursor <cursoragent@cursor.com>`, `Made-with: Cursor`) that some AI coding tools auto-inject, so the git history attribution stays with the human contributor's `git config`. |

## How to enable for this clone

```bash
git config core.hooksPath .githooks
```

That is all. The setting is per-clone (lives in `.git/config`) and persists until you change it. To verify:

```bash
git config --get core.hooksPath
# .githooks
```

To disable:

```bash
git config --unset core.hooksPath
```

## Why opt-in and not auto-installed

Git's security model intentionally requires manual activation of repository-shipped hooks, because an automatically-running hook from an arbitrary clone would be a remote code execution vector. The project respects that design and does not try to work around it (e.g. via a post-checkout installer). Each contributor decides whether to trust and run these hooks.

## When you specifically need this

You should enable `core.hooksPath .githooks` if any of the following is true:

- You contribute through Cursor IDE, Cursor CLI, or Cursor Cloud Agent.
- You contribute through any AI coding tool that appends an attribution trailer to commits.
- You're not sure whether your tooling injects trailers, and you want a safety net.

If you commit by hand and your tools do not inject trailers, you do not need this hook.

## Trade-offs

- The hook fires on **every** local commit (whether AI-assisted or not). It only modifies the commit message when it actually finds a matching trailer line, so the overhead for normal commits is a single short `sed` invocation. Negligible.
- The hook does **not** rewrite commits already pushed to the remote. If a co-author trailer slipped into a past commit, use `git rebase -i` + `git commit --amend` to clean it up, then force-push (with the usual caveats).

## See also

- `.cursor/rules/no-cursor-coauthor-trailer.mdc` — Cursor-specific guidance for agents working in this repo, including how to disable the trailer at the source.
- `CONTRIBUTING.md` — overall contribution guide; mentions enabling this hook.
