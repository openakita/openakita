"""Provider registry / selection logic for contrib.tts.

A plugin builds providers by handing the registry a config map keyed by
``provider_id``::

    configs = {
        "qwen3_tts_flash": {"api_key": dashscope_key},
        "cosyvoice":       {"api_key": dashscope_key},
        "openai":          {"api_key": openai_key},
        "edge":            {},
    }
    provider = select_provider("auto", configs=configs, region="cn")

The registry never reaches into ``os.environ`` itself. Everything is
explicitly passed in by the caller — this keeps the SDK side-effect free
and makes test isolation trivial.
"""

from __future__ import annotations

from typing import Any

from .base import BaseTTSProvider, TTSError
from .dashscope import CosyVoiceProvider, Qwen3TTSFlashProvider
from .edge import EdgeTTSProvider
from .openai import OpenAITTSProvider

_PROVIDER_CLASSES: dict[str, type[BaseTTSProvider]] = {
    Qwen3TTSFlashProvider.provider_id: Qwen3TTSFlashProvider,
    CosyVoiceProvider.provider_id: CosyVoiceProvider,
    OpenAITTSProvider.provider_id: OpenAITTSProvider,
    EdgeTTSProvider.provider_id: EdgeTTSProvider,
}


PROVIDER_PRIORITY_CHINA: tuple[str, ...] = (
    "qwen3_tts_flash",
    "cosyvoice",
    "openai",
    "edge",
)
"""Default ordering for China-region deployments — Bailian first."""

PROVIDER_PRIORITY_GLOBAL: tuple[str, ...] = (
    "openai",
    "qwen3_tts_flash",
    "cosyvoice",
    "edge",
)
"""Default ordering for non-China deployments — OpenAI first."""


def list_provider_ids() -> tuple[str, ...]:
    """Stable list of all known provider ids."""
    return tuple(_PROVIDER_CLASSES.keys())


def build_provider(provider_id: str, config: dict[str, Any] | None = None) -> BaseTTSProvider:
    """Instantiate a provider by id with the given config."""
    cls = _PROVIDER_CLASSES.get(provider_id)
    if cls is None:
        raise TTSError(
            f"Unknown TTS provider id: {provider_id!r}",
            retryable=False,
            provider=provider_id,
            kind="config",
        )
    return cls(config or {})


def available_providers(
    configs: dict[str, dict[str, Any]] | None = None,
) -> list[BaseTTSProvider]:
    """Build every provider whose config makes it ``is_available()``.

    Providers without an entry in ``configs`` are still attempted with
    an empty config — useful for ``edge`` which needs no config.
    """
    configs = configs or {}
    out: list[BaseTTSProvider] = []
    for pid, cls in _PROVIDER_CLASSES.items():
        cfg = configs.get(pid, {})
        provider = cls(cfg)
        if provider.is_available():
            out.append(provider)
    return out


def select_provider(
    preferred: str = "auto",
    *,
    configs: dict[str, dict[str, Any]] | None = None,
    region: str = "cn",
) -> BaseTTSProvider:
    """Pick the best available provider.

    - ``preferred="auto"`` walks the regional priority list and returns
      the first ``is_available()`` provider.
    - Any other value selects that exact provider, raising ``TTSError``
      if not available.
    """
    configs = configs or {}
    if preferred and preferred != "auto":
        provider = build_provider(preferred, configs.get(preferred, {}))
        if not provider.is_available():
            raise TTSError(
                f"TTS provider {preferred!r} is not available "
                "(missing api_key, optional dependency, or disabled).",
                retryable=False,
                provider=preferred,
                kind="config",
            )
        return provider

    order = PROVIDER_PRIORITY_CHINA if region == "cn" else PROVIDER_PRIORITY_GLOBAL
    for pid in order:
        provider = build_provider(pid, configs.get(pid, {}))
        if provider.is_available():
            return provider
    raise TTSError(
        "No TTS provider is available. Configure an API key for at least one "
        "of: qwen3_tts_flash / cosyvoice / openai, or install edge-tts.",
        retryable=False,
        provider="auto",
        kind="config",
    )
