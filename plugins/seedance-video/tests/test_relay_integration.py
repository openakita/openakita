"""seedance-video — _resolve_effective_ark_endpoint relay integration.

Same pattern as tongyi-image / avatar-studio: when ``ark_relay_endpoint``
names a relay in OpenAkita's shared registry, the resolver overlays
its base_url + api_key on the Ark fields. Strict policy raises
HTTPException(400) carrying the Chinese user_message so the Settings
UI banner has actionable text; official policy warns and falls back.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from _plugin_loader import load_seedance_plugin

_plugin = load_seedance_plugin()
Plugin = _plugin.Plugin


def _bare_plugin():
    return object.__new__(Plugin)


class _StubRelayResolutionError(Exception):
    def __init__(self, message, *, user_message=None):
        super().__init__(message)
        self.user_message = user_message or message


def _install_stub_relay_module(monkeypatch, *, refs_by_name=None):
    fake_pkg = SimpleNamespace()

    def fake_apply(settings, *, default_base_url="", required_capability="", plugin_name=""):
        out = dict(settings)
        relay_name = str(out.pop("relay_endpoint", "") or "").strip()
        policy = str(out.pop("relay_fallback_policy", "official") or "official")
        if not relay_name:
            return out
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
        return out

    fake_pkg.apply_relay_override = fake_apply
    fake_pkg.SettingsRelayResolutionError = _StubRelayResolutionError
    monkeypatch.setitem(sys.modules, "openakita", SimpleNamespace(relay=fake_pkg))
    monkeypatch.setitem(sys.modules, "openakita.relay", fake_pkg)


def _ref(base_url, api_key):
    return SimpleNamespace(base_url=base_url, api_key=api_key)


def test_no_relay_keeps_per_plugin(monkeypatch):
    _install_stub_relay_module(monkeypatch, refs_by_name={})
    plug = _bare_plugin()
    key, base = plug._resolve_effective_ark_endpoint(
        {"ark_api_key": "sk-direct", "ark_base_url": "https://ark.example.com"}
    )
    assert key == "sk-direct"
    assert base == "https://ark.example.com"


def test_relay_overrides(monkeypatch):
    _install_stub_relay_module(
        monkeypatch,
        refs_by_name={"r1": _ref("https://relay.example.com/v1", "sk-relay")},
    )
    plug = _bare_plugin()
    key, base = plug._resolve_effective_ark_endpoint(
        {
            "ark_api_key": "sk-direct",
            "ark_base_url": "https://ark.example.com",
            "ark_relay_endpoint": "r1",
        }
    )
    assert key == "sk-relay"
    assert base == "https://relay.example.com/v1"


def test_strict_missing_raises_http_400(monkeypatch):
    _install_stub_relay_module(monkeypatch, refs_by_name={})
    plug = _bare_plugin()
    with pytest.raises(HTTPException) as ei:
        plug._resolve_effective_ark_endpoint(
            {
                "ark_relay_endpoint": "ghost",
                "ark_relay_fallback_policy": "strict",
            }
        )
    assert ei.value.status_code == 400
    assert "ghost" in ei.value.detail


def test_official_missing_falls_back(monkeypatch):
    _install_stub_relay_module(monkeypatch, refs_by_name={})
    plug = _bare_plugin()
    key, base = plug._resolve_effective_ark_endpoint(
        {
            "ark_api_key": "sk-direct",
            "ark_base_url": "https://ark.example.com",
            "ark_relay_endpoint": "ghost",
        }
    )
    assert key == "sk-direct"
    assert base == "https://ark.example.com"


def test_import_failure_falls_back(monkeypatch):
    monkeypatch.setitem(sys.modules, "openakita.relay", None)
    plug = _bare_plugin()
    key, base = plug._resolve_effective_ark_endpoint(
        {
            "ark_api_key": "sk-direct",
            "ark_base_url": "https://ark.example.com",
            "ark_relay_endpoint": "ghost",
        }
    )
    assert key == "sk-direct"
    assert base == "https://ark.example.com"
