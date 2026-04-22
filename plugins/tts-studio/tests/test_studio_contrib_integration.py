"""Phase 2-02 regression tests: studio_engine on top of contrib.tts.

Goals:
  * Confirm ``_load_sibling`` is gone (no avatar-speaker import).
  * Confirm ``select_tts_provider`` returns providers from
    ``openakita_plugin_sdk.contrib.tts`` (via the shim) and that legacy
    aliases like ``"dashscope"`` still work.
  * Confirm ``configure_credentials`` updates state across providers.
  * Confirm ``PRESET_VOICES_ZH`` is non-empty and well-shaped.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

import studio_engine  # noqa: E402
from openakita_plugin_sdk.contrib.tts import (  # noqa: E402
    BaseTTSProvider,
    EdgeTTSProvider,
    Qwen3TTSFlashProvider,
)


def test_no_load_sibling_in_engine_source() -> None:
    """Phase 2-02 success criterion: the cross-plugin shim is gone."""
    src = Path(studio_engine.__file__).read_text(encoding="utf-8")
    assert "def _load_sibling" not in src
    assert "_load_sibling(" not in src
    assert "avatar-speaker/providers" not in src
    assert "_oa_avatar_providers" not in src


def test_preset_voices_shape() -> None:
    assert studio_engine.PRESET_VOICES_ZH, "voice catalog should not be empty"
    sample = studio_engine.PRESET_VOICES_ZH[0]
    assert {"id", "label", "provider"}.issubset(sample.keys())


def test_legacy_alias_dashscope_resolves_to_qwen3() -> None:
    studio_engine.configure_credentials(dashscope_api_key="sk-test-1234")
    prov = studio_engine.select_tts_provider("dashscope")
    assert prov.provider_id == Qwen3TTSFlashProvider.provider_id


def _edge_tts_installed() -> bool:
    try:
        import edge_tts  # noqa: F401

        return True
    except ImportError:
        return False


@pytest.mark.skipif(
    not _edge_tts_installed(),
    reason="edge-tts pip package not installed in this env",
)
def test_auto_falls_back_to_edge_when_no_credentials() -> None:
    studio_engine.configure_credentials(dashscope_api_key="", openai_api_key="")
    prov = studio_engine.select_tts_provider("auto")
    assert prov.provider_id == EdgeTTSProvider.provider_id


@pytest.mark.skipif(
    not _edge_tts_installed(),
    reason="edge-tts pip package not installed in this env",
)
def test_select_tts_provider_returns_shim_with_legacy_signature() -> None:
    studio_engine.configure_credentials(dashscope_api_key="", openai_api_key="")
    prov = studio_engine.select_tts_provider("edge")
    assert hasattr(prov, "synthesize")
    assert hasattr(prov, "provider_id")
    assert isinstance(prov._inner, BaseTTSProvider)


def test_auto_with_dashscope_key_picks_qwen3() -> None:
    studio_engine.configure_credentials(
        dashscope_api_key="sk-test-dashscope", openai_api_key=""
    )
    prov = studio_engine.select_tts_provider("auto")
    assert prov.provider_id == Qwen3TTSFlashProvider.provider_id


def test_auto_with_no_creds_and_no_edge_raises_ttserror() -> None:
    """When neither cred nor edge-tts is available, raise TTSError so
    the plugin layer can surface a clear error to the user instead of
    silently producing garbage audio."""
    if _edge_tts_installed():
        pytest.skip("edge-tts is installed → auto returns edge instead")
    from openakita_plugin_sdk.contrib.tts import TTSError

    studio_engine.configure_credentials(dashscope_api_key="", openai_api_key="")
    with pytest.raises(TTSError):
        studio_engine.select_tts_provider("auto")


def test_configure_credentials_is_idempotent_and_partial() -> None:
    studio_engine.configure_credentials(dashscope_api_key="key-A")
    assert studio_engine._CREDENTIALS["dashscope_api_key"] == "key-A"
    studio_engine.configure_credentials(openai_api_key="key-B")
    assert studio_engine._CREDENTIALS["dashscope_api_key"] == "key-A"
    assert studio_engine._CREDENTIALS["openai_api_key"] == "key-B"
    studio_engine.configure_credentials(dashscope_api_key="")
    assert studio_engine._CREDENTIALS["dashscope_api_key"] is None


def test_unknown_provider_alias_passes_through() -> None:
    from openakita_plugin_sdk.contrib.tts import TTSError

    with pytest.raises(TTSError):
        studio_engine.select_tts_provider("does-not-exist")
