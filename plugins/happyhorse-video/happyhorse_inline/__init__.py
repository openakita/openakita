"""happyhorse-video vendored helpers.

These modules are a verbatim port from
``plugins/seedance-video/seedance_inline/`` (the bulk of the helpers) and
``plugins/avatar-studio/avatar_studio_inline/`` (oss_uploader,
dep_bootstrap, llm_json_parser). They used to live under
``openakita_plugin_sdk.contrib.*`` but were inlined per-plugin in SDK
0.7.0 when contrib was retracted.

Modules:

- :mod:`happyhorse_inline.vendor_client`      — ``BaseVendorClient`` /
  ``VendorError`` / ``ERROR_KIND_*``. Used by
  ``happyhorse_dashscope_client`` for HappyHorse 1.0 / Wan 2.6/2.7 /
  s2v / animate / videoretalk over plain HTTP.
- :mod:`happyhorse_inline.oss_uploader`       — ``OssUploader`` /
  ``OssConfig`` for pushing user uploads to Aliyun OSS and handing
  DashScope a 6-h signed URL. Default ``oss_path_prefix`` is
  ``happyhorse-video``.
- :mod:`happyhorse_inline.upload_preview`     — ``add_upload_preview_route``
  / ``build_preview_url`` for the ``/uploads`` static-file route.
- :mod:`happyhorse_inline.storage_stats`      — ``collect_storage_stats``
  / ``StorageStats`` for the Settings → Storage card.
- :mod:`happyhorse_inline.llm_json_parser`    — ``parse_llm_json_object``
  for storyboard / prompt-optimizer / qwen-vl JSON salvage.
- :mod:`happyhorse_inline.parallel_executor`  — ``run_parallel`` for the
  parallel long-video chain fan-out.
- :mod:`happyhorse_inline.system_deps`        — ``SystemDepsManager`` for
  in-plugin FFmpeg detection + one-click install (long-video concat).
- :mod:`happyhorse_inline.dep_bootstrap`      — ``ensure_importable`` /
  ``preinstall_async`` for lazy auto-install of ``oss2`` /
  ``edge-tts`` / ``mutagen`` against
  ``~/.openakita/modules/happyhorse-video/site-packages/``.
"""
