# tools-tmp/

Local scratch directory for **all** temporary artifacts produced during development and debugging.

This entire directory is git-ignored (except this README). Put things here instead of the repo root.

## Examples

- Crash dump analysis: `tools-tmp/feedback-downloads/`, `tools-tmp/*.py`
- Diff comparisons: `tools-tmp/diff_*.txt`
- Symbol files: `tools-tmp/symbols/`
- Temporary scripts, logs, exports, etc.

## Rule

> Never write temporary/throwaway files to the repository root. Use `tools-tmp/` instead.

This prevents accidental commits via `git add -A` regardless of what the files are named.
