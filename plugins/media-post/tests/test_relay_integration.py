"""media-post — _resolve_vlm_endpoint relay integration.

When ``dashscope_relay_endpoint`` is set in plugin config the resolver
overlays the relay's base_url + api_key on the per-plugin
``dashscope_api_key``. Strict-policy + missing relay raises
HTTPException(400) so the Settings UI banner has actionable text.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from plugin import Plugin


def _bare_plugin():
    return object.__new__(Plugin)


class _StubError(Exception):
    def __init__(self, msg, *, user_message=None):
        super().__init__(msg)
        self.user_message = user_message or msg


def _install_stub_relay(monkeypatch, refs):
    pkg = SimpleNamespace()
    def apply(settings, *, default_base_url="", required_capability="", plugin_name=""):
        out = dict(settings)
        name = str(out.pop("relay_endpoint", "") or "").strip()
        policy = str(out.pop("relay_fallback_policy", "official") or "official")
        if not name:
            return out
        if name not in refs:
            if policy == "strict":
                raise _StubError(f"{name} missing", user_message=f"中转站 {name!r} 未找到")
            return out
        ref = refs[name]
        out["base_url"] = ref["base_url"]
        if ref.get("api_key"):
            out["api_key"] = ref["api_key"]
        return out
    pkg.apply_relay_override = apply
    pkg.SettingsRelayResolutionError = _StubError
    monkeypatch.setitem(sys.modules, "openakita", SimpleNamespace(relay=pkg))
    monkeypatch.setitem(sys.modules, "openakita.relay", pkg)


def test_no_relay_returns_per_plugin(monkeypatch):
    _install_stub_relay(monkeypatch, {})
    plug = _bare_plugin()
    key, base = plug._resolve_vlm_endpoint({"dashscope_api_key": "sk-direct"})
    assert key == "sk-direct"
    assert base == ""


def test_relay_overrides(monkeypatch):
    _install_stub_relay(
        monkeypatch,
        {"r": {"base_url": "https://relay.example.com/v1", "api_key": "sk-relay"}},
    )
    plug = _bare_plugin()
    key, base = plug._resolve_vlm_endpoint(
        {"dashscope_api_key": "sk-direct", "dashscope_relay_endpoint": "r"}
    )
    assert key == "sk-relay"
    assert base == "https://relay.example.com/v1"


def test_strict_missing_raises_400(monkeypatch):
    _install_stub_relay(monkeypatch, {})
    plug = _bare_plugin()
    with pytest.raises(HTTPException) as ei:
        plug._resolve_vlm_endpoint(
            {
                "dashscope_relay_endpoint": "ghost",
                "dashscope_relay_fallback_policy": "strict",
            }
        )
    assert ei.value.status_code == 400
    assert "ghost" in ei.value.detail


def test_official_missing_falls_back(monkeypatch):
    _install_stub_relay(monkeypatch, {})
    plug = _bare_plugin()
    key, base = plug._resolve_vlm_endpoint(
        {
            "dashscope_api_key": "sk-direct",
            "dashscope_relay_endpoint": "ghost",
        }
    )
    assert key == "sk-direct"
    assert base == ""


def test_import_failure_falls_back(monkeypatch):
    monkeypatch.setitem(sys.modules, "openakita.relay", None)
    plug = _bare_plugin()
    key, base = plug._resolve_vlm_endpoint(
        {"dashscope_api_key": "sk-direct", "dashscope_relay_endpoint": "ghost"}
    )
    assert key == "sk-direct"
    assert base == ""
