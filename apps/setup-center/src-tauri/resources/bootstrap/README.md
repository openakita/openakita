# OpenAkita Bootstrap Resources

This directory is packaged into the Tauri desktop app and is intentionally
small. It bootstraps the mutable runtime environments under:

```text
~/.openakita/runtime/app-venv
~/.openakita/runtime/agent-venv
```

Expected packaged files:

- `manifest.json`: bootstrap metadata consumed by the Tauri runtime manager.
- `bin/uv` or `bin/uv.exe`: uv binary for creating venvs and installing wheels.
- `wheels/openakita-<version>-py3-none-any.whl`: OpenAkita wheel for app runtime.
- `wheelhouse/`: optional enterprise/offline dependency wheelhouse.

`build/prepare_bootstrap_resources.py` defaults to a gitignored staging output
under `build/bootstrap-output` for local validation. CI/release packaging passes
`--commit-resources` to write into this directory intentionally. Do not commit
generated `bin/uv*` or wheel files from a local run unless you are updating the
tracked release bootstrap resources on purpose.

The bootstrap package must not contain a full Python or conda environment. If a
Python seed is added later, keep it explicit in `manifest.json` and small enough
to preserve the lightweight installer goal.
