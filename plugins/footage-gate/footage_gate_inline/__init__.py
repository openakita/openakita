# ruff: noqa: N999
"""footage-gate vendored helpers.

These modules used to live under ``openakita_plugin_sdk.contrib.*`` but were
inlined here in 1.0.0 when the SDK retracted its ``contrib`` subpackage. Each
file is a verbatim copy of the SDK 0.6.0 version it was forked from; do not
re-import the SDK contrib path — it no longer exists. Forked from
``plugins/subtitle-craft/subtitle_craft_inline`` (the post-SDK-0.7.0 reference
implementation).

Modules:

- :mod:`footage_gate_inline.upload_preview`    — ``add_upload_preview_route`` /
  ``build_preview_url`` for the ``/uploads`` route (preview source media,
  exported clips, qc_grid.png).
- :mod:`footage_gate_inline.storage_stats`     — ``collect_storage_stats`` /
  ``StorageStats`` for the Settings page storage card.
- :mod:`footage_gate_inline.parallel_executor` — bounded-concurrency executor
  used by ``cut_qc`` to run per-cutpoint frame-diff / waveform-spike checks
  in parallel without saturating the host.
- :mod:`footage_gate_inline.system_deps`       — ``SystemDepsManager`` for
  detecting + auto-installing FFmpeg on Windows / macOS / Linux, plus
  ``probe_ffmpeg_capabilities`` (version >= 4.4 + filter availability for
  ``signalstats`` / ``eq`` / ``subtitles`` / ``tonemap`` / ``zscale``).
  Forked verbatim from ``plugins/subtitle-craft/subtitle_craft_inline``;
  the base manager is dep-agnostic (parameterised by ``dep_id``) so it sits
  comfortably in any plugin that needs to gate on a system binary.
"""
