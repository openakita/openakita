"""Tests for avatar_model_registry — coverage of the 5-mode x 3-backend matrix."""

from __future__ import annotations

from avatar_model_registry import (
    ALL_BACKENDS,
    ALL_MODES,
    REGISTRY,
    ModelEntry,
    default_model,
    models_for,
)


def test_registry_covers_all_mode_backend_pairs() -> None:
    for mode in ALL_MODES:
        for backend in ALL_BACKENDS:
            candidates = models_for(mode, backend)
            assert len(candidates) >= 1, f"no entry for {mode}/{backend}"


def test_dashscope_entries_have_model_id() -> None:
    for entry in REGISTRY:
        if entry.backend == "dashscope":
            assert entry.model_id, f"{entry.mode}/dashscope missing model_id"


def test_rh_and_local_entries_have_empty_model_id() -> None:
    for entry in REGISTRY:
        if entry.backend in ("runninghub", "comfyui_local"):
            assert entry.model_id == "", (
                f"{entry.mode}/{entry.backend} should have empty model_id"
            )


def test_each_dashscope_mode_has_exactly_one_default() -> None:
    for mode in ALL_MODES:
        ds = models_for(mode, "dashscope")
        defaults = [e for e in ds if e.is_default]
        assert len(defaults) == 1, f"{mode}/dashscope has {len(defaults)} defaults"


def test_default_model_returns_entry() -> None:
    for mode in ALL_MODES:
        entry = default_model(mode, "dashscope")
        assert entry is not None
        assert isinstance(entry, ModelEntry)
        assert entry.is_default is True


def test_default_model_fallback_for_rh() -> None:
    for mode in ALL_MODES:
        entry = default_model(mode, "runninghub")
        assert entry is not None
        assert entry.backend == "runninghub"


def test_to_dict_round_trip() -> None:
    entry = REGISTRY[0]
    d = entry.to_dict()
    assert d["mode"] == entry.mode
    assert d["backend"] == entry.backend
    assert d["model_id"] == entry.model_id
    assert isinstance(d["is_default"], bool)
    assert isinstance(d["requires_oss"], bool)


def test_dashscope_entries_require_oss() -> None:
    for entry in REGISTRY:
        if entry.backend == "dashscope":
            assert entry.requires_oss is True
        else:
            assert entry.requires_oss is False
