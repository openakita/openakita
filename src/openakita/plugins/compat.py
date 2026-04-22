"""Plugin version compatibility checking.

Validates plugin manifest ``requires`` against the running system:

- ``requires.openakita``  — minimum system version  (``>=1.27.0``)
- ``requires.plugin_api`` — compatible API major     (``~1``)
- ``requires.sdk``        — minimum SDK version      (``>=0.1.0``, warn-only)
- ``requires.python``     — minimum Python version   (``>=3.11``)
"""

from __future__ import annotations

import logging
import platform
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .manifest import PluginManifest

logger = logging.getLogger(__name__)

PLUGIN_API_VERSION = "1.0.0"
PLUGIN_UI_API_VERSION = "1.0.0"


@dataclass
class CompatResult:
    """Outcome of a compatibility check."""

    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def check_compatibility(manifest: PluginManifest) -> CompatResult:
    """Run all compatibility checks for *manifest*.

    Returns a ``CompatResult`` — callers should skip loading when ``ok`` is
    ``False`` and log any ``warnings`` regardless.
    """
    result = CompatResult()
    requires = manifest.requires
    if not requires:
        return result

    _check_openakita(manifest.id, requires.get("openakita", ""), result)
    _check_plugin_api(manifest.id, requires.get("plugin_api", ""), result)
    _check_sdk(manifest.id, requires.get("sdk", ""), result)
    _check_python(manifest.id, requires.get("python", ""), result)

    if manifest.has_ui:
        _check_plugin_ui_api(manifest.id, requires.get("plugin_ui_api", ""), result)

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_version(raw: str) -> tuple[int, ...] | None:
    """Parse a dotted version string into a tuple of ints."""
    raw = raw.strip().lstrip("v")
    parts = []
    for seg in raw.split("."):
        seg = seg.strip()
        digits = ""
        for ch in seg:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            return None
        parts.append(int(digits))
    return tuple(parts) if parts else None


def _get_system_version() -> tuple[int, ...]:
    try:
        from .. import __version__

        v = _parse_version(__version__)
        if v:
            return v
    except Exception:
        pass
    return (0, 0, 0)


def _check_openakita(plugin_id: str, spec: str, result: CompatResult) -> None:
    if not spec:
        return
    if not spec.startswith(">="):
        result.warnings.append(f"Unrecognised openakita spec '{spec}' (expected >=X.Y.Z)")
        return

    min_ver = _parse_version(spec[2:])
    if min_ver is None:
        result.warnings.append(f"Cannot parse openakita version in '{spec}'")
        return

    current = _get_system_version()
    if current < min_ver:
        msg = (
            f"Plugin '{plugin_id}' requires openakita {spec}, "
            f"current is {'.'.join(str(x) for x in current)}"
        )
        result.errors.append(msg)
        result.ok = False


def _check_plugin_api(plugin_id: str, spec: str, result: CompatResult) -> None:
    if not spec:
        return

    current = _parse_version(PLUGIN_API_VERSION)
    if current is None:
        return

    if spec.startswith("~"):
        req_major_str = spec[1:].strip()
        req_major = _parse_version(req_major_str)
        if req_major is None:
            result.warnings.append(f"Cannot parse plugin_api spec '{spec}'")
            return

        if req_major[0] != current[0]:
            msg = (
                f"Plugin '{plugin_id}' requires plugin_api {spec} "
                f"(major {req_major[0]}), current API is {PLUGIN_API_VERSION} "
                f"(major {current[0]})"
            )
            result.errors.append(msg)
            result.ok = False
        elif len(req_major) > 1 and len(current) > 1 and req_major[1] > current[1]:
            result.warnings.append(
                f"Plugin '{plugin_id}' was built for API ~{req_major_str}, "
                f"current is {PLUGIN_API_VERSION} — some features may be missing"
            )
    elif spec.startswith(">="):
        req = _parse_version(spec[2:])
        if req is None:
            result.warnings.append(f"Cannot parse plugin_api spec '{spec}'")
            return
        if current < req:
            msg = (
                f"Plugin '{plugin_id}' requires plugin_api {spec}, current is {PLUGIN_API_VERSION}"
            )
            result.errors.append(msg)
            result.ok = False
    else:
        result.warnings.append(f"Unrecognised plugin_api spec '{spec}' (expected ~N or >=X.Y.Z)")


def _check_sdk(plugin_id: str, spec: str, result: CompatResult) -> None:
    """SDK version is informational — warn only."""
    if not spec or not spec.startswith(">="):
        return
    req = _parse_version(spec[2:])
    if req is None:
        return

    try:
        from openakita_plugin_sdk.version import SDK_VERSION

        sdk = _parse_version(SDK_VERSION)
    except ImportError:
        sdk = None

    if sdk is None:
        result.warnings.append(
            f"Plugin '{plugin_id}' needs openakita-plugin-sdk {spec} but it is not "
            f"installed. Fix it with `pip install \"openakita[plugins]\"` "
            f"(or `pip install -e ./openakita-plugin-sdk` in monorepo dev). "
            f"Plugins importing from openakita_plugin_sdk.contrib will fail to load "
            f"until this is resolved."
        )
    elif sdk < req:
        result.warnings.append(
            f"Plugin '{plugin_id}' recommends openakita-plugin-sdk {spec}, "
            f"installed is {'.'.join(str(x) for x in sdk)}. "
            f"Upgrade with `pip install -U openakita-plugin-sdk`."
        )


def _check_python(plugin_id: str, spec: str, result: CompatResult) -> None:
    if not spec or not spec.startswith(">="):
        return
    req = _parse_version(spec[2:])
    if req is None:
        return
    current = sys.version_info[:3]
    if current < req:
        msg = f"Plugin '{plugin_id}' requires Python {spec}, current is {platform.python_version()}"
        result.errors.append(msg)
        result.ok = False


def _check_plugin_ui_api(plugin_id: str, spec: str, result: CompatResult) -> None:
    """Check plugin_ui_api compatibility — same logic as _check_plugin_api."""
    if not spec:
        return

    current = _parse_version(PLUGIN_UI_API_VERSION)
    if current is None:
        return

    if spec.startswith("~"):
        req_major_str = spec[1:].strip()
        req_major = _parse_version(req_major_str)
        if req_major is None:
            result.warnings.append(f"Cannot parse plugin_ui_api spec '{spec}'")
            return
        if req_major[0] != current[0]:
            result.warnings.append(
                f"Plugin '{plugin_id}' requires plugin_ui_api {spec} "
                f"(major {req_major[0]}), current UI API is {PLUGIN_UI_API_VERSION} "
                f"(major {current[0]}) — UI will not be loaded"
            )
        elif len(req_major) > 1 and len(current) > 1 and req_major[1] > current[1]:
            result.warnings.append(
                f"Plugin '{plugin_id}' was built for UI API ~{req_major_str}, "
                f"current is {PLUGIN_UI_API_VERSION} — some UI features may be missing"
            )
    elif spec.startswith(">="):
        req = _parse_version(spec[2:])
        if req is None:
            result.warnings.append(f"Cannot parse plugin_ui_api spec '{spec}'")
            return
        if current < req:
            result.warnings.append(
                f"Plugin '{plugin_id}' requires plugin_ui_api {spec}, "
                f"current is {PLUGIN_UI_API_VERSION} — UI will not be loaded"
            )
    else:
        result.warnings.append(
            f"Unrecognised plugin_ui_api spec '{spec}' (expected ~N or >=X.Y.Z)"
        )
