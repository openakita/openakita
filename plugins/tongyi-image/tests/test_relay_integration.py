"""tongyi-image — relay endpoint resolution in _resolve_effective_endpoint.

Verifies that:

1. Without ``dashscope_relay_endpoint`` the resolver returns the
   per-plugin api_key + base_url unchanged.
2. With a valid relay name the resolver overrides both.
3. With ``dashscope_relay_fallback_policy="strict"`` and a missing
   relay the resolver raises DashScopeError carrying the Chinese
   user message so the Settings UI banner has actionable text.
4. With the default ``official`` policy a missing relay just warns
   and the per-plugin values are kept (user never blocked).
5. When openakita.relay cannot be imported (degraded install) the
   resolver degrades silently.

The Plugin class is instantiated WITHOUT the full host context;
``_resolve_effective_endpoint`` is a pure method that only reads the
config dict, so testing it standalone keeps the surface small.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from plugin import Plugin
from tongyi_dashscope_client import DashScopeError


def _bare_plugin():
    """Construct a Plugin shell with no async init / no DashScope
    client. We only need ``_resolve_effective_endpoint`` — that
    method is intentionally side-effect free so it does not need
    the rest of the plugin's plumbing."""
    return object.__new__(Plugin)


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
        return out

    fake_pkg.apply_relay_override = fake_apply
    fake_pkg.SettingsRelayResolutionError = _StubRelayResolutionError
    monkeypatch.setitem(sys.modules, "openakita", SimpleNamespace(relay=fake_pkg))
    monkeypatch.setitem(sys.modules, "openakita.relay", fake_pkg)


def _ref(base_url, api_key):
    return SimpleNamespace(base_url=base_url, api_key=api_key)


def test_no_relay_returns_per_plugin_values(monkeypatch):
    _install_stub_relay_module(monkeypatch, refs_by_name={})
    plug = _bare_plugin()
    cfg = {
        "dashscope_api_key": "sk-direct",
        "dashscope_base_url": "https://direct.example.com",
    }
    key, base = plug._resolve_effective_endpoint(cfg)
    assert key == "sk-direct"
    assert base == "https://direct.example.com"


def test_relay_overrides_both_fields(monkeypatch):
    _install_stub_relay_module(
        monkeypatch,
        refs_by_name={"yunwu": _ref("https://yunwu.example.com/v1", "sk-yunwu")},
    )
    plug = _bare_plugin()
    cfg = {
        "dashscope_api_key": "sk-direct",
        "dashscope_base_url": "https://direct.example.com",
        "dashscope_relay_endpoint": "yunwu",
    }
    key, base = plug._resolve_effective_endpoint(cfg)
    assert key == "sk-yunwu"
    assert base == "https://yunwu.example.com/v1"


def test_missing_relay_strict_raises_dashscope_error(monkeypatch):
    _install_stub_relay_module(monkeypatch, refs_by_name={})
    plug = _bare_plugin()
    cfg = {
        "dashscope_api_key": "sk-direct",
        "dashscope_relay_endpoint": "ghost",
        "dashscope_relay_fallback_policy": "strict",
    }
    with pytest.raises(DashScopeError) as ei:
        plug._resolve_effective_endpoint(cfg)
    assert "ghost" in str(ei.value)
    assert ei.value.code == "RelayResolutionError"
    assert ei.value.status_code == 400


def test_missing_relay_official_falls_back(monkeypatch):
    _install_stub_relay_module(monkeypatch, refs_by_name={})
    plug = _bare_plugin()
    cfg = {
        "dashscope_api_key": "sk-direct",
        "dashscope_base_url": "https://direct.example.com",
        "dashscope_relay_endpoint": "ghost",
        # policy defaults to "official"
    }
    key, base = plug._resolve_effective_endpoint(cfg)
    assert key == "sk-direct"
    assert base == "https://direct.example.com"


def test_openakita_relay_import_failure_falls_back(monkeypatch):
    monkeypatch.setitem(sys.modules, "openakita.relay", None)
    plug = _bare_plugin()
    cfg = {
        "dashscope_api_key": "sk-direct",
        "dashscope_base_url": "https://direct.example.com",
        "dashscope_relay_endpoint": "ghost",
    }
    key, base = plug._resolve_effective_endpoint(cfg)
    assert key == "sk-direct"
    assert base == "https://direct.example.com"
