"""Build-info endpoint backing the frontend stale-bundle banner.

P-RC-2 commit P2.8 mitigation for Phase 7 in the original revamp
plan (red-prompt cache issues): the frontend embeds ``VITE_BUILD_ID``
at compile time and polls this endpoint every 60s; if the
``build_id`` returned here drifts away from the embedded one, the
SPA shows a sticky "新版本可用，请刷新页面" banner so operators do
not get stuck on a stale bundle after a backend redeploy that also
shipped a new SPA.

The endpoint is intentionally *unauthenticated* and *uncached* --
it is just a few-byte JSON read so the SPA can detect drift without
worrying about login state or stale CDN caches.

Resolution order for ``build_id`` (first non-empty wins):

1. ``OPENAKITA_BUILD_ID`` env var (CI / container override).
2. The ``__version__`` exposed by ``openakita`` package metadata
   (matches ``pyproject.toml``).
3. ``"dev"`` as a last resort.
"""

from __future__ import annotations

import os
import re
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["构建信息"])


def _resolve_build_id() -> str:
    env = os.environ.get("OPENAKITA_BUILD_ID", "").strip()
    if env:
        return env
    try:
        v = version("openakita")
        if v:
            return v
    except PackageNotFoundError:
        pass
    return "dev"


# ---------------------------------------------------------------------------
# Frontend bundle build-id detection (Fix-5 / exploratory v10 issue #5b)
# ---------------------------------------------------------------------------

# Pattern matching Vite's ``vite.config.ts`` dev fallback:
#   process.env.VITE_BUILD_ID || `dev-${Date.now().toString(36)}`
# Numeric Date.now() in base-36 is 7-9 lowercase alnum chars.
_DEV_BUILD_ID_PATTERN = re.compile(r'"(dev-[a-z0-9]{6,12})"')


def is_frontend_bundle_outdated(
    bundle_build_id: str | None,
    backend_version: str,
) -> bool:
    """Return ``True`` when the SPA bundle id lags the running backend.

    Single source of truth for the staleness rule that powers both
    the startup warning (server.py ``_check_frontend_bundle_freshness``)
    and the ``/api/health`` ``frontend_bundle.outdated`` field
    (exploratory v12 §10.2 follow-up).

    Rules:

    * ``None`` bundle id -> ``False`` (unknown; we never surface a
      warning when we cannot read the bundle, mirroring the startup
      check's "no traction, no banner" stance).
    * ``"dev-..."`` prefix -> always ``True`` because the Vite dev
      fallback (``dev-<base36 timestamp>``) cannot match any released
      backend semver.
    * Exact-match against ``backend_version`` -> ``False``; anything
      else -> ``True``.
    """
    if not bundle_build_id:
        return False
    if bundle_build_id.startswith("dev-"):
        return True
    return bundle_build_id != backend_version


def detect_frontend_bundle_build_id(dist_web: Path) -> str | None:
    """Best-effort: extract the ``__BUILD_ID__`` baked into a SPA bundle.

    Vite's ``define`` step substitutes ``__BUILD_ID__`` with a string
    literal at compile time, so the value ends up directly inside the
    entry ``index-*.js`` bundle. We:

    1. Read ``index.html`` to find the entry JS asset path.
    2. Scan that asset for the dev fallback marker ``"dev-<base36>"``.
    3. If a CI build set ``VITE_BUILD_ID=<backend version>``, the bundle
       contains the version literal -- which is already the canonical
       value, so a "matches backend" check is trivially satisfied; in
       that case we return ``None`` rather than reporting a false
       positive from one of the many semver literals in the bundle.

    Returns ``None`` if the bundle cannot be located or the marker is
    not found. The caller treats ``None`` as "unknown, no warning".
    """
    if not dist_web or not dist_web.is_dir():
        return None
    index_html = dist_web / "index.html"
    if not index_html.is_file():
        return None
    try:
        html = index_html.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    entries = re.findall(r'src="([^"]*/assets/index-[^"]+\.js)"', html)
    for entry in entries:
        rel = entry.split("/assets/")[-1]
        candidate = dist_web / "assets" / rel
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        match = _DEV_BUILD_ID_PATTERN.search(text)
        if match:
            return match.group(1)
    return None


@router.get("/build-info", summary="后端构建信息（用于前端检测过期 bundle）")
def get_build_info() -> dict[str, str]:
    """Return the running backend's build identifier.

    The frontend polls this every 60s and compares the response
    with its compile-time ``VITE_BUILD_ID``. A drift triggers the
    "请刷新页面" banner.
    """

    return {"build_id": _resolve_build_id()}
