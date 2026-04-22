"""Tests for openakita_plugin_sdk.contrib.vendor_client.

Uses an in-memory ``httpx.MockTransport`` so we can simulate every code path
without an actual network round-trip.
"""

from __future__ import annotations

import re

import httpx
import pytest

from openakita_plugin_sdk.contrib import BaseVendorClient, VendorError
from openakita_plugin_sdk.contrib.vendor_client import (
    ERROR_KIND_AUTH,
    ERROR_KIND_CLIENT,
    ERROR_KIND_MODERATION,
    ERROR_KIND_NETWORK,
    ERROR_KIND_NOT_FOUND,
    ERROR_KIND_RATE_LIMIT,
    ERROR_KIND_SERVER,
    ERROR_KIND_TIMEOUT,
)


# ── helper: a BaseVendorClient that uses httpx.MockTransport ────────────────


class _Client(BaseVendorClient):
    """Test subclass — substitutes ``httpx.AsyncClient`` with our mock."""

    base_url = "https://api.example.com"

    def __init__(self, transport: httpx.MockTransport, **kw: object) -> None:
        # Tiny backoff so retry tests are fast (jitter still applies but tiny).
        kw.setdefault("retry_backoff", 0.001)
        kw.setdefault("retry_max_backoff", 0.005)
        super().__init__(**kw)  # type: ignore[arg-type]
        self._transport = transport

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer test"}

    async def cancel_task(self, task_id: str) -> bool:  # pragma: no cover
        return True


@pytest.fixture(autouse=True)
def _patch_async_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force every ``httpx.AsyncClient(...)`` to use the per-test transport.

    The test installs the transport on a mutable container before the call.
    """
    container: dict[str, httpx.MockTransport] = {}
    real_init = httpx.AsyncClient.__init__

    def patched_init(self: httpx.AsyncClient, *args: object, **kwargs: object) -> None:
        if "transport" not in kwargs and "transport" in container:
            kwargs["transport"] = container["transport"]
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)
    # Expose the container globally for the helper below
    pytest.transport_container = container  # type: ignore[attr-defined]


def _install(transport: httpx.MockTransport) -> None:
    pytest.transport_container["transport"] = transport  # type: ignore[attr-defined]


# ── Happy path ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_json_returns_parsed_payload() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["Authorization"] == "Bearer test"
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    _install(transport)
    out = await _Client(transport).get_json("/v1/x")
    assert out == {"ok": True}


# ── 4xx never retry, 429 / 5xx do retry, then succeed ───────────────────────


@pytest.mark.asyncio
async def test_404_does_not_retry_and_classified_as_not_found() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404, json={"error": "missing"})

    transport = httpx.MockTransport(handler)
    _install(transport)
    with pytest.raises(VendorError) as ei:
        await _Client(transport, max_retries=3).get_json("/v1/x")
    assert calls["n"] == 1
    assert ei.value.status == 404
    assert ei.value.kind == ERROR_KIND_NOT_FOUND
    assert ei.value.retryable is False


@pytest.mark.asyncio
async def test_400_does_not_retry_and_classified_as_client() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"error": "bad input"})

    transport = httpx.MockTransport(handler)
    _install(transport)
    with pytest.raises(VendorError) as ei:
        await _Client(transport, max_retries=3).get_json("/v1/x")
    assert calls["n"] == 1
    assert ei.value.kind == ERROR_KIND_CLIENT


@pytest.mark.asyncio
async def test_401_classified_as_auth() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(401))
    _install(transport)
    with pytest.raises(VendorError) as ei:
        await _Client(transport, max_retries=3).get_json("/v1/x")
    assert ei.value.kind == ERROR_KIND_AUTH


@pytest.mark.asyncio
async def test_429_retries_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, json={"error": "slow down"})
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    _install(transport)
    out = await _Client(transport, max_retries=3).get_json("/v1/x")
    assert out == {"ok": True}
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_503_retries_then_terminal_failure_classified_as_server() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, json={"error": "down"})

    transport = httpx.MockTransport(handler)
    _install(transport)
    with pytest.raises(VendorError) as ei:
        await _Client(transport, max_retries=2).get_json("/v1/x")
    assert calls["n"] == 3  # initial + 2 retries
    assert ei.value.kind == ERROR_KIND_SERVER
    assert ei.value.status == 503


# ── Network error retried, then surfaced ────────────────────────────────────


@pytest.mark.asyncio
async def test_network_error_retries_then_terminal_classified_as_network() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("oops")

    transport = httpx.MockTransport(handler)
    _install(transport)
    with pytest.raises(VendorError) as ei:
        await _Client(transport, max_retries=1).get_json("/v1/x")
    assert calls["n"] == 2
    assert ei.value.kind == ERROR_KIND_NETWORK
    assert ei.value.status is None


@pytest.mark.asyncio
async def test_timeout_classified_as_timeout() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=req)

    transport = httpx.MockTransport(handler)
    _install(transport)
    with pytest.raises(VendorError) as ei:
        await _Client(transport, max_retries=0).get_json("/v1/x")
    assert ei.value.kind == ERROR_KIND_TIMEOUT
    assert ei.value.status is None


# ── Moderation: never retry even on 5xx ─────────────────────────────────────


@pytest.mark.asyncio
async def test_moderation_in_5xx_body_does_not_retry() -> None:
    """5xx + body containing moderation keyword should fail immediately."""
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, json={
            "error": "Content policy violated: prompt rejected",
        })

    transport = httpx.MockTransport(handler)
    _install(transport)
    with pytest.raises(VendorError) as ei:
        await _Client(transport, max_retries=3).get_json("/v1/x")
    assert calls["n"] == 1  # NOT retried
    assert ei.value.kind == ERROR_KIND_MODERATION
    assert ei.value.retryable is False


@pytest.mark.asyncio
async def test_moderation_chinese_keyword_in_400_body() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(
        400, json={"error": "\u5185\u5bb9\u5b89\u5168\u68c0\u6d4b\u4e0d\u901a\u8fc7"}))
    _install(transport)
    with pytest.raises(VendorError) as ei:
        await _Client(transport, max_retries=3).get_json("/v1/x")
    assert ei.value.kind == ERROR_KIND_MODERATION


@pytest.mark.asyncio
async def test_moderation_in_200_body_treated_as_failure() -> None:
    """Some vendors return 200 + a moderation message — must surface as error."""
    transport = httpx.MockTransport(lambda r: httpx.Response(
        200, json={"warning": "moderation triggered, no output produced"}))
    _install(transport)
    with pytest.raises(VendorError) as ei:
        await _Client(transport).get_json("/v1/x")
    assert ei.value.kind == ERROR_KIND_MODERATION
    assert ei.value.status == 200


@pytest.mark.asyncio
async def test_moderation_pattern_can_be_disabled() -> None:
    """Pass moderation_pattern=None to bypass the check entirely."""
    transport = httpx.MockTransport(lambda r: httpx.Response(
        200, json={"text": "moderation in regular content shouldn't trigger"}))
    _install(transport)
    out = await _Client(transport, moderation_pattern=None).get_json("/v1/x")
    assert "text" in out  # success, no exception


@pytest.mark.asyncio
async def test_moderation_pattern_can_be_overridden() -> None:
    """Custom regex replaces the default."""
    custom = re.compile(r"forbidden", re.IGNORECASE)
    transport = httpx.MockTransport(lambda r: httpx.Response(
        400, json={"error": "Forbidden topic detected"}))
    _install(transport)
    with pytest.raises(VendorError) as ei:
        await _Client(transport, moderation_pattern=custom).get_json("/v1/x")
    assert ei.value.kind == ERROR_KIND_MODERATION


# ── Backwards compatibility: no kind kwarg ──────────────────────────────────


def test_vendor_error_kind_defaults_to_unknown() -> None:
    """Existing plugin code raising VendorError without kind must keep working."""
    e = VendorError("legacy error", status=500, retryable=True)
    assert e.kind == "unknown"


def test_vendor_error_accepts_kind() -> None:
    e = VendorError("typed", kind=ERROR_KIND_RATE_LIMIT)
    assert e.kind == "rate_limit"
