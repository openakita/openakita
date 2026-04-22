"""Monorepo / bundled-tree fallback loader for ``openakita_plugin_sdk``.

Why this exists
---------------
Since plugin scaffold contrib (``openakita_plugin_sdk.contrib``) was added,
plugin entry modules import ``openakita_plugin_sdk`` directly. Outside a
production install (where the SDK is shipped via the ``[plugins]`` extra or
bundled wheel) the package is **not** discoverable, and every such plugin
fails with::

    ModuleNotFoundError: No module named 'openakita_plugin_sdk'

This module restores the monorepo development experience: when the SDK is
not already importable, it walks parent directories looking for the
``openakita-plugin-sdk/src/openakita_plugin_sdk`` source tree and prepends
its ``src`` directory to ``sys.path``.

Design constraints
------------------
- **Idempotent** — calling it twice never duplicates ``sys.path`` entries.
- **Silent on success** — does not pollute logs in production where the SDK
  is properly installed.
- **No imports of openakita.*** — plugins/manager imports this at boot, so
  we keep the dependency surface to the standard library only.
- **Never raises** — a failure here must not break agent startup; plugins
  will simply fail individually (which is the existing behaviour).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_SDK_PACKAGE_NAME = "openakita_plugin_sdk"
_SDK_SOURCE_DIRNAME = "openakita-plugin-sdk"
_INJECTED_PATH: Path | None = None


def ensure_plugin_sdk_on_path() -> Path | None:
    """Make ``openakita_plugin_sdk`` importable, returning the package path.

    Resolution order:

    1. If the package is already importable (production / explicit install),
       return its existing location and do nothing.
    2. If a previous call already injected a monorepo path, return it.
    3. Walk parent directories of this file looking for
       ``<repo>/openakita-plugin-sdk/src/openakita_plugin_sdk/__init__.py``.
       When found, prepend the ``src`` directory to ``sys.path`` exactly
       once and return the package path.
    4. Otherwise return ``None`` and let the caller report the situation.
    """
    global _INJECTED_PATH

    try:
        import importlib

        spec = importlib.util.find_spec(_SDK_PACKAGE_NAME)
    except Exception:
        spec = None

    if spec is not None and spec.origin:
        return Path(spec.origin).resolve().parent

    if _INJECTED_PATH is not None and _INJECTED_PATH.exists():
        return _INJECTED_PATH

    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate_src = parent / _SDK_SOURCE_DIRNAME / "src"
        candidate_pkg = candidate_src / _SDK_PACKAGE_NAME / "__init__.py"
        if candidate_pkg.is_file():
            src_str = str(candidate_src)
            if src_str not in sys.path:
                sys.path.insert(0, src_str)
            _INJECTED_PATH = candidate_src / _SDK_PACKAGE_NAME
            logger.info(
                "openakita_plugin_sdk not installed; injected monorepo source from %s",
                candidate_src,
            )
            return _INJECTED_PATH

    return None


__all__ = ["ensure_plugin_sdk_on_path"]
