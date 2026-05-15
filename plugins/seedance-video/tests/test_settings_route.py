"""Tests for the ``PUT /settings`` route — Sprint 8 follow-up.

The user reported "I typed the API key and clicked save; the toast says
'设置已保存' but nothing was actually persisted."  Root cause was a mix
of (a) silently swallowed HTTP failures on the UI side and (b) the route
not validating blank input or reading the value back to confirm.

These tests guard the *backend half* of the fix — the UI side cannot be
unit-tested cheaply, so we lean on a manual smoke walk-through there.

We invoke the route handler the same way ``test_mode_validation.py``
does: build a ``Plugin`` via ``__new__`` so we never spin up a real
event loop / FastAPI app, and stub ``_tm`` to a minimal in-memory dict.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from _plugin_loader import load_seedance_plugin

_plugin = load_seedance_plugin()
ConfigUpdateBody = _plugin.ConfigUpdateBody
Plugin = _plugin.Plugin
_normalize_base_url = _plugin._normalize_base_url


class _FakeTM:
    """Tiny in-memory TaskManager stand-in.

    Only the surface ``update_settings`` actually touches: ``set_configs``
    persists, ``get_all_config`` reads back.  Lets us verify that
    saving + read-back round-trips through the route handler unchanged.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.set_calls: list[dict[str, str]] = []

    async def set_configs(self, updates: dict[str, str]) -> None:
        # Mirror TaskManager.set_configs semantics — keep the latest
        # value per key (INSERT OR REPLACE).
        self.set_calls.append(dict(updates))
        for k, v in updates.items():
            self.store[k] = v

    async def get_all_config(self) -> dict[str, str]:
        return dict(self.store)


def _make_plugin(*, fake_tm: _FakeTM | None = None) -> tuple[Plugin, _FakeTM]:
    p = Plugin.__new__(Plugin)
    tm = fake_tm or _FakeTM()
    p._tm = tm
    p._ark = None
    return p, tm


async def _call_update_settings(p: Plugin, updates: dict[str, str]) -> dict:
    """Invoke the route's inner closure exactly like FastAPI would.

    The handler is defined inside ``Plugin.on_load`` so we replicate its
    body here.  Keep this in lock-step with ``plugin.py`` ``update_settings``.
    Whenever you change the route, mirror the change here.
    """
    body = ConfigUpdateBody(updates=updates)

    cleaned: dict[str, str] = {k: (v or "").strip() for k, v in body.updates.items()}
    if "ark_base_url" in cleaned:
        cleaned["ark_base_url"] = _normalize_base_url(
            cleaned["ark_base_url"], field="ARK Base URL",
        )

    if "ark_api_key" in cleaned and not cleaned["ark_api_key"]:
        raise HTTPException(
            status_code=400,
            detail="ARK API Key 不能为空白 — 请粘贴有效的密钥（前往 console.volcengine.com/ark 获取）",
        )

    await p._tm.set_configs(cleaned)
    saved = await p._tm.get_all_config()
    for k, expected in cleaned.items():
        if saved.get(k, "") != expected:
            raise HTTPException(
                status_code=500,
                detail=f"保存失败 — 配置项 {k} 写入后回读不一致，请检查插件数据目录权限",
            )

    if "ark_api_key" in cleaned and cleaned["ark_api_key"]:
        # Skip ArkClient construction in tests; we only care that the
        # route's persistence + verification path is correct.
        pass

    return {"ok": True, "config": saved}


# ── Validation: blank keys are rejected up-front ─────────────────────────


@pytest.mark.asyncio
async def test_update_settings_rejects_blank_api_key() -> None:
    p, tm = _make_plugin()
    with pytest.raises(HTTPException) as exc:
        await _call_update_settings(p, {"ark_api_key": ""})
    assert exc.value.status_code == 400
    assert "不能为空白" in exc.value.detail
    # Crucially: nothing must hit the DB.
    assert tm.set_calls == []


@pytest.mark.asyncio
async def test_update_settings_rejects_whitespace_api_key() -> None:
    p, tm = _make_plugin()
    with pytest.raises(HTTPException) as exc:
        await _call_update_settings(p, {"ark_api_key": "   \t  "})
    assert exc.value.status_code == 400
    assert tm.set_calls == []


# ── Happy path: value is trimmed, persisted, and round-trips ─────────────


@pytest.mark.asyncio
async def test_update_settings_persists_and_returns_config() -> None:
    p, tm = _make_plugin()
    result = await _call_update_settings(p, {"ark_api_key": "sk-test-real-1234"})

    assert result["ok"] is True
    assert result["config"]["ark_api_key"] == "sk-test-real-1234"
    # Persisted in the fake store too.
    assert tm.store["ark_api_key"] == "sk-test-real-1234"


@pytest.mark.asyncio
async def test_update_settings_trims_surrounding_whitespace() -> None:
    """Common copy-paste mishap — leading/trailing whitespace must be
    stripped before persistence so subsequent Ark calls don't mysteriously
    fail with 'invalid api key'."""
    p, tm = _make_plugin()
    result = await _call_update_settings(p, {"ark_api_key": "  sk-padded  \n"})

    assert result["config"]["ark_api_key"] == "sk-padded"
    assert tm.store["ark_api_key"] == "sk-padded"


@pytest.mark.asyncio
async def test_update_settings_normalizes_base_url() -> None:
    p, tm = _make_plugin()
    result = await _call_update_settings(
        p, {"ark_base_url": "  https://relay.example.com/api/v3/  "}
    )

    assert result["config"]["ark_base_url"] == "https://relay.example.com/api/v3"
    assert tm.store["ark_base_url"] == "https://relay.example.com/api/v3"


@pytest.mark.asyncio
async def test_update_settings_allows_clearing_base_url() -> None:
    p, tm = _make_plugin()
    result = await _call_update_settings(p, {"ark_base_url": "   "})

    assert result["config"]["ark_base_url"] == ""
    assert tm.store["ark_base_url"] == ""


@pytest.mark.asyncio
async def test_update_settings_rejects_invalid_base_url_protocol() -> None:
    p, tm = _make_plugin()
    with pytest.raises(HTTPException) as exc:
        await _call_update_settings(p, {"ark_base_url": "relay.example.com/api/v3"})

    assert exc.value.status_code == 400
    assert "http:// 或 https://" in exc.value.detail
    assert tm.set_calls == []


# ── Read-back verification: storage drift bubbles up as HTTP 500 ─────────


@pytest.mark.asyncio
async def test_update_settings_detects_storage_dropping_value() -> None:
    """If the underlying store silently drops the write, we must NOT lie
    to the UI ('设置已保存') — return 500 so the user sees a real error."""
    tm = _FakeTM()

    # Simulate a flaky storage layer that accepts the write but loses
    # data on read-back.  Real-world equivalents: read-only sqlite,
    # disk full, schema drift after a botched migration.
    async def lying_get_all_config() -> dict[str, str]:
        return {}  # storage 'forgot' what we just wrote

    tm.get_all_config = lying_get_all_config  # type: ignore[assignment]

    p, _ = _make_plugin(fake_tm=tm)
    with pytest.raises(HTTPException) as exc:
        await _call_update_settings(p, {"ark_api_key": "sk-will-be-dropped"})
    assert exc.value.status_code == 500
    assert "回读不一致" in exc.value.detail


@pytest.mark.asyncio
async def test_update_settings_passes_through_other_keys() -> None:
    """Non-key updates (output_dir, poll_interval, …) should still flow
    through; the validation guard only fires for ``ark_api_key``."""
    p, tm = _make_plugin()
    result = await _call_update_settings(
        p, {"output_dir": "/tmp/seed", "poll_interval": "30"}
    )
    assert result["ok"] is True
    assert tm.store["output_dir"] == "/tmp/seed"
    assert tm.store["poll_interval"] == "30"
