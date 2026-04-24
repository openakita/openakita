# footage-gate · Validation Notes

This document records the **defensive measures** baked into v1.0 against
real failures observed in the upstream reference repos
(`browser-use/video-use`, `GVCLab/CutClaw`, `calesthio/OpenMontage`),
plus the **canonical EDL contract** the `cut_qc` mode consumes. Anything
listed here has at least one matching test case in `tests/` so the
defence cannot regress silently.

---

## 1 · EDL standard field names

The `cut_qc` mode consumes an **EDL JSON** payload describing the cut
boundaries it should re-render. The accepted shape is:

```jsonc
{
  "output_resolution": [1080, 1920],   // [width, height], optional → defaults to (1920, 1080)
  "total_duration_s": 42.0,             // optional → computed from cuts when absent
  "cuts": [
    {
      "in_seconds": 0.0,                // STANDARD — preferred
      "out_seconds": 5.0,
      "source": {
        "media_type": "video",          // "video" | "image"
        "path": "/abs/path/to/clip.mp4"
      }
    },
    {
      // LEGACY — also accepted but emits an `edl_field_normalized` info issue
      "start_seconds": 5.0,
      "end_seconds": 10.0,
      "source": { "media_type": "image", "path": "/abs/path/to/poster.png" }
    }
  ],
  "subtitles": [
    {
      "in_seconds": 0.0,
      "out_seconds": 5.0,
      "MarginV": 90,                    // platform safe-zone for portrait outputs
      "text": "Hello"
    }
  ],
  "overlays": [
    { "in_seconds": 0.0, "out_seconds": 5.0, "type": "logo" }
  ],
  "filter_chain": [                     // optional — order matters for cut_qc check (c)
    "subtitles", "overlay"
  ]
}
```

Implementation: see `parse_edl` / `NormalizedEdl` in
`footage_gate_qc.py`. The route layer accepts the EDL as a dict in the
`POST /tasks` body's `params.edl` field.

---

## 2 · Upstream defects defended against

Each row links the upstream issue / PR, the file & function that
implements the defence, and the test case that pins it.

### Defence #1 — HDR tone-mapping safety

| Item | Value |
| --- | --- |
| Upstream | [video-use PR #6 — HDR `eq=` regression](https://github.com/browser-use/video-use/pull/6) |
| Symptom | iPhone HLG / Sony PQ footage rendered with `eq=gamma=` produces over-saturated, contrast-blown frames after platform re-encode (TikTok / IG / YT). macOS QuickTime auto-tone-maps and hides the bug locally. |
| Defence | `is_hdr_source()` probes `color_transfer ∈ HDR_TRANSFERS = ("smpte2084", "arib-std-b67")`. When true, `prepare_filter_chain()` prepends `TONEMAP_CHAIN` (the `zscale + tonemap=hable + zscale` form from FFmpeg's official HDR→SDR cookbook) before the `eq=` filter. `apply_grade()` and `remux_from_edl()` both honour this. The source_review report adds an `hdr_source` risk to alert the user. |
| Code | `footage_gate_ffmpeg.is_hdr_source` · `footage_gate_models.TONEMAP_CHAIN` · `footage_gate_grade.prepare_filter_chain` · `footage_gate_grade.apply_grade` · `footage_gate_qc.run_qc_with_remux` |
| Tests | `tests/test_grade.py::TestPrepareFilterChain::test_prepends_tonemap_when_hdr` · `tests/test_pipeline.py::test_run_auto_color_pipeline_hdr_tonemap` |

### Defence #2 — Subtitle vertical safe-zone

| Item | Value |
| --- | --- |
| Upstream | [video-use PR #5 — `bold-overlay` MarginV too small for portrait UI safe-zones](https://github.com/browser-use/video-use/pull/5) |
| Symptom | `MarginV=35` puts the subtitle box inside the iOS gesture-bar / Android nav-bar / TikTok bottom UI on 1080×1920 portrait outputs — the subtitle is invisible to the viewer. |
| Defence | `subtitle_overlay_check()` raises a `subtitle_in_safe_zone` issue when output is portrait (`h > w`) and `subtitle.MarginV < MIN_SUBTITLE_MARGINV_VERTICAL = 90`. The auto-remux loop's fix bumps the value to 90 (matching the upstream PR's recommended floor). |
| Code | `footage_gate_models.MIN_SUBTITLE_MARGINV_VERTICAL` · `footage_gate_qc.subtitle_overlay_check` · `footage_gate_qc._apply_fix_strategies` |
| Tests | `tests/test_qc.py::TestSubtitleOverlayCheck::test_portrait_safe_zone_warning` · `tests/test_qc.py::TestRunQcWithRemux::test_auto_remux_bumps_marginv` |

### Defence #3 — `tool_registry` removal in source review

| Item | Value |
| --- | --- |
| Upstream | [OpenMontage PR #46 — `tool_registry.get_tool` API change](https://github.com/calesthio/OpenMontage/pull/46) |
| Symptom | `source_media_review.py` originally called `tool_registry.get_tool(name)` in 4 places. The new upstream API is `tool_registry.get(name)`; calling the old name raises `AttributeError` and the review pipeline crashes mid-task. |
| Defence | We do **not** introduce `tool_registry` at all — `footage_gate_review.py` calls `ffprobe` / PIL directly via the local subprocess shim. An AST-based regression test asserts no `import tool_registry` and no `.get_tool(` call in the module's code (docstrings excluded). |
| Code | `footage_gate_review.py` (entire module is `tool_registry`-free) |
| Tests | `tests/test_review.py::TestNoToolRegistryUsage::test_no_tool_registry_import` (uses `ast.parse` so docstrings cannot trip it) |

### Defence #4 — EDL field-name normalisation

| Item | Value |
| --- | --- |
| Upstream | [OpenMontage Issue #43 — `start_seconds` vs `in_seconds` confusion](https://github.com/calesthio/OpenMontage/issues/43) |
| Symptom | LLM-authored EDLs frequently use `start_seconds` / `end_seconds`, but the Remotion compositor expects `in_seconds` / `out_seconds`. The mismatch produces `undefined * fps == NaN` which crashes the Rust frame cache. |
| Defence | `parse_edl()` accepts **either** spelling and normalises every cut / subtitle / overlay to the standard `in_seconds` / `out_seconds` form. When a legacy spelling was seen, `NormalizedEdl.field_naming == "legacy"` and the QC report carries an `edl_field_normalized` info issue so the user can fix the source. |
| Code | `footage_gate_qc.parse_edl` · `footage_gate_qc.NormalizedEdl.field_naming` |
| Tests | `tests/test_qc.py::TestParseEdl::test_standard_naming` · `tests/test_qc.py::TestParseEdl::test_legacy_naming_normalises_to_in_out` |

### Defence #5 — Mixed video + image EDL cuts

| Item | Value |
| --- | --- |
| Upstream | [OpenMontage Issue #42 — Image cuts crash compositor](https://github.com/calesthio/OpenMontage/issues/42) |
| Symptom | An EDL that mixes `media_type: "video"` and `media_type: "image"` cuts crashes the Rust frame cache (`Option::unwrap on None` in `frame_cache.rs:257`) when the compositor first hits a still-image source. |
| Defence | `preprocess_image_cuts()` scans the EDL pre-render: every `media_type == "image"` cut is converted to a short MP4 loop sized to the cut's duration / target fps / target resolution, and the cut's `source.path` is rewritten to the loop. The QC report includes one `image_cut_preprocessed` info issue per rewritten cut. |
| Code | `footage_gate_qc.preprocess_image_cuts` |
| Tests | `tests/test_qc.py::TestPreprocessImageCuts::test_image_cuts_rewritten_to_mp4_loops` |

### Defence #6 — `usable_for` always emitted

| Item | Value |
| --- | --- |
| Upstream | [OpenMontage Issue #44 — `reference_image_url` hand-off lost](https://github.com/calesthio/OpenMontage/issues/44) |
| Symptom | `source_media_review` reported `usable_for: ["hero", "reference_image_to_video"]` for image inputs but the field was lost at the consumer side, so 38 % of valid hero images never reached the downstream `image_to_video` plugin. |
| Defence | `review_source_media()` always populates the `usable_for` field for every entry — including images — so v2.0 cross-plugin handoff (`assets_bus.meta_json.usable_for`) can carry the routing information without a schema migration. |
| Code | `footage_gate_review.review_source_media` (every entry sets `usable_for`) |
| Tests | `tests/test_review.py::TestReviewSourceMedia::test_image_entry_has_usable_for` |

### Defence #7 — Pure-NumPy silence detection

| Item | Value |
| --- | --- |
| Upstream | [CutClaw Issue #3 — `aubio` install fails on Python 3.10+ / NumPy 1.24+](https://github.com/GVCLab/CutClaw/issues/3) |
| Symptom | `pip install aubio` errors out on modern Python / NumPy combos (the only fix is `conda install -c conda-forge aubio`). |
| Defence | We do **not** depend on `aubio` / `madmom` / `librosa`. `compute_non_silent_intervals()` is a pure-NumPy port of the CutClaw `_compute_non_silent_intervals` algorithm (RMS → dB → morphological merge → pad). A regression test asserts no `aubio` / `madmom` / `librosa` import lurks in the module. |
| Code | `footage_gate_silence.compute_non_silent_intervals` · `requirements.txt` (only Pillow + numpy) |
| Tests | `tests/test_silence.py::TestNoForbiddenAudioDeps::test_no_audio_dep_imports` |

### Defence #8 — Minimum dependency set + FFmpeg ≥ 4.4

| Item | Value |
| --- | --- |
| Upstream | [OpenMontage Issue #18 / PR #21 — incomplete Python deps](https://github.com/calesthio/OpenMontage/issues/18) |
| Symptom | Fresh checkouts of OpenMontage failed `make demo` because the baseline requirements were under-specified. |
| Defence | `requirements.txt` pins explicit minimum versions (`Pillow>=10.0.0`, `numpy>=1.24.0`) and the README documents the FFmpeg ≥ 4.4 floor (with `signalstats` / `eq` / `subtitles` / `tonemap` filters). The Settings → System Dependencies panel surfaces missing / too-old FFmpeg with a `dependency` `error_kind`. |
| Code | `requirements.txt` · `README.md` · `footage_gate_inline/system_deps.py` (FFmpeg detector) |
| Tests | `tests/test_system_deps.py` |

---

## 3 · Defensive test guards (regression nets)

The following tests fail **immediately** if a future maintainer
accidentally re-introduces one of the upstream pitfalls:

| Guard | What it asserts | Test |
| --- | --- | --- |
| No `aubio` / `madmom` / `librosa` import | Source code of `footage_gate_silence.py` contains none of those import names. | `test_silence.py::TestNoForbiddenAudioDeps::test_no_audio_dep_imports` |
| No `tool_registry` usage in review module | AST of `footage_gate_review.py` contains no `import tool_registry` and no `.get_tool(` call (docstrings are ignored). | `test_review.py::TestNoToolRegistryUsage::test_no_tool_registry_import` |
| EDL legacy naming round-trips | `parse_edl({start_seconds, end_seconds})` emits `field_naming == "legacy"` and produces `in_seconds` / `out_seconds`. | `test_qc.py::TestParseEdl::test_legacy_naming_normalises_to_in_out` |
| Image cuts get rewritten | `preprocess_image_cuts()` converts every image cut to an MP4 loop and rewrites the EDL. | `test_qc.py::TestPreprocessImageCuts::test_image_cuts_rewritten_to_mp4_loops` |
| Portrait MarginV warning | A 1080 × 1920 EDL with `MarginV=35` raises a `subtitle_in_safe_zone` warning. | `test_qc.py::TestSubtitleOverlayCheck::test_portrait_safe_zone_warning` |
| HDR tonemap prepended | `prepare_filter_chain(..., hdr_source=True)` produces a filter starting with `TONEMAP_CHAIN`. | `test_grade.py::TestPrepareFilterChain::test_prepends_tonemap_when_hdr` |

---

## 4 · Upstream snapshot freeze

The vendored algorithms are pinned to the upstream commits below as of
**2026-04-23**. We deliberately **do not** auto-track upstream changes
afterwards — the local copies are owned by `footage-gate` from this
point on.

| Repo | URL | Snapshot |
| --- | --- | --- |
| video-use | https://github.com/browser-use/video-use | `1ffa36d` |
| CutClaw | https://github.com/GVCLab/CutClaw | `db48d08` (MERGED PR #4) |
| OpenMontage | https://github.com/calesthio/OpenMontage | `9e17263` |

Subsequent upstream changes are NOT auto-tracked.
