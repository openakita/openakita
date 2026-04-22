# Avatar Studio (avatar-studio)

DashScope-powered digital human studio. Phase 0 skeleton — full feature set
(photo speak / video relip / video reface / avatar compose, voice & figure
libraries) lands incrementally across Phases 1-6 per
`c:\Users\Peilong_Hong\.cursor\plans\一_·_认知校准（关键事实）_c907d768.plan.md`.

> Status: **Phase 0** — directory layout, vendored UI Kit, vendored helpers.
> Not yet usable. Track progress in CHANGELOG.md.

## Layout

```
plugins/avatar-studio/
├── plugin.json
├── plugin.py                       # entry (skeleton until Phase 4)
├── avatar_studio_inline/           # vendored helpers (no SDK contrib)
│   ├── vendor_client.py
│   ├── upload_preview.py
│   ├── storage_stats.py
│   ├── llm_json_parser.py
│   └── parallel_executor.py
├── tests/
│   ├── conftest.py
│   ├── fixtures/                   # DashScope response seeds (Phase 6)
│   └── integration/                # @pytest.mark.integration smoke tests
└── ui/dist/
    ├── index.html                  # React + Babel CDN single-file (Phase 5)
    └── _assets/                    # vendored UI Kit, no host mount
        ├── bootstrap.js
        ├── styles.css
        ├── icons.js
        ├── i18n.js
        └── markdown-mini.js
```

## Acceptance gates

- **Gate 1** Per-phase DoD: ruff / ruff format / mypy / pytest 0 errors.
- **Gate 2** Feature Parity Matrix 4 mode × 14 self-checks (Phase 5).
- **Gate 3** §7 user smoke test 5 steps (Phase 6).
