---
name: Bug Report
about: Report a reproducible bug to help us fix it faster
title: "[BUG] "
labels: bug, needs-triage
assignees: ""
---

## Checklist

- [ ] I searched existing issues and did not find a duplicate.
- [ ] I can reproduce this issue with current latest code/release.
- [ ] I removed sensitive data (API keys, tokens, personal info) from logs.

## Summary

A short description of the bug and why it matters.

## Steps to Reproduce

Please provide minimal and deterministic steps:

1. ...
2. ...
3. ...

## Expected Result

What should happen.

## Actual Result

What actually happened.

## Reproducibility

- Frequency: [Always / Often / Sometimes / Rarely]
- Regression: [Yes / No / Unknown]
- First known version (if any): [e.g. v1.10.10]

## Environment

- OS: [e.g. macOS 14.6 / Windows 11 / Ubuntu 22.04]
- Python: [e.g. 3.11.9]
- OpenAkita version/commit: [e.g. v1.10.10 / abc1234]
- Install method: [pip / source / desktop dmg / docker]
- Runtime mode: [quick setup / full setup / remote mode]
- Model provider + model: [e.g. OpenAI gpt-4o-mini]

## Configuration (optional)

Relevant config only (redacted):
- `.env` keys involved:
- `llm_endpoints.json` excerpt:

## Logs / Error Output

```text
Paste logs, stack trace, or error message here
```

## Screenshots / Video (optional)

Attach UI screenshots or short recordings if helpful.

## Additional Notes

Anything else that could help diagnose (workarounds, timing, network/proxy, etc.).

## Resolution Preference

How would you like this issue to be resolved? (Please select one)

- [ ] Needs maintainer fix (I can't submit a PR at the moment)
- [ ] I can fix this and submit a PR
- [ ] Not sure / needs discussion
