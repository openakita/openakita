"""Resolve a shared relay endpoint by name for plugin clients.

The plugin calls ``resolve_relay_endpoint("yunwu-image")`` and gets
back a :class:`RelayReference` carrying ``base_url`` (the URL to hit)
and ``api_key`` (already looked up from .env via ``api_key_env``).
The plugin never touches ``llm_endpoints.json`` directly — that way
when the relay config schema evolves we change one file, not eight.

Capability filtering
--------------------
Each relay entry typically declares which media kinds it serves via
its ``capabilities`` field (``"image"``, ``"video"``, ``"audio"``,
``"tts"``, ...). Plugins can pass ``required_capability="image"`` to
make sure a TTS-only relay is not accidentally returned to an image
plugin.

Catalog awareness
-----------------
``RelayReference`` exposes the relay's last-probed model catalog
(via ``EndpointConfig.supported_models``) so the plugin UI can grey
out models the relay does not actually carry. Plugins should consult
:meth:`RelayReference.supports_model` to decide whether to submit a
job before calling the vendor API.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from ..llm.endpoint_manager import EndpointManager
from ..llm.types import EndpointConfig


class RelayNotFound(KeyError):
    """Raised when ``resolve_relay_endpoint`` cannot find the name.

    Carries ``available`` so the plugin UI can show the user "you
    asked for X, but the relay list contains Y, Z" without a second
    round trip to the API.
    """

    def __init__(self, name: str, *, available: Sequence[str] = ()):
        super().__init__(name)
        self.name = name
        self.available = list(available)

    def __str__(self) -> str:
        return (
            f"relay endpoint {self.name!r} not found"
            + (f" (available: {', '.join(self.available)})" if self.available else "")
        )


@dataclass(frozen=True)
class RelayReference:
    """Resolved view of one ``relay_endpoints`` entry.

    The plugin should treat this as immutable; rerun
    :func:`resolve_relay_endpoint` after the user re-saves Settings
    to pick up the new ``base_url`` / ``api_key``.
    """

    name: str
    base_url: str
    api_key: str
    capabilities: list[str] = field(default_factory=list)
    supported_models: list[str] = field(default_factory=list)
    models_synced_at: float | None = None
    note: str | None = None
    extra: dict = field(default_factory=dict)

    def has_capability(self, cap: str) -> bool:
        cap_l = (cap or "").strip().lower()
        return any(c.strip().lower() == cap_l for c in self.capabilities)

    def supports_model(self, model: str) -> bool:
        """Mirror of :meth:`EndpointConfig.supports_model` — permissive
        when no catalog has been probed."""
        if not model:
            return True
        if not self.supported_models:
            return True
        target = model.strip().lower()
        return any((m or "").strip().lower() == target for m in self.supported_models)


# ─── Internal manager cache ─────────────────────────────────────────


def _resolve_workspace(workspace_dir: str | os.PathLike | None) -> Path:
    """Decide which workspace's llm_endpoints.json to read.

    Honours, in order:

    1. The explicit ``workspace_dir`` argument (plugin-supplied).
    2. ``OPENAKITA_WORKSPACE`` env var (set by ``openakita serve``).
    3. ``os.getcwd()`` as a last resort so the helper still works in
       a one-shot script.
    """
    if workspace_dir:
        return Path(workspace_dir)
    env_ws = os.environ.get("OPENAKITA_WORKSPACE", "").strip()
    if env_ws:
        return Path(env_ws)
    return Path.cwd()


def _build_reference(cfg: EndpointConfig) -> RelayReference:
    api_key = cfg.get_api_key() or ""
    extra = dict(cfg.extra_params or {})
    return RelayReference(
        name=cfg.name,
        base_url=cfg.base_url,
        api_key=api_key,
        capabilities=list(cfg.capabilities or []),
        supported_models=list(cfg.supported_models or []),
        models_synced_at=cfg.models_synced_at,
        note=cfg.note,
        extra=extra,
    )


# ─── Public API ─────────────────────────────────────────────────────


def list_relay_endpoints(
    workspace_dir: str | os.PathLike | None = None,
    *,
    required_capability: str | None = None,
    enabled_only: bool = True,
) -> list[RelayReference]:
    """Return all relay endpoints, optionally filtered by capability.

    Disabled entries are omitted by default (the user toggled them
    off) but ``enabled_only=False`` is available for the Settings UI
    that needs to render the full list including disabled rows.
    """
    ws = _resolve_workspace(workspace_dir)
    mgr = EndpointManager(ws)
    out: list[RelayReference] = []
    for raw in mgr.list_endpoints("relay_endpoints"):
        if enabled_only and raw.get("enabled", True) is False:
            continue
        try:
            cfg = EndpointConfig.from_dict(raw)
        except Exception:
            # Skip malformed entries rather than blowing up the whole
            # plugin UI — log on the EndpointManager side.
            continue
        ref = _build_reference(cfg)
        if required_capability and not ref.has_capability(required_capability):
            continue
        out.append(ref)
    return out


def resolve_relay_endpoint(
    name: str,
    workspace_dir: str | os.PathLike | None = None,
    *,
    required_capability: str | None = None,
) -> RelayReference:
    """Look up one relay endpoint by name.

    Raises :class:`RelayNotFound` (with ``available=`` populated) if
    the name is missing, the entry is disabled, or it lacks the
    requested capability. Plugins should catch the exception and
    surface ``str(exc)`` directly to the user — it is already
    actionable.
    """
    refs = list_relay_endpoints(
        workspace_dir,
        required_capability=required_capability,
        enabled_only=True,
    )
    name_l = (name or "").strip().lower()
    for ref in refs:
        if ref.name.strip().lower() == name_l:
            return ref
    raise RelayNotFound(
        name,
        available=[r.name for r in refs],
    )
