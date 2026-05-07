"""DispatchService red-line tests.

We stub :meth:`PluginAPI.send_message` so unit tests don't need a live
gateway. The dispatch service is the **only** place the plugin calls
out to an IM adapter — locking its behaviour down matters:

* Empty content is accepted and marked ``skipped="empty"``.
* Long content is split by :mod:`finpulse_notification.splitter`.
* Per-key cooldown drops duplicates; content-based dedupe does the
  same without an explicit key.
* An adapter exception on one chunk does **not** abort the batch.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from finpulse_dispatch import DispatchResult, DispatchService


class _StubAPI:
    """Captures every ``send_message`` call for inspection."""

    def __init__(self, *, fail_chunk_indices: tuple[int, ...] = ()) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self.calls = 0
        self._fail_idx = set(fail_chunk_indices)

    def send_message(self, *, channel: str, chat_id: str, text: str) -> None:
        idx = self.calls
        self.calls += 1
        if idx in self._fail_idx:
            raise RuntimeError(f"simulated adapter failure @chunk {idx}")
        self.sent.append((channel, chat_id, text))


def _run(coro):
    return asyncio.run(coro)


def _ds(api: _StubAPI, **kw) -> DispatchService:
    return DispatchService(api, inter_chunk_delay=0.0, **kw)


# ── Basic send paths ─────────────────────────────────────────────────


def test_empty_content_is_no_op() -> None:
    api = _StubAPI()
    ds = _ds(api)
    res = _run(ds.send(channel="feishu", chat_id="u1", content=""))
    assert res.ok is True
    assert res.skipped == "empty"
    assert api.sent == []


def test_short_content_sends_single_chunk() -> None:
    api = _StubAPI()
    ds = _ds(api)
    res = _run(ds.send(channel="feishu", chat_id="u1", content="hello\n"))
    assert res.ok is True
    assert res.sent_chunks == 1
    assert len(api.sent) == 1
    assert api.sent[0][0] == "feishu"
    assert api.sent[0][1] == "u1"
    assert "hello" in api.sent[0][2]


def test_long_content_splits_across_chunks() -> None:
    api = _StubAPI()
    ds = _ds(api, batch_bytes={"feishu": 80})
    content = "\n".join(f"line-{i:02d}" for i in range(30)) + "\n"
    res = _run(ds.send(channel="feishu", chat_id="u1", content=content))
    assert res.ok is True
    assert res.sent_chunks >= 2
    assert len(api.sent) == res.sent_chunks
    combined = "\n".join(text for _, _, text in api.sent)
    # Every line should survive the split
    for i in range(30):
        assert f"line-{i:02d}" in combined


# ── Cooldown paths ──────────────────────────────────────────────────


def test_cooldown_key_suppresses_repeat_send() -> None:
    api = _StubAPI()
    ds = _ds(api)
    first = _run(
        ds.send(
            channel="feishu",
            chat_id="u1",
            content="hello\n",
            cooldown_key="daily:morning:2026-04-24",
            cooldown_s=60,
        )
    )
    assert first.ok is True
    assert first.sent_chunks == 1
    second = _run(
        ds.send(
            channel="feishu",
            chat_id="u1",
            content="hello\n",
            cooldown_key="daily:morning:2026-04-24",
            cooldown_s=60,
        )
    )
    assert second.skipped == "cooldown"
    assert second.sent_chunks == 0
    assert len(api.sent) == 1


def test_content_dedupe_suppresses_same_payload() -> None:
    api = _StubAPI()
    ds = _ds(api)
    _run(
        ds.send(
            channel="feishu",
            chat_id="u1",
            content="exact same content\n",
            cooldown_s=30,
            dedupe_by_content=True,
        )
    )
    res = _run(
        ds.send(
            channel="feishu",
            chat_id="u1",
            content="exact same content\n",
            cooldown_s=30,
            dedupe_by_content=True,
        )
    )
    assert res.skipped == "cooldown"
    assert len(api.sent) == 1


def test_expired_cooldown_allows_resend() -> None:
    api = _StubAPI()
    ds = _ds(api)
    _run(
        ds.send(
            channel="feishu",
            chat_id="u1",
            content="hi\n",
            cooldown_key="k1",
            cooldown_s=0.01,
        )
    )
    time.sleep(0.02)
    res = _run(
        ds.send(
            channel="feishu",
            chat_id="u1",
            content="hi\n",
            cooldown_key="k1",
            cooldown_s=0.01,
        )
    )
    assert res.ok is True
    assert res.skipped is None
    assert len(api.sent) == 2


def test_clear_cooldown_wipes_state() -> None:
    api = _StubAPI()
    ds = _ds(api)
    _run(
        ds.send(
            channel="feishu",
            chat_id="u1",
            content="hi\n",
            cooldown_key="k1",
            cooldown_s=60,
        )
    )
    assert ds.cooldown_snapshot().get("k1") is not None
    ds.clear_cooldown("k1")
    assert ds.cooldown_snapshot().get("k1") is None
    res = _run(
        ds.send(
            channel="feishu",
            chat_id="u1",
            content="hi\n",
            cooldown_key="k1",
            cooldown_s=60,
        )
    )
    assert res.ok is True
    assert len(api.sent) == 2


# ── Failure paths ───────────────────────────────────────────────────


def test_partial_adapter_failure_keeps_remaining_chunks() -> None:
    # Fail the first chunk only — subsequent chunks should still go.
    api = _StubAPI(fail_chunk_indices=(0,))
    ds = _ds(api, batch_bytes={"feishu": 80})
    content = "\n".join(f"line-{i:02d}" for i in range(30)) + "\n"
    res = _run(ds.send(channel="feishu", chat_id="u1", content=content))
    assert res.sent_chunks < res.sent_chunks + len(res.errors)  # errors captured
    assert res.errors, "expected at least one recorded adapter error"
    assert len(api.sent) >= 1


def test_full_failure_does_not_update_cooldown() -> None:
    # Force every send to fail.
    api = _StubAPI(fail_chunk_indices=tuple(range(100)))
    ds = _ds(api)
    res = _run(
        ds.send(
            channel="feishu",
            chat_id="u1",
            content="hello\n",
            cooldown_key="k1",
            cooldown_s=60,
        )
    )
    assert res.ok is False
    assert "k1" not in ds.cooldown_snapshot(), (
        "cooldown must not be stamped when no chunks were delivered"
    )


# ── broadcast() ─────────────────────────────────────────────────────


def test_broadcast_preserves_target_order() -> None:
    api = _StubAPI()
    ds = _ds(api)
    targets = [
        {"channel": "feishu", "chat_id": "u1"},
        {"channel": "dingtalk", "chat_id": "u2"},
        {"channel": "telegram", "chat_id": "u3"},
    ]
    results = _run(ds.broadcast(targets=targets, content="hi\n"))
    assert [r.channel for r in results] == ["feishu", "dingtalk", "telegram"]
    assert all(isinstance(r, DispatchResult) for r in results)
    assert [s[0] for s in api.sent] == ["feishu", "dingtalk", "telegram"]


def test_broadcast_skips_entries_missing_target_fields() -> None:
    api = _StubAPI()
    ds = _ds(api)
    targets = [
        {"channel": "feishu", "chat_id": ""},
        {"channel": "", "chat_id": "u1"},
        {"channel": "feishu", "chat_id": "u2"},
    ]
    results = _run(ds.broadcast(targets=targets, content="hi\n"))
    assert results[0].errors == ["missing_target"]
    assert results[1].errors == ["missing_target"]
    assert results[2].ok is True
    assert len(api.sent) == 1


# ── PDF caption / fast-fail paths ────────────────────────────────────


class _StubAdapter:
    """Stand-in for an IM adapter exposing ``send_file`` + ``send_text``.

    ``send_file_caption_failure`` simulates the legacy "caption text
    sent before the file body" trap that some real adapters (notably
    WeChat iLink) used to surface as a misleading "PDF send failed".
    With caption decoupling the file dispatch must not be aborted by
    a caption argument.
    """

    channel_name = "stub"

    def __init__(
        self,
        *,
        send_file_caption_failure: bool = False,
        text_failure: Exception | None = None,
        text_caption_failure: Exception | None = None,
    ) -> None:
        self.send_file_calls: list[dict[str, str]] = []
        self.send_text_calls: list[tuple[str, str]] = []
        self._send_file_caption_failure = send_file_caption_failure
        self._text_failure = text_failure
        self._text_caption_failure = text_caption_failure

    async def send_file(
        self, chat_id: str, file_path: str, caption: str = ""
    ) -> str:
        if caption and self._send_file_caption_failure:
            raise RuntimeError(
                "WeChat sendmessage(text) failed: ret=-2, errcode=None, errmsg="
            )
        self.send_file_calls.append(
            {"chat_id": chat_id, "file_path": file_path, "caption": caption}
        )
        return "file-ok"

    async def send_text(self, chat_id: str, text: str) -> str:
        # When the file dispatch already landed and we are sending a
        # follow-up caption, simulate a permission failure if requested.
        if self.send_file_calls and self._text_caption_failure is not None:
            raise self._text_caption_failure
        if self._text_failure is not None:
            raise self._text_failure
        self.send_text_calls.append((chat_id, text))
        return "text-ok"


class _StubGateway:
    def __init__(self, adapter: _StubAdapter) -> None:
        self._adapter = adapter

    def get_adapter(self, channel: str) -> _StubAdapter:  # noqa: ARG002
        return self._adapter


class _StubAPIWithAdapter(_StubAPI):
    def __init__(self, adapter: _StubAdapter, **kw) -> None:
        super().__init__(**kw)
        self._host = {"gateway": _StubGateway(adapter)}


async def _stub_render(self, html: str, out_path: Path) -> None:  # noqa: ARG001
    out_path.write_bytes(b"%PDF-1.4 stub\n")


def test_pdf_caption_failure_does_not_abort_pdf_dispatch(monkeypatch) -> None:
    """The file body must reach the adapter even if caption send fails.

    Regression: the old code passed ``caption=...`` to ``send_file``
    and let an exception inside the adapter (caption-text-first) be
    reported as ``pdf_file:...``, which made fin-pulse fall back to
    plain text — multiplying the failure cost.
    """

    monkeypatch.setattr(
        DispatchService, "_render_html_to_pdf", _stub_render, raising=True
    )
    adapter = _StubAdapter(
        send_file_caption_failure=True,
        text_caption_failure=RuntimeError(
            "WeChat sendmessage(text) failed: ret=-2, errcode=None, errmsg="
        ),
    )
    api = _StubAPIWithAdapter(adapter)
    ds = _ds(api)
    res = _run(
        ds.send(
            channel="stub",
            chat_id="u1",
            content="<html><body>hi</body></html>",
            content_kind="html",
            file_name="report.pdf",
            fallback_text="plain markdown body",
        )
    )
    assert res.ok is True, "PDF body landed; result must be ok"
    assert res.content_kind == "pdf"
    assert len(adapter.send_file_calls) == 1
    # Caption is NOT passed into send_file any more — it travels as a
    # separate, best-effort follow-up via send_text.
    assert adapter.send_file_calls[0]["caption"] == ""
    # Caption follow-up failed, but errors stay empty because we
    # intentionally swallow caption follow-up failures.
    assert res.errors == []


def test_permanent_error_skips_text_fallback(monkeypatch) -> None:
    """A WeChat ret=-2 must short-circuit the PDF→text fallback.

    Re-trying every markdown chunk would just earn another ~75s of
    exponential-backoff per chunk on real WeChat. The hint string
    must be inserted at the head of ``errors`` so the UI surfaces it.
    """

    monkeypatch.setattr(
        DispatchService, "_render_html_to_pdf", _stub_render, raising=True
    )

    class _AlwaysFailAdapter(_StubAdapter):
        async def send_file(self, chat_id, file_path, caption=""):  # noqa: ARG002
            raise RuntimeError(
                "WeChat sendmessage(text) failed: ret=-2, errcode=None, errmsg="
            )

        async def send_text(self, chat_id: str, text: str) -> str:
            self.send_text_calls.append((chat_id, text))
            raise RuntimeError(
                "WeChat sendmessage(text) failed: ret=-2, errcode=None, errmsg="
            )

    adapter = _AlwaysFailAdapter()
    api = _StubAPIWithAdapter(adapter)
    ds = _ds(api)
    res = _run(
        ds.send(
            channel="stub",
            chat_id="u1",
            content="<html><body>hi</body></html>",
            content_kind="html",
            file_name="report.pdf",
            fallback_text="line 1\nline 2\nline 3\n",
        )
    )
    assert res.ok is False
    assert adapter.send_text_calls == [], (
        "permanent error must skip the text fan-out entirely"
    )
    assert res.errors, "expected at least the pdf_file error to be recorded"
    assert res.errors[0].startswith("hint:"), (
        "actionable hint must be the first error so the UI shows it"
    )
    assert any("pdf_file:" in e for e in res.errors)


def test_text_chunk_permanent_error_aborts_remaining_chunks() -> None:
    """A permanent error mid-fan-out must stop subsequent chunks too."""

    class _PermFailAPI:
        def __init__(self) -> None:
            self.calls = 0
            self.sent: list[tuple[str, str, str]] = []
            self._host: dict = {}

        def send_message(self, *, channel: str, chat_id: str, text: str) -> None:
            self.calls += 1
            raise RuntimeError(
                "WeChat sendmessage(text) failed: ret=-2, errcode=None, errmsg="
            )

    api = _PermFailAPI()
    ds = DispatchService(api, inter_chunk_delay=0.0, batch_bytes={"wechat": 50})
    big = "\n".join(f"line-{i:02d}" for i in range(20)) + "\n"
    res = _run(ds.send(channel="wechat", chat_id="u1", content=big))
    assert res.ok is False
    # Should stop after the very first chunk failure rather than calling
    # send_message dozens of times — proves the fast-fail.
    assert api.calls == 1
    assert res.errors[0].startswith("hint:")
