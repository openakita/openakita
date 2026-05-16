"""happyhorse-video — shared relay-station integration.

Plugins can point at a relay registered in OpenAkita's shared relay
registry instead of pasting per-plugin api_key + base_url. These
tests freeze the integration contract:

1. With ``relay_endpoint=""`` the client behaves exactly like before
   (per-plugin api_key / base_url). Nothing about the previous
   pipeline changes — that is the upgrade-safety guarantee.
2. With ``relay_endpoint=<name>`` the client's base_url + auth header
   come from the resolved RelayReference, not from the per-plugin
   settings. The settings dict carries ``_relay_reference`` so the
   pipeline / cost preview can consult ``supports_model`` before
   submitting.
3. When the relay name is missing, behaviour depends on the
   ``relay_fallback_policy`` knob:
     - ``official`` (default): warn and fall back silently
     - ``strict``: raise VendorError(ERROR_KIND_CLIENT) so the user
       has to fix the relay name before continuing.
4. When ``openakita.relay`` cannot be imported (degraded environment)
   the client must NOT crash — it just warns and uses per-plugin
   settings, same as missing-name fallback.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from happyhorse_dashscope_client import (
    DASHSCOPE_BASE_URL_BJ,
    HappyhorseDashScopeClient,
    make_default_settings,
)
from happyhorse_inline.vendor_client import VendorError


def _read_settings_factory(**overrides):
    def _read():
        s = make_default_settings()
        s.update(overrides)
        return s
    return _read


# ─── Fake openakita.relay module so tests don't depend on PYTHONPATH ──


class _StubRelayNotFound(Exception):
    def __init__(self, name, available=()):
        super().__init__(name)
        self.name = name
        self.available = list(available)


def _install_stub_relay_module(monkeypatch, *, refs_by_name=None, raises=None):
    """Inject a fake ``openakita.relay`` module into sys.modules.

    Mirrors only the API surface ``happyhorse_dashscope_client._settings``
    actually uses: ``resolve_relay_endpoint`` + ``RelayNotFound``.
    """
    fake_pkg = SimpleNamespace()

    def fake_resolve(name, *, required_capability=None):
        if raises is not None:
            raise raises
        ref = (refs_by_name or {}).get(name)
        if ref is None:
            raise _StubRelayNotFound(name, available=list((refs_by_name or {}).keys()))
        return ref

    fake_pkg.resolve_relay_endpoint = fake_resolve
    fake_pkg.RelayNotFound = _StubRelayNotFound
    monkeypatch.setitem(sys.modules, "openakita", SimpleNamespace(relay=fake_pkg))
    monkeypatch.setitem(sys.modules, "openakita.relay", fake_pkg)


def _make_ref(name="yunwu-video", base_url="https://relay.example.com/v1", api_key="sk-relay", supported=None):
    return SimpleNamespace(
        name=name,
        base_url=base_url,
        api_key=api_key,
        capabilities=["video"],
        supported_models=list(supported or []),
        models_synced_at=None,
        note=None,
        extra={},
        supports_model=lambda m: not supported or (m or "").lower() in {x.lower() for x in supported},
    )


# ─── 1. Backward compatibility — no relay configured ─────────────────


def test_no_relay_keeps_per_plugin_settings(monkeypatch):
    _install_stub_relay_module(monkeypatch, refs_by_name={"unused": _make_ref()})
    c = HappyhorseDashScopeClient(
        _read_settings_factory(api_key="sk-direct", base_url="https://dashscope.aliyuncs.com")
    )
    s = c._settings()
    assert s["api_key"] == "sk-direct"
    assert s["base_url"] == "https://dashscope.aliyuncs.com"
    assert "_relay_reference" not in s
    assert c.base_url == "https://dashscope.aliyuncs.com"


# ─── 2. Relay resolution succeeds ────────────────────────────────────


def test_relay_overrides_base_url_and_api_key(monkeypatch):
    ref = _make_ref(base_url="https://yunwu.example.com/v1", api_key="sk-yunwu")
    _install_stub_relay_module(monkeypatch, refs_by_name={"yunwu-video": ref})
    c = HappyhorseDashScopeClient(
        _read_settings_factory(
            api_key="sk-direct",
            base_url="https://dashscope.aliyuncs.com",
            relay_endpoint="yunwu-video",
        )
    )
    s = c._settings()
    assert s["api_key"] == "sk-yunwu"
    assert s["base_url"] == "https://yunwu.example.com/v1"
    assert s["_relay_reference"] is ref
    assert c.base_url == "https://yunwu.example.com/v1"
    # auth_headers() must reflect the relay key, not the per-plugin one
    h = c.auth_headers()
    assert h["Authorization"] == "Bearer sk-yunwu"


def test_relay_with_empty_apikey_keeps_per_plugin_key(monkeypatch):
    """A relay with no key (public/anon endpoints exist) should fall
    back to the per-plugin api_key instead of clearing auth."""
    ref = _make_ref(api_key="")
    _install_stub_relay_module(monkeypatch, refs_by_name={"yunwu-video": ref})
    c = HappyhorseDashScopeClient(
        _read_settings_factory(api_key="sk-direct", relay_endpoint="yunwu-video")
    )
    s = c._settings()
    assert s["api_key"] == "sk-direct"
    assert s["base_url"] == "https://relay.example.com/v1"  # base_url still overridden


# ─── 3. Fallback policy ──────────────────────────────────────────────


def test_missing_relay_with_official_policy_falls_back(monkeypatch):
    _install_stub_relay_module(monkeypatch, refs_by_name={})
    c = HappyhorseDashScopeClient(
        _read_settings_factory(
            api_key="sk-direct",
            base_url="https://dashscope.aliyuncs.com",
            relay_endpoint="ghost",
            relay_fallback_policy="official",
        )
    )
    s = c._settings()
    assert s["api_key"] == "sk-direct"
    assert s["base_url"] == "https://dashscope.aliyuncs.com"
    assert "_relay_reference" not in s


def test_missing_relay_with_strict_policy_raises_vendorerror(monkeypatch):
    _install_stub_relay_module(monkeypatch, refs_by_name={})
    # NOTE: HappyhorseDashScopeClient.__init__ primes _settings() once
    # so a strict-policy mis-config surfaces immediately at construction
    # time. This is intentional: catching it at runtime would force the
    # first task to pay the latency of one full pipeline before failing.
    with pytest.raises(VendorError) as ei:
        HappyhorseDashScopeClient(
            _read_settings_factory(
                relay_endpoint="ghost",
                relay_fallback_policy="strict",
            )
        )
    assert "ghost" in str(ei.value)
    assert ei.value.retryable is False  # config error, retry won't help


# ─── 4. Degraded environment ─────────────────────────────────────────


def test_openakita_relay_import_failure_falls_back_gracefully(monkeypatch):
    """When the bundled / installed plugin runs in an environment
    that does not expose openakita.relay, the client must NOT crash —
    it just keeps the per-plugin values and logs."""
    monkeypatch.setitem(sys.modules, "openakita.relay", None)
    c = HappyhorseDashScopeClient(
        _read_settings_factory(
            api_key="sk-direct",
            base_url="https://dashscope.aliyuncs.com",
            relay_endpoint="yunwu-video",
        )
    )
    s = c._settings()
    assert s["api_key"] == "sk-direct"
    assert s["base_url"] == "https://dashscope.aliyuncs.com"


# ─── 5. make_default_settings exposes the new fields ────────────────


def test_default_settings_carry_relay_keys():
    """Plugin Settings UI reflects on make_default_settings to render
    the form; the two new fields must be in there."""
    d = make_default_settings()
    assert "relay_endpoint" in d
    assert d["relay_endpoint"] == ""
    assert d["relay_fallback_policy"] == "official"
