"""Smoke tests for happyhorse_model_registry.py."""

from __future__ import annotations

from happyhorse_model_registry import (
    ALL_MODES,
    REGISTRY,
    RegistryPayload,
    by_model_id,
    default_model,
    lookup,
    models_for,
)


def test_every_mode_has_at_least_one_default():
    for mode in ALL_MODES:
        entry = default_model(mode)
        assert entry is not None, f"mode {mode} has no default registry entry"
        assert entry.is_default is True


def test_happyhorse_t2v_default_is_happyhorse_1_0():
    entry = default_model("t2v")
    assert entry.model_id == "happyhorse-1.0-t2v"


def test_happyhorse_i2v_default_is_happyhorse_1_0():
    entry = default_model("i2v")
    assert entry.model_id == "happyhorse-1.0-i2v"


def test_happyhorse_r2v_default_is_happyhorse_1_0():
    entry = default_model("r2v")
    assert entry.model_id == "happyhorse-1.0-r2v"


def test_models_for_returns_only_that_mode():
    entries = models_for("t2v")
    assert all(e.mode == "t2v" for e in entries)
    assert len(entries) >= 1


def test_lookup_unknown_returns_none():
    assert lookup("t2v", "definitely-not-a-model") is None


def test_by_model_id_finds_happyhorse_models():
    """``by_model_id`` is mode-agnostic: it returns *some* canonical
    entry whose model_id matches; mode/endpoint/protocol fields are
    invariant across modes for the same model_id by construction."""
    entry = by_model_id("happyhorse-1.0-i2v")
    assert entry is not None
    assert entry.model_id == "happyhorse-1.0-i2v"
    assert entry.endpoint_family == "video_synthesis"


def test_by_model_id_returns_none_for_unknown():
    assert by_model_id("definitely-not-a-model") is None


def test_registry_payload_build_is_serializable():
    payload = RegistryPayload.build()
    assert isinstance(payload.models, list)
    assert isinstance(payload.defaults, dict)
    assert all("model_id" in m for m in payload.models)
    assert all("mode" in m for m in payload.models)


def test_native_audio_sync_only_on_happyhorse_video_modes():
    """HappyHorse 1.0 family is the only one with native audio sync."""
    for entry in REGISTRY:
        if entry.native_audio_sync:
            assert entry.model_id.startswith("happyhorse-1.0-"), (
                f"unexpected native_audio_sync on {entry.model_id}"
            )
