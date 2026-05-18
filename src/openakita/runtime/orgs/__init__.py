"""Runtime v2 organisation persistence layer.

Two interchangeable backends share the same duck-typed contract
(list / get / create / patch / delete + close). The default is the
process-local :class:`JsonOrgStore` (a single ``data/orgs_v2.json``
file under :data:`settings.data_dir`). Operators can opt into the
multi-process-safe :class:`SqliteOrgStore` by setting
``ORGS_V2_BACKEND=sqlite`` in ``.env`` (P-RC-3).

Use :func:`get_default_store` to fetch the process-wide singleton;
use :func:`reset_default_store` to swap backend / path in tests.
"""

from __future__ import annotations

from .sqlite_store import SqliteOrgStore
from .store import JsonOrgStore, OrgNotFound, get_default_store, reset_default_store

__all__ = [
    "JsonOrgStore",
    "OrgNotFound",
    "SqliteOrgStore",
    "get_default_store",
    "reset_default_store",
]
