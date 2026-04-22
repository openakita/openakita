# avatar-studio CHANGELOG

## [Unreleased] — Phase 0 (skeleton)

### Added
- Plugin directory `plugins/avatar-studio/` (first-class, peer of
  `tongyi-image` / `seedance-video`).
- Vendored UI Kit assets under `ui/dist/_assets/` (5 files, ~60KB,
  copied from `plugins-archive/_shared/web-uikit/`).
- Vendored helpers under `avatar_studio_inline/` (5 files, forked from
  `plugins/seedance-video/seedance_inline/`):
  - `vendor_client.py` — `BaseVendorClient` + 9 `ERROR_KIND_*`.
  - `upload_preview.py` — `/uploads/...` route helper.
  - `storage_stats.py` — async storage walker.
  - `llm_json_parser.py` — 5-level fallback JSON extractor.
  - `parallel_executor.py` — bounded-concurrency executor.
- Skeleton `plugin.py` (PluginBase subclass that just logs).
- `plugin.json` (sdk `>=0.7.0,<0.8.0`, 9 tools, ui.entry).
- `tests/conftest.py` + `tests/test_smoke.py` (vendored helpers import +
  three-layer fallback assertion + `_assets` presence check).

### Notes
- Zero `openakita_plugin_sdk.contrib` imports (contrib was retracted in
  SDK 0.7.0; see commit `d6d0c964`).
- Zero `/api/plugins/_sdk/*` host-mount references (host stopped mounting
  these in 0.7.0; see commit `4cdf6275`).
- Earlier sibling `plugins-archive/avatar-speaker/` is untouched and
  remains for backward compatibility — avatar-studio does NOT inherit
  any code from it (different scope, different API surface).
