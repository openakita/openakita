"""L1 unit tests for the LiteLLM provider registry."""

from __future__ import annotations

import sys
import types

import pytest

from openakita.llm.registries.base import ModelInfo, ProviderRegistry
from openakita.llm.registries.litellm import LiteLLMRegistry

# ── ProviderInfo attributes ──


def test_info_slug():
    reg = LiteLLMRegistry()
    assert reg.info.slug == "litellm"


def test_info_name():
    reg = LiteLLMRegistry()
    assert reg.info.name == "LiteLLM"


def test_info_api_type_is_openai():
    reg = LiteLLMRegistry()
    assert reg.info.api_type == "openai"


def test_info_does_not_require_api_key():
    reg = LiteLLMRegistry()
    assert reg.info.requires_api_key is False


def test_info_supports_model_list():
    reg = LiteLLMRegistry()
    assert reg.info.supports_model_list is True


# ── Inheritance ──


def test_extends_provider_registry():
    assert issubclass(LiteLLMRegistry, ProviderRegistry)


# ── list_models with mocked litellm ──


@pytest.mark.asyncio
async def test_list_models_returns_models_from_litellm_model_cost():
    fake_litellm = types.ModuleType("litellm")
    fake_litellm.model_cost = {
        "openai/gpt-4o": {},
        "anthropic/claude-sonnet-4-20250514": {},
        "gpt-4o": {},  # no slash, should be filtered out
    }
    sys.modules["litellm"] = fake_litellm

    try:
        reg = LiteLLMRegistry()
        models = await reg.list_models(api_key="")

        model_ids = [m.id for m in models]
        assert "openai/gpt-4o" in model_ids
        assert "anthropic/claude-sonnet-4-20250514" in model_ids
        assert "gpt-4o" not in model_ids  # filtered: no slash
    finally:
        del sys.modules["litellm"]


@pytest.mark.asyncio
async def test_list_models_returns_only_model_info_instances():
    fake_litellm = types.ModuleType("litellm")
    fake_litellm.model_cost = {"openai/gpt-4o": {}}
    sys.modules["litellm"] = fake_litellm

    try:
        reg = LiteLLMRegistry()
        models = await reg.list_models(api_key="")

        for m in models:
            assert isinstance(m, ModelInfo)
    finally:
        del sys.modules["litellm"]


@pytest.mark.asyncio
async def test_list_models_deduplicates():
    fake_litellm = types.ModuleType("litellm")
    fake_litellm.model_cost = {
        "openai/gpt-4o": {},
    }
    sys.modules["litellm"] = fake_litellm

    try:
        reg = LiteLLMRegistry()
        models = await reg.list_models(api_key="")
        ids = [m.id for m in models]
        assert len(ids) == len(set(ids))
    finally:
        del sys.modules["litellm"]


# ── Fallback to preset models when litellm not installed ──


@pytest.mark.asyncio
async def test_list_models_falls_back_to_preset_when_import_fails():
    saved = sys.modules.pop("litellm", None)
    # Ensure import fails by injecting a broken module
    sys.modules["litellm"] = None  # type: ignore[assignment]

    try:
        reg = LiteLLMRegistry()
        models = await reg.list_models(api_key="")

        assert len(models) > 0
        model_ids = [m.id for m in models]
        assert "openai/gpt-4o" in model_ids
        assert "anthropic/claude-sonnet-4-20250514" in model_ids
    finally:
        del sys.modules["litellm"]
        if saved is not None:
            sys.modules["litellm"] = saved


@pytest.mark.asyncio
async def test_preset_models_all_have_slash():
    reg = LiteLLMRegistry()
    presets = reg._get_preset_models()
    for m in presets:
        assert "/" in m.id, f"preset model {m.id} missing provider/ prefix"


# ── Registration in __init__.py ──


def test_litellm_in_class_module_map():
    from openakita.llm.registries import _CLASS_MODULE_MAP

    assert "LiteLLMRegistry" in _CLASS_MODULE_MAP
    assert _CLASS_MODULE_MAP["LiteLLMRegistry"] == ".litellm"


def test_litellm_in_registry_by_slug():
    from openakita.llm.registries import REGISTRY_BY_SLUG

    assert "litellm" in REGISTRY_BY_SLUG
    assert isinstance(REGISTRY_BY_SLUG["litellm"], LiteLLMRegistry)
