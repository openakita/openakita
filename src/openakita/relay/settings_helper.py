"""Plugin-side helper for the standard relay override settings.

Every vendor plugin (happyhorse-video, tongyi-image, avatar-studio,
seedance-video, ...) reads a per-plugin settings dict that ends up
looking like::

    {
      "api_key":  "...",
      "base_url": "https://dashscope.aliyuncs.com",
      "relay_endpoint": "yunwu-video",          # optional
      "relay_fallback_policy": "official",      # optional
    }

This helper takes that dict and, when ``relay_endpoint`` is set,
overlays the resolved :class:`RelayReference`'s ``base_url`` /
``api_key`` on it. The fallback semantics match
``relay_fallback_policy``:

    - ``"official"`` (default): warn-and-keep on missing/unhealthy
      relay so the user is never blocked by a typo or a temporarily
      disabled relay.
    - ``"strict"``: raise :class:`SettingsRelayResolutionError` so the
      plugin can surface it via its own ``VendorError`` translation
      (each plugin's exception type is different — we cannot raise
      it from here without coupling).

Returning a dict instead of mutating in place keeps callers explicit
about which fields the helper may have touched.
"""

from __future__ import annotations

import logging
from typing import Any

from .resolver import RelayNotFound, RelayReference, resolve_relay_endpoint

logger = logging.getLogger(__name__)


class SettingsRelayResolutionError(Exception):
    """Raised when ``relay_fallback_policy == "strict"`` and the relay
    cannot be resolved. Plugins catch this and re-raise as their own
    vendor-specific exception type so the UI error surface stays
    consistent within each plugin.

    The ``user_message`` attribute already includes the relay name
    and the list of relays the user might have meant, so plugins can
    surface it verbatim.
    """

    def __init__(self, message: str, *, user_message: str | None = None):
        super().__init__(message)
        self.user_message = user_message or message


def apply_relay_override(
    settings: dict[str, Any],
    *,
    default_base_url: str = "",
    required_capability: str = "",
    plugin_name: str = "plugin",
) -> dict[str, Any]:
    """Return a *new* dict with relay overrides applied (if any).

    The input ``settings`` is never mutated. The returned dict is a
    shallow copy with the standard relay fields removed
    (``relay_endpoint`` / ``relay_fallback_policy``) and replaced by:

      - ``base_url`` overridden with the relay's URL
      - ``api_key`` overridden with the relay's key (only when the
        relay actually has one; empty relay keys mean "use the per-
        plugin key" — some relays serve public endpoints)
      - ``_relay_reference``: the resolved RelayReference, stashed
        for plugins that want to call ``ref.supports_model(...)``
        before submission

    Failure modes (governed by ``relay_fallback_policy``):

      - ``"strict"``: SettingsRelayResolutionError
      - anything else (default ``"official"``): warn and fall back
        to the per-plugin values

    Importing this helper is intentionally lazy in plugin code: if the
    openakita package is not on the plugin's sys.path (e.g. bundled
    plugin distributions) the plugin should ``except ImportError`` and
    skip the relay step entirely. We never crash from that path here.
    """
    if not isinstance(settings, dict):
        raise TypeError("settings must be a dict")

    out = dict(settings)
    relay_name = str(out.pop("relay_endpoint", "") or "").strip()
    policy = str(out.pop("relay_fallback_policy", "") or "official")
    if not relay_name:
        return out

    try:
        ref = resolve_relay_endpoint(
            relay_name,
            required_capability=required_capability or None,
        )
    except RelayNotFound as exc:
        if policy == "strict":
            available = ", ".join(exc.available) if exc.available else "（空）"
            user_msg = (
                f"中转站 {relay_name!r} 未找到或不支持所需能力 "
                f"({required_capability or '任意'})。当前可用: {available}。"
                "请到 LLM 配置页检查 relay_endpoints。"
            )
            raise SettingsRelayResolutionError(
                f"relay {relay_name!r} not resolvable: {exc}",
                user_message=user_msg,
            ) from exc
        logger.warning(
            "%s: relay %r unresolvable (%s); falling back to per-plugin settings",
            plugin_name,
            relay_name,
            exc,
        )
        return out
    except Exception as exc:  # noqa: BLE001
        # Anything else (network during a sync, import problem, ...)
        # is non-fatal — we never want plugin Settings updates to die
        # on a transient relay registry issue.
        logger.warning(
            "%s: relay resolution failed for %r: %s; falling back",
            plugin_name,
            relay_name,
            exc,
        )
        return out

    if isinstance(ref, RelayReference):
        if ref.base_url:
            out["base_url"] = ref.base_url
        elif default_base_url and not out.get("base_url"):
            out["base_url"] = default_base_url
        if ref.api_key:
            out["api_key"] = ref.api_key
        out["_relay_reference"] = ref
    return out
