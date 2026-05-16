"""Shared relay-station registry for plugin clients.

Plugins (happyhorse-video, tongyi-image, avatar-studio, ...) often
need a non-LLM HTTP endpoint — a relay station or aggregator that
serves image / video / TTS / animation models compatible with their
vendor SDK. Without this module each plugin would carry its own
``base_url`` + ``api_key`` settings, forcing the user to repeat the
same relay configuration ten times and to update ten places when
the relay changes URL.

This module exposes a tiny helper :func:`resolve_relay_endpoint`
that looks up an :class:`EndpointConfig` by ``name`` from the
``relay_endpoints`` list in ``llm_endpoints.json`` (managed by
:class:`openakita.llm.endpoint_manager.EndpointManager`) and returns
the resolved ``base_url`` + ``api_key`` + capability metadata. The
plugin layer therefore stays decoupled from EndpointManager's file
layout while still benefiting from probe / sync / fallback features
implemented in the LLM core.
"""

from .resolver import (
    RelayNotFound,
    RelayReference,
    list_relay_endpoints,
    resolve_relay_endpoint,
)

__all__ = [
    "RelayNotFound",
    "RelayReference",
    "list_relay_endpoints",
    "resolve_relay_endpoint",
]
