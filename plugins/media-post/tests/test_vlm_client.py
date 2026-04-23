"""Phase 2b — ``mediapost_vlm_client`` unit tests (Gate 2b per §11 Phase 2b).

Coverage focus per §10.1 ``test_vlm_client.py`` row:

- ``call_vlm_batch`` — success path, JSON fence stripping, length
  mismatch returns ``None``, OOM-safe ``finally`` invocation.
- ``call_vlm_concurrent`` — order preservation across batches and
  failed-slot ``None`` handling.
- ``qwen_plus_call`` — happy path + the 9 canonical error kinds
  surfaced as :class:`MediaPostError`.

Tests inject a fully in-memory mock ``httpx.AsyncClient`` so no real
network call ever fires. ``DEFAULT_MAX_RETRIES`` is set to 0 in the
client constructor so each test runs in milliseconds even when the
first attempt fails.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from typing import Any

import pytest
from mediapost_models import ALLOWED_ERROR_KINDS, MediaPostError
from mediapost_vlm_client import (
    DEFAULT_VLM_MODEL,
    MAX_VLM_BATCH_FRAMES,
    MediaPostVlmClient,
    _classify_http_response,
    _classify_transport_error,
    _parse_vlm_json_list,
    _strip_json_fence,
)

# ---------------------------------------------------------------------------
# Mock httpx — installed at module level so the lazy import inside the
# client picks it up. We restore the original module after each test via
# the ``mock_httpx`` fixture.
# ---------------------------------------------------------------------------


class _MockResponse:
    def __init__(
        self,
        status_code: int,
        json_payload: dict[str, Any] | None = None,
        text: str | None = None,
    ) -> None:
        self.status_code = status_code
        self._json = json_payload
        self.text = text if text is not None else json.dumps(json_payload or {})

    def json(self) -> dict[str, Any]:
        if self._json is None:
            raise ValueError("no json")
        return self._json


class _MockAsyncClient:
    """In-memory drop-in for ``httpx.AsyncClient``.

    The test sets ``responses`` to a list of ``_MockResponse`` /
    Exception instances; each ``post`` call pops the next one.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.responses: list[Any] = []
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    async def post(self, url: str, **kwargs: Any) -> _MockResponse:
        self.calls.append({"url": url, **kwargs})
        if not self.responses:
            raise AssertionError("MockAsyncClient: ran out of scripted responses")
        nxt = self.responses.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt

    async def aclose(self) -> None:
        self.closed = True


def _install_mock_httpx() -> tuple[types.ModuleType, _MockAsyncClient]:
    """Install a stub ``httpx`` module exposing the bits the client uses."""
    mock_client = _MockAsyncClient()
    mod = types.ModuleType("httpx")

    class _TimeoutException(Exception):
        pass

    class _HTTPError(Exception):
        pass

    class _ConnectError(_HTTPError):
        pass

    mod.AsyncClient = lambda *a, **kw: mock_client  # type: ignore[attr-defined]
    mod.TimeoutException = _TimeoutException  # type: ignore[attr-defined]
    mod.HTTPError = _HTTPError  # type: ignore[attr-defined]
    mod.ConnectError = _ConnectError  # type: ignore[attr-defined]
    return mod, mock_client


@pytest.fixture()
def mock_httpx():
    """Swap ``httpx`` for an in-memory mock; restore on teardown."""
    original = sys.modules.get("httpx")
    mod, mock_client = _install_mock_httpx()
    sys.modules["httpx"] = mod
    try:
        yield mod, mock_client
    finally:
        if original is not None:
            sys.modules["httpx"] = original
        else:
            sys.modules.pop("httpx", None)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _vlm_response(payload: list[dict[str, Any]]) -> _MockResponse:
    return _MockResponse(
        200,
        json_payload={
            "choices": [{"message": {"content": json.dumps(payload)}}],
        },
    )


def _qwen_plus_response(text: str) -> _MockResponse:
    return _MockResponse(
        200, json_payload={"choices": [{"message": {"content": text}}]}
    )


# ---------------------------------------------------------------------------
# Helpers (pure)
# ---------------------------------------------------------------------------


class TestStripJsonFence:
    def test_handles_json_fence(self) -> None:
        raw = "preface\n```json\n[{\"a\": 1}]\n```\nepilogue"
        assert _strip_json_fence(raw) == '[{"a": 1}]'

    def test_handles_unlabeled_fence(self) -> None:
        raw = "```\n[1,2,3]\n```"
        assert _strip_json_fence(raw) == "[1,2,3]"

    def test_no_fence_returns_stripped(self) -> None:
        assert _strip_json_fence("  [1] \n") == "[1]"

    def test_empty_returns_empty(self) -> None:
        assert _strip_json_fence("") == ""


class TestParseVlmJsonList:
    def test_happy_path(self) -> None:
        raw = "```json\n[{\"x\": 1}, {\"x\": 2}]\n```"
        out = _parse_vlm_json_list(raw, expected_len=2)
        assert out == [{"x": 1}, {"x": 2}]

    def test_length_mismatch_returns_none(self) -> None:
        raw = json.dumps([{"a": 1}])
        assert _parse_vlm_json_list(raw, expected_len=2) is None

    def test_non_list_returns_none(self) -> None:
        raw = json.dumps({"a": 1})
        assert _parse_vlm_json_list(raw, expected_len=1) is None

    def test_invalid_json_returns_none(self) -> None:
        assert _parse_vlm_json_list("not json", expected_len=1) is None

    def test_empty_returns_none(self) -> None:
        assert _parse_vlm_json_list("", expected_len=1) is None

    def test_non_dict_items_become_empty_dict(self) -> None:
        raw = json.dumps([{"a": 1}, "scalar", None])
        out = _parse_vlm_json_list(raw, expected_len=3)
        assert out == [{"a": 1}, {}, {}]


class TestClassifyHttpResponse:
    def test_2xx_returns_none_kind(self) -> None:
        kind, retryable, _ = _classify_http_response(_MockResponse(200, {}))
        assert kind is None
        assert retryable is False

    def test_401_is_auth(self) -> None:
        kind, retryable, _ = _classify_http_response(_MockResponse(401, text="nope"))
        assert kind == "auth"
        assert retryable is False

    def test_429_is_quota_retryable(self) -> None:
        kind, retryable, _ = _classify_http_response(_MockResponse(429, text="rate"))
        assert kind == "quota"
        assert retryable is True

    def test_500_is_network_retryable(self) -> None:
        kind, retryable, _ = _classify_http_response(_MockResponse(503, text="oops"))
        assert kind == "network"
        assert retryable is True

    def test_404_is_format(self) -> None:
        kind, _, _ = _classify_http_response(_MockResponse(404, text=""))
        assert kind == "format"

    def test_400_quota_payload_maps_to_quota(self) -> None:
        kind, _, _ = _classify_http_response(_MockResponse(400, text="quota_exceeded"))
        assert kind == "quota"

    def test_moderation_payload_maps_to_moderation(self) -> None:
        kind, _, _ = _classify_http_response(
            _MockResponse(400, text='"code":"data_inspection_failed"')
        )
        assert kind == "moderation"

    def test_4xx_other_is_format(self) -> None:
        kind, _, _ = _classify_http_response(_MockResponse(422, text="schema bad"))
        assert kind == "format"


class TestClassifyTransportError:
    def test_timeout_class(self) -> None:
        class TimeoutException(Exception):
            pass

        assert _classify_transport_error(TimeoutException()) == "timeout"

    def test_connect_class(self) -> None:
        class ConnectError(Exception):
            pass

        assert _classify_transport_error(ConnectError()) == "network"

    def test_unknown_class(self) -> None:
        assert _classify_transport_error(RuntimeError("?")) == "unknown"


# ---------------------------------------------------------------------------
# call_vlm_batch
# ---------------------------------------------------------------------------


class TestCallVlmBatch:
    def test_happy_path_returns_parsed_list(self, mock_httpx) -> None:
        _, mock_client = mock_httpx
        client = MediaPostVlmClient(api_key="sk-test", max_retries=0)
        mock_client.responses = [
            _vlm_response([{"score": 4.2}, {"score": 3.8}])
        ]
        out = _run(
            client.call_vlm_batch(
                ["b64a", "b64b"],
                [0, 1],
                "rate {frame_count} frames",
                {"frame_count": 2},
            )
        )
        assert out == [{"score": 4.2}, {"score": 3.8}]
        assert mock_client.calls[0]["json"]["model"] == DEFAULT_VLM_MODEL

    def test_strips_json_fence_in_response(self, mock_httpx) -> None:
        _, mock_client = mock_httpx
        client = MediaPostVlmClient(api_key="sk-test", max_retries=0)
        mock_client.responses = [
            _MockResponse(
                200,
                json_payload={
                    "choices": [
                        {
                            "message": {
                                "content": "```json\n[{\"a\":1}]\n```"
                            }
                        }
                    ]
                },
            )
        ]
        out = _run(
            client.call_vlm_batch(
                ["b64"], [0], "p {frame_count}", {"frame_count": 1}
            )
        )
        assert out == [{"a": 1}]

    def test_length_mismatch_returns_none(self, mock_httpx) -> None:
        _, mock_client = mock_httpx
        client = MediaPostVlmClient(api_key="sk-test", max_retries=0)
        mock_client.responses = [_vlm_response([{"score": 1}])]  # only 1, asked 2
        out = _run(
            client.call_vlm_batch(
                ["b1", "b2"],
                [0, 1],
                "p {frame_count}",
                {"frame_count": 2},
            )
        )
        assert out is None

    def test_empty_frames_raises_format(self, mock_httpx) -> None:
        client = MediaPostVlmClient(api_key="sk-test", max_retries=0)
        with pytest.raises(MediaPostError) as exc_info:
            _run(client.call_vlm_batch([], [], "p", None))
        assert exc_info.value.kind == "format"

    def test_indices_length_mismatch_raises_format(self, mock_httpx) -> None:
        client = MediaPostVlmClient(api_key="sk-test", max_retries=0)
        with pytest.raises(MediaPostError) as exc_info:
            _run(client.call_vlm_batch(["b1"], [0, 1], "p", None))
        assert exc_info.value.kind == "format"

    def test_oversized_batch_raises_format(self, mock_httpx) -> None:
        client = MediaPostVlmClient(api_key="sk-test", max_retries=0)
        with pytest.raises(MediaPostError) as exc_info:
            _run(
                client.call_vlm_batch(
                    [f"b{i}" for i in range(MAX_VLM_BATCH_FRAMES + 1)],
                    list(range(MAX_VLM_BATCH_FRAMES + 1)),
                    "p {frame_count}",
                    {"frame_count": MAX_VLM_BATCH_FRAMES + 1},
                )
            )
        assert exc_info.value.kind == "format"

    def test_missing_api_key_raises_auth(self, mock_httpx) -> None:
        client = MediaPostVlmClient(api_key="", max_retries=0)
        with pytest.raises(MediaPostError) as exc_info:
            _run(
                client.call_vlm_batch(
                    ["b"], [0], "p {frame_count}", {"frame_count": 1}
                )
            )
        assert exc_info.value.kind == "auth"

    def test_template_format_error_raises_format(self, mock_httpx) -> None:
        client = MediaPostVlmClient(api_key="sk-test", max_retries=0)
        with pytest.raises(MediaPostError) as exc_info:
            _run(
                client.call_vlm_batch(
                    ["b"], [0], "p {missing_key}", {"frame_count": 1}
                )
            )
        assert exc_info.value.kind == "format"

    def test_finally_clears_buffers_even_on_error(self, mock_httpx) -> None:
        # Confirms the ``finally`` doesn't swallow the original exception
        # AND that a follow-up call still works (i.e. no leaked state).
        _, mock_client = mock_httpx
        client = MediaPostVlmClient(api_key="sk-test", max_retries=0)
        mock_client.responses = [
            _MockResponse(401, text="invalid api key"),
            _vlm_response([{"score": 5}]),
        ]
        with pytest.raises(MediaPostError) as exc_info:
            _run(
                client.call_vlm_batch(
                    ["b"], [0], "p {frame_count}", {"frame_count": 1}
                )
            )
        assert exc_info.value.kind == "auth"
        # Second call is still healthy.
        out = _run(
            client.call_vlm_batch(
                ["b"], [0], "p {frame_count}", {"frame_count": 1}
            )
        )
        assert out == [{"score": 5}]


# ---------------------------------------------------------------------------
# call_vlm_concurrent
# ---------------------------------------------------------------------------


class TestCallVlmConcurrent:
    def test_preserves_order_across_batches(self, mock_httpx) -> None:
        _, mock_client = mock_httpx
        client = MediaPostVlmClient(api_key="sk-test", max_retries=0)
        # 10 frames, batch_size=3 -> 4 batches: 3+3+3+1
        mock_client.responses = [
            _vlm_response([{"i": 0}, {"i": 1}, {"i": 2}]),
            _vlm_response([{"i": 3}, {"i": 4}, {"i": 5}]),
            _vlm_response([{"i": 6}, {"i": 7}, {"i": 8}]),
            _vlm_response([{"i": 9}]),
        ]
        out = _run(
            client.call_vlm_concurrent(
                [f"b{i}" for i in range(10)],
                list(range(10)),
                "p {frame_count}",
                lambda idxs: {"frame_count": len(idxs)},
                batch_size=3,
                concurrency=2,
            )
        )
        assert out == [{"i": i} for i in range(10)]

    def test_failed_batch_yields_none_slots(self, mock_httpx) -> None:
        _, mock_client = mock_httpx
        client = MediaPostVlmClient(api_key="sk-test", max_retries=0)
        # 4 frames, batch_size=2 -> 2 batches; second batch returns 401.
        mock_client.responses = [
            _vlm_response([{"ok": 1}, {"ok": 2}]),
            _MockResponse(429, text="too many requests"),
        ]
        out = _run(
            client.call_vlm_concurrent(
                [f"b{i}" for i in range(4)],
                list(range(4)),
                "p {frame_count}",
                lambda idxs: {"frame_count": len(idxs)},
                batch_size=2,
                concurrency=1,
            )
        )
        assert out == [{"ok": 1}, {"ok": 2}, None, None]

    def test_empty_input_returns_empty(self, mock_httpx) -> None:
        client = MediaPostVlmClient(api_key="sk-test", max_retries=0)
        out = _run(
            client.call_vlm_concurrent(
                [], [], "p", lambda idxs: {}, batch_size=8, concurrency=2
            )
        )
        assert out == []

    def test_invalid_batch_size_raises_format(self, mock_httpx) -> None:
        client = MediaPostVlmClient(api_key="sk-test", max_retries=0)
        with pytest.raises(MediaPostError) as exc_info:
            _run(
                client.call_vlm_concurrent(
                    ["b"], [0], "p", lambda idxs: {},
                    batch_size=MAX_VLM_BATCH_FRAMES + 1,
                    concurrency=1,
                )
            )
        assert exc_info.value.kind == "format"

    def test_input_length_mismatch_raises_format(self, mock_httpx) -> None:
        client = MediaPostVlmClient(api_key="sk-test", max_retries=0)
        with pytest.raises(MediaPostError) as exc_info:
            _run(
                client.call_vlm_concurrent(
                    ["b1", "b2"], [0],
                    "p", lambda idxs: {},
                    batch_size=8, concurrency=1,
                )
            )
        assert exc_info.value.kind == "format"


# ---------------------------------------------------------------------------
# qwen_plus_call
# ---------------------------------------------------------------------------


class TestQwenPlusCall:
    def test_happy_path_returns_content(self, mock_httpx) -> None:
        _, mock_client = mock_httpx
        client = MediaPostVlmClient(api_key="sk-test", max_retries=0)
        mock_client.responses = [_qwen_plus_response("hello world")]
        out = _run(
            client.qwen_plus_call([{"role": "user", "content": "hi"}])
        )
        assert out == "hello world"

    def test_empty_messages_raises_format(self, mock_httpx) -> None:
        client = MediaPostVlmClient(api_key="sk-test", max_retries=0)
        with pytest.raises(MediaPostError) as exc_info:
            _run(client.qwen_plus_call([]))
        assert exc_info.value.kind == "format"

    def test_no_api_key_raises_auth(self, mock_httpx) -> None:
        client = MediaPostVlmClient(api_key="", max_retries=0)
        with pytest.raises(MediaPostError) as exc_info:
            _run(client.qwen_plus_call([{"role": "user", "content": "hi"}]))
        assert exc_info.value.kind == "auth"


# ---------------------------------------------------------------------------
# 9-kind error coverage — each canonical kind must surface from at
# least one HTTP-shaped failure path.
# ---------------------------------------------------------------------------


class TestNineErrorKinds:
    """Each canonical ``error_kind`` must be reachable via the client.

    Maps a failure scenario to the expected ``MediaPostError.kind``.
    Smoke is a single mock HTTP response (or transport exception).
    Retries are disabled so each test resolves in milliseconds.
    """

    def _call(self, mock_client) -> Any:
        client = MediaPostVlmClient(api_key="sk-test", max_retries=0)
        return _run(
            client.qwen_plus_call([{"role": "user", "content": "x"}])
        )

    def test_network_500(self, mock_httpx) -> None:
        _, mock_client = mock_httpx
        mock_client.responses = [_MockResponse(503, text="bad gateway")]
        with pytest.raises(MediaPostError) as exc_info:
            self._call(mock_client)
        assert exc_info.value.kind == "network"

    def test_timeout_transport_error(self, mock_httpx) -> None:
        mod, mock_client = mock_httpx
        mock_client.responses = [mod.TimeoutException("read timeout")]
        with pytest.raises(MediaPostError) as exc_info:
            self._call(mock_client)
        assert exc_info.value.kind == "timeout"

    def test_auth_401(self, mock_httpx) -> None:
        _, mock_client = mock_httpx
        mock_client.responses = [_MockResponse(401, text="invalid key")]
        with pytest.raises(MediaPostError) as exc_info:
            self._call(mock_client)
        assert exc_info.value.kind == "auth"

    def test_quota_429(self, mock_httpx) -> None:
        _, mock_client = mock_httpx
        mock_client.responses = [_MockResponse(429, text="rate limited")]
        with pytest.raises(MediaPostError) as exc_info:
            self._call(mock_client)
        assert exc_info.value.kind == "quota"

    def test_moderation_data_inspection(self, mock_httpx) -> None:
        _, mock_client = mock_httpx
        mock_client.responses = [
            _MockResponse(400, text='"code":"data_inspection_failed"')
        ]
        with pytest.raises(MediaPostError) as exc_info:
            self._call(mock_client)
        assert exc_info.value.kind == "moderation"

    def test_dependency_no_api_key(self, mock_httpx) -> None:
        # ``dependency`` covers missing ffmpeg / Playwright at the
        # mode-module layer; the client itself surfaces missing API key
        # as ``auth``. Confirm the dependency kind exists in the
        # canonical taxonomy so consuming code can still raise it.
        assert "dependency" in ALLOWED_ERROR_KINDS

    def test_format_404(self, mock_httpx) -> None:
        _, mock_client = mock_httpx
        mock_client.responses = [_MockResponse(404, text="not found")]
        with pytest.raises(MediaPostError) as exc_info:
            self._call(mock_client)
        assert exc_info.value.kind == "format"

    def test_duration_kind_in_taxonomy(self) -> None:
        # ``duration`` is raised by the recompose mode (Phase 3), not
        # by the VLM client itself. Confirm the taxonomy includes it
        # so MediaPostError(kind="duration") is accepted.
        err = MediaPostError("duration", "30 min cap exceeded")
        assert err.kind == "duration"

    def test_unknown_unexpected_transport(self, mock_httpx) -> None:
        mod, mock_client = mock_httpx
        mock_client.responses = [mod.HTTPError("???")]
        with pytest.raises(MediaPostError) as exc_info:
            self._call(mock_client)
        # An unrecognised httpx.HTTPError without Connect/Network/Timeout
        # in its name maps to the ``unknown`` kind.
        assert exc_info.value.kind == "unknown"


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_close_aclose_underlying_client(self, mock_httpx) -> None:
        _, mock_client = mock_httpx
        client = MediaPostVlmClient(api_key="sk-test", max_retries=0)
        mock_client.responses = [_qwen_plus_response("ok")]
        _run(client.qwen_plus_call([{"role": "user", "content": "hi"}]))
        _run(client.close())
        assert mock_client.closed is True

    def test_update_api_key_replaces_credential(self) -> None:
        client = MediaPostVlmClient(api_key="old", max_retries=0)
        client.update_api_key("new")
        assert client._api_key == "new"
