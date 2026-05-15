# Changelog · happyhorse-video / 快乐马工作室

All notable changes to this plugin are recorded here. The plugin follows
[SemVer](https://semver.org/) and the project's standard
"date-versioned changelog" convention.

## [1.0.0] — 2026-05-15

Initial release. Bailian-powered unified video studio merging the spirit
of [`plugins/seedance-video`](../seedance-video/) (storyboard long-video
pipeline, prompt optimizer, ffmpeg concat) and
[`plugins/avatar-studio`](../avatar-studio/) (digital-human modes, OSS
uploader, CosyVoice / Edge-TTS) on a single backend (Aliyun DashScope /
Bailian).

### Added

- **HappyHorse 1.0 family** as the default video engine across `t2v` /
  `i2v` / `r2v` / `video_edit` modes (native audio-video sync, 7-language
  lip-sync, 720P/1080P, 3-15s).
- **Wan 2.6 / 2.7 fallback** registered as alternative model picks per
  mode: `wan2.6-t2v`, `wan2.6-i2v` / `wan2.6-i2v-flash`,
  `wan2.6-r2v` / `wan2.6-r2v-flash`, and `wan2.7-i2v` (multimodal:
  first-frame / first-and-last-frame / video-continuation).
- **5 digital-human modes** ported from avatar-studio: `photo_speak`
  (`wan2.2-s2v`), `video_relip` (`videoretalk`), `video_reface`
  (`wan2.2-animate-mix`), `pose_drive` (`wan2.2-animate-move`),
  `avatar_compose` (`wan2.7-image` → s2v).
- **Long-video storyboard pipeline** ported from seedance-video:
  AI-driven shot decomposition, serial / parallel chain generation,
  ffmpeg concat with optional crossfade.
- **Unified TTS**: CosyVoice-v2 (12 system voices + custom clones) and
  Edge-TTS (free, 12 Chinese voices).
- **Per-mode model dropdown** in CreateTab + `default_model_<mode>` in
  Settings. Submitted task without explicit `model` falls back to the
  per-mode default.
- **OSS-backed input pipeline** (signed HTTPS URLs, 6h TTL).
- **OrgRuntime workbench protocol**: every `hh_*` tool returns
  `video_url` / `video_path` / `last_frame_url` / `local_paths` /
  `asset_ids`, and every input schema accepts `from_asset_ids` so the
  node can consume upstream image-workbench output without rehosting.
- **Black-themed React/Babel single-file UI** (7 tabs: Create / Tasks /
  Storyboard / Voices / Figures / Prompt / Settings) with Iconify SVG
  icons inlined into `_assets/icons.js`.
- **Org template** `happyhorse-video-studio` registered in
  [`src/openakita/orgs/templates.py`](../../src/openakita/orgs/templates.py)
  for end-to-end "Bailian AIGC video studio" orchestration.
- Companion test plan doc
  [`docs/happyhorse-video-test-plan.md`](../../docs/happyhorse-video-test-plan.md).

### Notes

- HappyHorse 1.0 rejects the legacy params `with_audio` / `size` /
  `quality` / `fps` / `audio`; the client validates and surfaces a
  clear `error_kind: client` instead of letting DashScope late-fail.
- Wan 2.6 uses the legacy protocol (`size: "1280*720"`); Wan 2.7-i2v
  and HappyHorse use the new async protocol (`resolution: "720P"`).
  Both routed through `happyhorse_dashscope_client.py` via the
  `endpoint_family` / `protocol_version` registry fields.
- DashScope async per-key concurrency cap = 1; submits are serialised
  by an internal `asyncio.Semaphore(1)`.
