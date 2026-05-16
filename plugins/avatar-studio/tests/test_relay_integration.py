"""avatar-studio — shared relay-station integration.

Mirrors plugins/happyhorse-video/tests/test_relay_integration.py so
the two plugins stay in lockstep. Both use the same
openakita.relay.apply_relay_override helper underneath but each
plugin has its own VendorError + ERROR_KIND_CLIENT, so the strict-
policy raise contract has to be verified per plugin.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from avatar_dashscope_client import (
    AvatarDashScopeClient,
    DASHSCOPE_BASE_URL_BJ,
    make_default_settings,
)
from avatar_studio_inline.vendor_client import VendorError


def _read_settings_factory(**overrides):
    def _read():
        s = make_default_settings()
        s.update(overrides)
        return s
    return _read


# ─── Fake openakita.relay module so tests do not depend on PYTHONPATH ──


class _StubRelayResolutionError(Exception):
    def __init__(self, message, *, user_message=None):
        super().__init__(message)
        self.user_message = user_message or message


def _install_stub_relay_module(monkeypatch, *, refs_by_name=None, raises=None):
    fake_pkg = SimpleNamespace()

    def fake_apply(settings, *, default_base_url="", required_capability="", plugin_name=""):
        out = dict(settings)
        relay_name = str(out.pop("relay_endpoint", "") or "").strip()
        policy = str(out.pop("relay_fallback_policy", "official") or "official")
        if not relay_name:
            return out
        if raises is not None:
            raise raises
        ref = (refs_by_name or {}).get(relay_name)
        if ref is None:
            if policy == "strict":
                raise _StubRelayResolutionError(
                    f"{relay_name} not found",
                    user_message=f"中转站 {relay_name!r} 未找到",
                )
            return out
        out["base_url"] = ref.base_url
        if ref.api_key:
            out["api_key"] = ref.api_key
        out["_relay_reference"] = ref
        return out

    fake_pkg.apply_relay_override = fake_apply
    fake_pkg.SettingsRelayResolutionError = _StubRelayResolutionError
    monkeypatch.setitem(sys.modules, "openakita", SimpleNamespace(relay=fake_pkg))
    monkeypatch.setitem(sys.modules, "openakita.relay", fake_pkg)


def _make_ref(base_url="https://relay.example.com/v1", api_key="sk-relay"):
    return SimpleNamespace(
        name="yunwu-video",
        base_url=base_url,
        api_key=api_key,
        capabilities=["video"],
        supported_models=[],
        models_synced_at=None,
        note=None,
        extra={},
    )


# ─── Tests ──────────────────────────────────────────────────────────


def test_no_relay_keeps_per_plugin_settings(monkeypatch):
    _install_stub_relay_module(monkeypatch, refs_by_name={})
    c = AvatarDashScopeClient(
        _read_settings_factory(api_key="sk-direct", base_url="https://dashscope.aliyuncs.com")
    )
    assert c.base_url == "https://dashscope.aliyuncs.com"


def test_relay_overrides_base_url_and_api_key(monkeypatch):
    ref = _make_ref(base_url="https://yunwu.example.com/v1", api_key="sk-yunwu")
    _install_stub_relay_module(monkeypatch, refs_by_name={"yunwu-video": ref})
    c = AvatarDashScopeClient(
        _read_settings_factory(
            api_key="sk-direct",
            base_url="https://dashscope.aliyuncs.com",
            relay_endpoint="yunwu-video",
        )
    )
    s = c._settings()
    assert s["api_key"] == "sk-yunwu"
    assert s["base_url"] == "https://yunwu.example.com/v1"
    assert c.base_url == "https://yunwu.example.com/v1"
    assert s["_relay_reference"] is ref


def test_missing_relay_with_strict_policy_raises_vendorerror(monkeypatch):
    _install_stub_relay_module(monkeypatch, refs_by_name={})
    with pytest.raises(VendorError) as ei:
        AvatarDashScopeClient(
            _read_settings_factory(
                relay_endpoint="ghost",
                relay_fallback_policy="strict",
            )
        )
    assert "ghost" in str(ei.value)
    assert ei.value.retryable is False


def test_missing_relay_with_official_policy_falls_back(monkeypatch):
    _install_stub_relay_module(monkeypatch, refs_by_name={})
    c = AvatarDashScopeClient(
        _read_settings_factory(
            api_key="sk-direct",
            base_url="https://dashscope.aliyuncs.com",
            relay_endpoint="ghost",
        )
    )
    assert c.base_url == "https://dashscope.aliyuncs.com"


def test_openakita_relay_import_failure_falls_back(monkeypatch):
    monkeypatch.setitem(sys.modules, "openakita.relay", None)
    c = AvatarDashScopeClient(
        _read_settings_factory(
            api_key="sk-direct",
            base_url="https://dashscope.aliyuncs.com",
            relay_endpoint="ghost",
        )
    )
    assert c.base_url == "https://dashscope.aliyuncs.com"


def test_default_settings_carry_relay_keys():
    d = make_default_settings()
    assert d["relay_endpoint"] == ""
    assert d["relay_fallback_policy"] == "official"
