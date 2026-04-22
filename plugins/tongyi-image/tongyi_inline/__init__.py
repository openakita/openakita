"""tongyi-image vendored helpers.

These modules used to live under ``openakita_plugin_sdk.contrib.*`` but were
inlined here in 0.7.0 when the SDK retracted its contrib subpackage. Each
file is a verbatim copy of the SDK 0.6.0 version it was forked from; do not
re-import the SDK contrib path — it no longer exists.

Modules:

- :mod:`tongyi_inline.upload_preview`  — ``add_upload_preview_route`` /
  ``build_preview_url`` for the ``/uploads`` route.
- :mod:`tongyi_inline.storage_stats`   — ``collect_storage_stats`` /
  ``StorageStats`` for the dashboard storage card.
"""
