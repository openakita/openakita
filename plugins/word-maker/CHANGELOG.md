# Changelog

## 0.1.1

- Add structured dependency diagnostics for Settings and `/deps/check`,
  including required/optional status, purpose, and missing-impact feedback.
- Update the Settings UI to show the dependency report while keeping dependency
  checks read-only with no in-app pip installation.
- Add `brain_status` so Settings and `/deps/check` can distinguish missing
  `brain.access` permission from an unavailable OpenAkita LLM client.
- Make Word Maker Brain calls prefer `think_lightweight`, then `think`, then
  legacy `compiler_think`, with deterministic fallback on empty or invalid JSON
  responses.

## 0.1.0

- Add the self-contained Word Maker plugin shell, guided UI, project store,
  source extraction, DOCX template inspection/rendering, Brain-assisted planning,
  deterministic pipeline, audit checks, and optional PPT handoff metadata.

