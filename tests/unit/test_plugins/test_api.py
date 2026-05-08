"""Tests for openakita.plugins.api — PluginAPI and permission enforcement."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openakita.plugins.api import PluginAPI
from openakita.plugins.hooks import HookRegistry
from openakita.plugins.manifest import BASIC_PERMISSIONS, PluginManifest
from openakita.plugins.sandbox import PluginErrorTracker


def _make_manifest(**overrides) -> PluginManifest:
    defaults = {
        "id": "test-plugin",
        "name": "Test Plugin",
        "version": "1.0.0",
        "plugin_type": "python",
        "permissions": list(BASIC_PERMISSIONS),
    }
    defaults.update(overrides)
    return PluginManifest(**defaults)


def _make_api(
    tmp_path: Path,
    *,
    granted: list[str] | None = None,
    host_refs: dict | None = None,
    hook_registry: HookRegistry | None = None,
    manifest_overrides: dict | None = None,
) -> PluginAPI:
    manifest = _make_manifest(**(manifest_overrides or {}))
    return PluginAPI(
        plugin_id=manifest.id,
        manifest=manifest,
        granted_permissions=granted if granted is not None else list(BASIC_PERMISSIONS),
        data_dir=tmp_path,
        host_refs=host_refs,
        hook_registry=hook_registry,
    )


# ---------- Logging (no permission check) ----------


class TestLogging:
    def test_log_info(self, tmp_path):
        api = _make_api(tmp_path)
        api.log("hello")

    def test_log_error(self, tmp_path):
        api = _make_api(tmp_path)
        api.log_error("something went wrong", RuntimeError("err"))

    def test_log_debug(self, tmp_path):
        api = _make_api(tmp_path)
        api.log_debug("debug info")


# ---------- Config ----------


class TestConfig:
    def test_get_config_empty(self, tmp_path):
        api = _make_api(tmp_path)
        assert api.get_config() == {}

    def test_set_config_creates_file(self, tmp_path):
        api = _make_api(tmp_path)
        api.set_config({"key": "value"})
        config_path = tmp_path / "config.json"
        assert config_path.exists()
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["key"] == "value"

    def test_set_config_merges(self, tmp_path):
        api = _make_api(tmp_path)
        api.set_config({"a": 1})
        api.set_config({"b": 2})
        result = api.get_config()
        assert result == {"a": 1, "b": 2}

    def test_get_config_reads_back(self, tmp_path):
        api = _make_api(tmp_path)
        api.set_config({"foo": "bar"})
        assert api.get_config()["foo"] == "bar"


# ---------- Tool registration ----------


class TestRegisterTools:
    def test_register_tools_calls_registry(self, tmp_path):
        mock_registry = MagicMock()
        api = _make_api(tmp_path, host_refs={"tool_registry": mock_registry})
        defs = [{"name": "my_tool"}]

        def handler():
            return None

        api.register_tools(defs, handler)
        mock_registry.register.assert_called_once()
        assert "my_tool" in api._registered_tools

    def test_register_tools_no_registry_warns(self, tmp_path):
        api = _make_api(tmp_path, host_refs={})
        api.register_tools([{"name": "t"}], lambda: None)

    def test_register_tools_basic_permission_always_allowed(self, tmp_path):
        api = _make_api(tmp_path, granted=list(BASIC_PERMISSIONS))
        api.register_tools([{"name": "t"}], lambda: None)


# ---------- Hook registration ----------


class TestRegisterHook:
    def test_basic_hook_works_with_hooks_basic(self, tmp_path):
        tracker = PluginErrorTracker()
        hr = HookRegistry(error_tracker=tracker)
        api = _make_api(tmp_path, granted=list(BASIC_PERMISSIONS), hook_registry=hr)
        api.register_hook("on_init", lambda: None)
        assert len(hr.get_hooks("on_init")) == 1

    def test_message_hook_requires_hooks_message(self, tmp_path):
        tracker = PluginErrorTracker()
        hr = HookRegistry(error_tracker=tracker)
        api = _make_api(tmp_path, granted=list(BASIC_PERMISSIONS), hook_registry=hr)
        api.register_hook("on_message_received", lambda: None)
        assert len(hr.get_hooks("on_message_received")) == 0, (
            "Hook should be silently skipped when permission not granted"
        )
        assert "hooks.message" in api._pending_permissions

    def test_message_hook_works_when_granted(self, tmp_path):
        tracker = PluginErrorTracker()
        hr = HookRegistry(error_tracker=tracker)
        granted = list(BASIC_PERMISSIONS) + ["hooks.message"]
        api = _make_api(tmp_path, granted=granted, hook_registry=hr)
        api.register_hook("on_message_received", lambda: None)
        assert len(hr.get_hooks("on_message_received")) == 1

    def test_shutdown_hook_is_basic(self, tmp_path):
        tracker = PluginErrorTracker()
        hr = HookRegistry(error_tracker=tracker)
        api = _make_api(tmp_path, granted=list(BASIC_PERMISSIONS), hook_registry=hr)
        api.register_hook("on_shutdown", lambda: None)
        assert len(hr.get_hooks("on_shutdown")) == 1


# ---------- Channel registration ----------


class TestRegisterChannel:
    def test_requires_channel_register_permission(self, tmp_path):
        api = _make_api(tmp_path, granted=list(BASIC_PERMISSIONS))
        api.register_channel("slack", lambda: None)
        assert "slack" not in api._registered_channels, (
            "Channel should be silently skipped when permission not granted"
        )
        assert "channel.register" in api._pending_permissions

    def test_works_when_granted(self, tmp_path):
        mock_registry = MagicMock()
        granted = list(BASIC_PERMISSIONS) + ["channel.register"]
        api = _make_api(tmp_path, granted=granted, host_refs={"channel_registry": mock_registry})
        def factory():
            return None

        api.register_channel("slack", factory)
        mock_registry.assert_called_once_with("slack", factory)
        assert "slack" in api._registered_channels


# ---------- LLM provider ----------


class TestRegisterLlmProvider:
    def test_requires_llm_register_permission(self, tmp_path):
        api = _make_api(tmp_path, granted=list(BASIC_PERMISSIONS))
        api.register_llm_provider("custom", type)
        from openakita.plugins import PLUGIN_PROVIDER_MAP
        assert "custom" not in PLUGIN_PROVIDER_MAP, (
            "LLM provider should be silently skipped when permission not granted"
        )
        assert "llm.register" in api._pending_permissions

    def test_works_when_granted(self, tmp_path):
        granted = list(BASIC_PERMISSIONS) + ["llm.register"]
        api = _make_api(tmp_path, granted=granted)

        class FakeProvider:
            pass

        api.register_llm_provider("custom-api", FakeProvider)
        from openakita.plugins import PLUGIN_PROVIDER_MAP

        assert "custom-api" in PLUGIN_PROVIDER_MAP
        del PLUGIN_PROVIDER_MAP["custom-api"]


# ---------- Brain access ----------


class TestGetBrain:
    def test_requires_brain_access(self, tmp_path):
        api = _make_api(tmp_path, granted=list(BASIC_PERMISSIONS))
        result = api.get_brain()
        assert result is None, "get_brain should return None when permission not granted"
        assert "brain.access" in api._pending_permissions

    def test_returns_brain_when_granted(self, tmp_path):
        fake_brain = MagicMock()
        granted = list(BASIC_PERMISSIONS) + ["brain.access"]
        api = _make_api(tmp_path, granted=granted, host_refs={"brain": fake_brain})
        assert api.get_brain() is fake_brain


# ---------- UI events ----------


class TestBroadcastUiEvent:
    @pytest.mark.asyncio
    async def test_uses_websocket_broadcast_not_im_gateway(self, tmp_path):
        mock_gateway = MagicMock()
        mock_gateway.broadcast = AsyncMock()
        api = _make_api(tmp_path, host_refs={"gateway": mock_gateway})

        with patch(
            "openakita.api.routes.websocket.broadcast_event",
            new_callable=AsyncMock,
        ) as mock_broadcast_event:
            api.broadcast_ui_event("task_update", {"task_id": "t1", "status": "succeeded"})
            await asyncio.sleep(0)

        mock_broadcast_event.assert_awaited_once_with(
            "plugin:test-plugin:task_update",
            {"task_id": "t1", "status": "succeeded"},
        )
        mock_gateway.broadcast.assert_not_called()


# ---------- Permission check ----------


class TestPermissionCheck:
    def test_basic_permission_always_passes(self, tmp_path):
        api = _make_api(tmp_path, granted=[])
        for perm in BASIC_PERMISSIONS:
            api._check_permission(perm)

    def test_advanced_permission_fails_if_not_granted(self, tmp_path):
        api = _make_api(tmp_path, granted=list(BASIC_PERMISSIONS))
        result = api._check_permission("brain.access")
        assert result is False, "_check_permission should return False when not granted"
        assert "brain.access" in api._pending_permissions

    def test_advanced_permission_passes_when_granted(self, tmp_path):
        api = _make_api(tmp_path, granted=["brain.access"])
        api._check_permission("brain.access")


# ---------- Cleanup ----------


class TestCleanup:
    def test_cleanup_unregisters_hooks(self, tmp_path):
        tracker = PluginErrorTracker()
        hr = HookRegistry(error_tracker=tracker)
        api = _make_api(tmp_path, granted=list(BASIC_PERMISSIONS), hook_registry=hr)
        api.register_hook("on_init", lambda: None)
        assert len(hr.get_hooks("on_init")) == 1
        api._cleanup()
        assert len(hr.get_hooks("on_init")) == 0

    def test_cleanup_unregisters_tools(self, tmp_path):
        mock_registry = MagicMock()
        api = _make_api(tmp_path, host_refs={"tool_registry": mock_registry})
        api.register_tools([{"name": "t1"}], lambda: None)
        api._cleanup()
        mock_registry.unregister.assert_called_once_with("plugin_test-plugin")

    def test_cleanup_without_hooks_or_tools(self, tmp_path):
        api = _make_api(tmp_path)
        api._cleanup()

    def test_cleanup_survives_broken_host_refs(self, tmp_path):
        """_cleanup must not crash even if host_refs point to broken objects."""
        mock_registry = MagicMock()
        mock_registry.unregister.side_effect = RuntimeError("registry exploded")
        api = _make_api(tmp_path, host_refs={"tool_registry": mock_registry})
        api.register_tools([{"name": "t1"}], lambda: None)
        api._cleanup()

    def test_cleanup_survives_double_call(self, tmp_path):
        """Calling _cleanup twice must not raise."""
        tracker = PluginErrorTracker()
        hr = HookRegistry(error_tracker=tracker)
        api = _make_api(tmp_path, granted=list(BASIC_PERMISSIONS), hook_registry=hr)
        api.register_hook("on_init", lambda: None)
        api._cleanup()
        api._cleanup()


# ---------- Edge cases: invalid inputs ----------


class TestEdgeCases:
    def test_register_tools_empty_name_skipped(self, tmp_path):
        """Tool defs without a name field should be silently skipped."""
        mock_registry = MagicMock()
        api = _make_api(tmp_path, host_refs={"tool_registry": mock_registry})
        api.register_tools([{"description": "no name"}], lambda: None)
        mock_registry.register.assert_not_called()

    def test_register_tools_mixed_valid_invalid(self, tmp_path):
        """Only valid defs should be registered, invalid ones skipped."""
        mock_registry = MagicMock()
        tool_defs: list[dict] = []
        api = _make_api(
            tmp_path,
            host_refs={"tool_registry": mock_registry, "tool_definitions": tool_defs},
        )
        api.register_tools(
            [{"name": "good"}, {"description": "bad"}, {"name": "also_good"}],
            lambda: None,
        )
        mock_registry.register.assert_called_once()
        assert len(tool_defs) == 2

    def test_register_hook_non_callable_rejected(self, tmp_path):
        """Passing a non-callable to register_hook must not crash."""
        tracker = PluginErrorTracker()
        hr = HookRegistry(error_tracker=tracker)
        api = _make_api(tmp_path, granted=list(BASIC_PERMISSIONS), hook_registry=hr)
        api.register_hook("on_init", "not_a_function")  # type: ignore[arg-type]
        assert len(hr.get_hooks("on_init")) == 0

    def test_register_agent_lifecycle_alias_hooks_with_message_permission(self, tmp_path):
        """OpenClaw-style aliases use the same permission tier as message hooks."""
        hr = HookRegistry()
        api = _make_api(tmp_path, granted=["hooks.message"], hook_registry=hr)
        api.register_hook("before_agent_start", lambda **kw: None)
        api.register_hook("agent_end", lambda **kw: None)
        assert len(hr.get_hooks("before_agent_start")) == 1
        assert len(hr.get_hooks("agent_end")) == 1

    def test_register_llm_provider_rejects_non_class(self, tmp_path):
        """Passing an instance instead of a class must not crash."""
        api = _make_api(tmp_path, granted=["llm.register"])
        api.register_llm_provider("test_type", "not_a_class")  # type: ignore[arg-type]
        from openakita.plugins import PLUGIN_PROVIDER_MAP
        assert "test_type" not in PLUGIN_PROVIDER_MAP

    def test_register_channel_empty_name_rejected(self, tmp_path):
        """Empty type_name should be rejected."""
        api = _make_api(tmp_path, granted=["channel.register"])
        api.register_channel("", lambda: None)

    def test_register_retrieval_source_none_rejected(self, tmp_path):
        """Passing None as source should not crash."""
        api = _make_api(tmp_path, granted=["retrieval.register"])
        api.register_retrieval_source(None)  # type: ignore[arg-type]

    def test_register_tools_catalog_error_contained(self, tmp_path):
        """If add_tool raises, the error is logged but does not propagate."""
        mock_registry = MagicMock()
        mock_catalog = MagicMock()
        mock_catalog.add_tool.side_effect = KeyError("name")
        api = _make_api(
            tmp_path,
            host_refs={"tool_registry": mock_registry, "tool_catalog": mock_catalog},
        )
        api.register_tools([{"name": "t1"}], lambda: None)
        assert "t1" in api._registered_tools

