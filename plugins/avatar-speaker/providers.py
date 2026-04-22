"""avatar-speaker — TTS routing built on top of ``contrib.tts``.

Phase 2-01 of the overhaul playbook moves the actual provider
implementations into ``openakita_plugin_sdk.contrib.tts`` so other
plugins (tts-studio, ppt-to-video, video-translator, dub-it, …) can
import the same providers without ``_load_sibling`` reaching into this
plugin's source tree.

What stays here:

- :func:`select_tts_provider` — preserves the legacy entry point name
  used by other parts of avatar-speaker / future digital-human work.
- :func:`select_avatar` — digital-human scaffold (still local because
  the avatar surface is not yet generalised in the SDK).
- :data:`PRESET_VOICES_ZH` — a back-compat alias of the SDK's
  :data:`VOICE_CATALOG` reshaped into the legacy ``[{id,label,provider}]``
  shape consumed by the existing UI.

What changed:

- **No more ``os.environ`` reads.** Credentials must be configured via
  :func:`configure_credentials` (the plugin layer wires this from
  ``_tm.get_config("dashscope_api_key")`` etc.).
- **No more sibling imports of provider modules.** All providers are
  the canonical SDK ones.
"""

from __future__ import annotations

import logging
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openakita_plugin_sdk.contrib.tts import (
    VOICE_CATALOG,
    BaseTTSProvider,
    CosyVoiceProvider,
    EdgeTTSProvider,
    OpenAITTSProvider,
    Qwen3TTSFlashProvider,
    TTSError,
)
from openakita_plugin_sdk.contrib.tts import (
    TTSResult as _SDKTTSResult,
)
from openakita_plugin_sdk.contrib.tts import (
    select_provider as _sdk_select_provider,
)

logger = logging.getLogger(__name__)


# ── back-compat dataclass ────────────────────────────────────────────────


@dataclass
class TTSResult:
    """Legacy TTSResult shape (kept for code that imports it from here)."""

    provider: str
    audio_path: Path
    duration_sec: float
    voice: str
    raw: dict[str, Any]


def _to_legacy_result(result: _SDKTTSResult) -> TTSResult:
    return TTSResult(
        provider=result.provider,
        audio_path=result.audio_path,
        duration_sec=result.duration_sec,
        voice=result.voice,
        raw=result.raw,
    )


# ── back-compat voice catalog ────────────────────────────────────────────


PRESET_VOICES_ZH: list[dict[str, str]] = [
    {"id": v.id, "label": v.label, "provider": v.provider}
    for v in VOICE_CATALOG
    if v.language.startswith("zh") or v.provider in ("openai",)
]


# ── credential registry (replaces the old ``from_env`` calls) ────────────

# Plugin code calls :func:`configure_credentials` whenever the user
# updates their API keys via ``POST /settings``. Each subsequent
# :func:`select_tts_provider` call uses the latest credentials.

_CREDENTIALS: dict[str, str | None] = {
    "dashscope_api_key": None,
    "openai_api_key": None,
}

# A few plugin UIs flip an explicit "preferred" knob, e.g. choosing the
# legacy ``cosyvoice`` instead of the new ``qwen3_tts_flash``.
_LEGACY_TO_PROVIDER_ID: dict[str, str] = {
    "auto": "auto",
    "edge": "edge",
    "dashscope": "qwen3_tts_flash",
    "qwen3": "qwen3_tts_flash",
    "qwen3_tts_flash": "qwen3_tts_flash",
    "cosyvoice": "cosyvoice",
    "openai": "openai",
    "stub": "stub",
}


def configure_credentials(
    *,
    dashscope_api_key: str | None = None,
    openai_api_key: str | None = None,
) -> None:
    """Hot-update credentials used by subsequent provider builds.

    Plugin code must call this once on load and every time the user
    edits the settings panel. Pass ``None`` to clear a key.
    """
    if dashscope_api_key is not None:
        _CREDENTIALS["dashscope_api_key"] = dashscope_api_key or None
    if openai_api_key is not None:
        _CREDENTIALS["openai_api_key"] = openai_api_key or None


def _build_configs() -> dict[str, dict[str, Any]]:
    """Per-provider config dict for the SDK registry."""
    dk = _CREDENTIALS.get("dashscope_api_key")
    ok = _CREDENTIALS.get("openai_api_key")
    return {
        Qwen3TTSFlashProvider.provider_id: {"api_key": dk} if dk else {},
        CosyVoiceProvider.provider_id: {"api_key": dk} if dk else {},
        OpenAITTSProvider.provider_id: {"api_key": ok} if ok else {},
        EdgeTTSProvider.provider_id: {},
    }


# ── stub provider (kept locally — pure stdlib silence wav) ───────────────


class StubLocalProvider:
    """Always-available silent-WAV provider, useful for dev/demo runs.

    Not part of contrib.tts — it has no external value beyond this plugin
    and adding it to the SDK registry would risk hiding misconfiguration.
    """

    name = "stub-silent"
    provider_id = "stub-silent"

    async def synthesize(
        self,
        *,
        text: str,
        voice: str = "stub",
        rate: str = "+0%",  # noqa: ARG002
        pitch: str = "+0Hz",  # noqa: ARG002
        output_dir: Path,
    ) -> TTSResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        out = output_dir / f"{uuid.uuid4().hex[:12]}.wav"
        sample_rate = 22050
        n_samples = int(sample_rate * max(1.0, min(10.0, len(text) / 4.0)))
        with wave.open(str(out), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(b"\x00\x00" * n_samples)
        return TTSResult(
            provider=self.name,
            audio_path=out,
            duration_sec=n_samples / sample_rate,
            voice=voice,
            raw={"note": "stub silent wav, no real TTS"},
        )

    async def cancel_task(self, task_id: str) -> bool:  # noqa: ARG002
        return False


# ── public selector (back-compat shim around contrib.tts) ────────────────


class _SDKProviderShim:
    """Adapt :class:`BaseTTSProvider` so its ``synthesize`` returns the
    legacy plugin-local :class:`TTSResult` shape (``provider`` / ``name``
    string fields, etc.). Avoids breaking other plugin internals that
    pattern-matched on the legacy result type.
    """

    def __init__(self, provider: BaseTTSProvider) -> None:
        self._inner = provider
        self.name = provider.provider_id
        self.provider_id = provider.provider_id

    async def synthesize(
        self,
        *,
        text: str,
        voice: str = "",
        rate: str = "+0%",
        pitch: str = "+0Hz",
        output_dir: Path,
        **kwargs: Any,
    ) -> TTSResult:
        result = await self._inner.synthesize(
            text=text, voice=voice, output_dir=output_dir,
            rate=rate, pitch=pitch, **kwargs,
        )
        return _to_legacy_result(result)

    async def cancel_task(self, task_id: str) -> bool:
        return await self._inner.cancel_task(task_id)


def select_tts_provider(preferred: str = "auto") -> Any:
    """Pick a TTS provider, preserving the legacy `select_tts_provider` API.

    Translates legacy ids (``"dashscope"`` → ``"qwen3_tts_flash"``) and
    raises the legacy :class:`VendorError`-compatible :class:`TTSError`
    so existing error handling paths keep working.
    """
    if preferred == "stub":
        return StubLocalProvider()
    pid = _LEGACY_TO_PROVIDER_ID.get(preferred, preferred)
    try:
        sdk_provider = _sdk_select_provider(
            pid, configs=_build_configs(), region="cn",
        )
    except TTSError:
        if preferred == "auto":
            return StubLocalProvider()
        raise
    return _SDKProviderShim(sdk_provider)


# ── digital-human (scaffold, unchanged) ──────────────────────────────────


class DigitalHumanAvatar:
    """Avatar scaffold — render(audio, portrait) → talking-head video.

    Kept here (not in SDK) because the avatar surface is still in flux
    and other plugins do not yet depend on it. When DashScope's
    ``wan2.2-s2v`` / ``videoretalk`` integration lands, it should still
    live in this plugin (it is the avatar plugin) but its interface
    should match this base class so downstream callers don't change.
    """

    name = "abstract-avatar"

    async def render(
        self,
        *,
        audio_path: Path,
        portrait_path: Path,
        output_dir: Path,
    ) -> Path:
        raise NotImplementedError(
            f"{type(self).__name__}.render() not implemented yet — "
            "数字人合成在 P3 backlog (wan2.2-s2v / videoretalk 集成)。"
            "目前只支持音频生成，请先关闭【数字人形象】。",
        )


class StubAvatar(DigitalHumanAvatar):
    """No-op avatar — writes a plain-text description as the artefact."""

    name = "stub-avatar"

    async def render(
        self,
        *,
        audio_path: Path,
        portrait_path: Path,
        output_dir: Path,
    ) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        out = output_dir / f"{uuid.uuid4().hex[:12]}.txt"
        out.write_text(
            f"Stub avatar render\naudio: {audio_path}\nportrait: {portrait_path}\n"
            "(实际数字人合成在 P3 实现 - wan2.2-s2v / videoretalk)",
            encoding="utf-8",
        )
        return out


def select_avatar(preferred: str = "stub") -> DigitalHumanAvatar | None:
    if preferred in ("none", "off", ""):
        return None
    if preferred == "stub":
        return StubAvatar()
    return DigitalHumanAvatar()


__all__ = [
    "PRESET_VOICES_ZH",
    "DigitalHumanAvatar",
    "StubAvatar",
    "StubLocalProvider",
    "TTSError",
    "TTSResult",
    "configure_credentials",
    "select_avatar",
    "select_tts_provider",
]
