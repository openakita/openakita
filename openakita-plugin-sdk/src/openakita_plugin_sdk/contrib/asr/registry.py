"""Provider registry / selection for contrib.asr."""

from __future__ import annotations

from typing import Any

from .base import ASRError, BaseASRProvider
from .dashscope_paraformer import DashScopeParaformerProvider
from .stub import StubASRProvider
from .whisper_local import WhisperLocalProvider

_PROVIDER_CLASSES: dict[str, type[BaseASRProvider]] = {
    DashScopeParaformerProvider.provider_id: DashScopeParaformerProvider,
    WhisperLocalProvider.provider_id: WhisperLocalProvider,
    StubASRProvider.provider_id: StubASRProvider,
}


PROVIDER_PRIORITY_CHINA: tuple[str, ...] = (
    "dashscope_paraformer",
    "whisper_local",
    "stub",
)

PROVIDER_PRIORITY_GLOBAL: tuple[str, ...] = (
    "whisper_local",
    "dashscope_paraformer",
    "stub",
)


def list_provider_ids() -> tuple[str, ...]:
    return tuple(_PROVIDER_CLASSES.keys())


def build_provider(provider_id: str, config: dict[str, Any] | None = None) -> BaseASRProvider:
    cls = _PROVIDER_CLASSES.get(provider_id)
    if cls is None:
        raise ASRError(
            f"Unknown ASR provider id: {provider_id!r}",
            retryable=False,
            provider=provider_id,
            kind="config",
        )
    return cls(config or {})


def available_providers(
    configs: dict[str, dict[str, Any]] | None = None,
) -> list[BaseASRProvider]:
    configs = configs or {}
    out: list[BaseASRProvider] = []
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
    allow_stub: bool = False,
) -> BaseASRProvider:
    configs = configs or {}
    if preferred and preferred != "auto":
        provider = build_provider(preferred, configs.get(preferred, {}))
        if not provider.is_available():
            raise ASRError(
                f"ASR provider {preferred!r} is not available "
                "(missing api_key, optional dependency, or disabled).",
                retryable=False,
                provider=preferred,
                kind="config",
            )
        return provider

    order = PROVIDER_PRIORITY_CHINA if region == "cn" else PROVIDER_PRIORITY_GLOBAL
    for pid in order:
        if pid == "stub" and not allow_stub:
            continue
        provider = build_provider(pid, configs.get(pid, {}))
        if provider.is_available():
            return provider
    if allow_stub:
        return StubASRProvider({})
    raise ASRError(
        "No ASR provider is available. Configure DashScope API key or "
        "install whisper.cpp.",
        retryable=False,
        provider="auto",
        kind="config",
    )
