"""Unit tests for the hook ``match`` predicate (commit B).

Covers:
- pass-through when no match is set
- predicate filters callback when returning False
- predicate exception treated as no-match and recorded
- async dispatch path (HookRegistry.dispatch)
- sync dispatch path (HookRegistry.dispatch_sync)
- wrapped callback path (e.g. builtins / objects without __dict__)
- ACL: matched callbacks still receive correct kwargs
"""

from __future__ import annotations

import pytest

from openakita.plugins.hooks import HookRegistry
from openakita.plugins.sandbox import PluginErrorTracker


# ---------- helpers ----------


def _calls() -> list[dict]:
    return []


def _make_cb(sink: list[dict], tag: str):
    def cb(**kwargs):
        sink.append({"tag": tag, **kwargs})
        return tag

    return cb


# ---------- async dispatch ----------


@pytest.mark.asyncio
async def test_async_no_match_runs_all():
    reg = HookRegistry()
    sink = _calls()
    reg.register("on_message_received", _make_cb(sink, "a"), plugin_id="p1")
    reg.register("on_message_received", _make_cb(sink, "b"), plugin_id="p2")

    results = await reg.dispatch("on_message_received", channel="wecom", text="hi")

    assert sorted(results) == ["a", "b"]
    assert {c["tag"] for c in sink} == {"a", "b"}
    for c in sink:
        assert c["channel"] == "wecom"
        assert c["text"] == "hi"


@pytest.mark.asyncio
async def test_async_match_filters_callback():
    reg = HookRegistry()
    sink = _calls()
    reg.register(
        "on_message_received",
        _make_cb(sink, "wecom-only"),
        plugin_id="p1",
        match=lambda **kw: kw.get("channel") == "wecom",
    )
    reg.register(
        "on_message_received",
        _make_cb(sink, "no-filter"),
        plugin_id="p2",
    )

    res1 = await reg.dispatch("on_message_received", channel="wecom", text="hi")
    assert sorted(res1) == ["no-filter", "wecom-only"]

    sink.clear()
    res2 = await reg.dispatch("on_message_received", channel="feishu", text="hi")
    assert res2 == ["no-filter"]
    assert [c["tag"] for c in sink] == ["no-filter"]


@pytest.mark.asyncio
async def test_async_match_predicate_raising_is_treated_as_no_match():
    tracker = PluginErrorTracker()
    reg = HookRegistry(error_tracker=tracker)
    sink = _calls()

    def boom(**kwargs):
        raise RuntimeError("predicate boom")

    reg.register(
        "on_message_received",
        _make_cb(sink, "p1"),
        plugin_id="p1",
        match=boom,
    )
    reg.register(
        "on_message_received",
        _make_cb(sink, "p2"),
        plugin_id="p2",
    )

    results = await reg.dispatch("on_message_received", channel="wecom")
    assert results == ["p2"], "p1 must be skipped, p2 must still run"

    errs = tracker.get_errors("p1")
    assert any("hook:on_message_received:match" in e["context"] for e in errs)
    assert tracker.health_snapshot("p1")["exception_count"] >= 1
    assert tracker.health_snapshot("p2")["exception_count"] == 0


@pytest.mark.asyncio
async def test_async_match_returning_truthy_runs_callback():
    reg = HookRegistry()
    sink = _calls()
    reg.register(
        "on_init",
        _make_cb(sink, "a"),
        plugin_id="p1",
        match=lambda **kw: 1,  # truthy non-bool
    )
    results = await reg.dispatch("on_init")
    assert results == ["a"]


# ---------- sync dispatch ----------


def test_sync_no_match_runs_all():
    reg = HookRegistry()
    sink = _calls()
    reg.register("on_prompt_build", _make_cb(sink, "a"), plugin_id="p1")
    reg.register("on_prompt_build", _make_cb(sink, "b"), plugin_id="p2")

    results = reg.dispatch_sync("on_prompt_build", phase="system")
    assert sorted(results) == ["a", "b"]


def test_sync_match_filters_callback():
    reg = HookRegistry()
    sink = _calls()
    reg.register(
        "on_prompt_build",
        _make_cb(sink, "system-only"),
        plugin_id="p1",
        match=lambda **kw: kw.get("phase") == "system",
    )
    reg.register(
        "on_prompt_build",
        _make_cb(sink, "always"),
        plugin_id="p2",
    )

    res1 = reg.dispatch_sync("on_prompt_build", phase="system")
    assert sorted(res1) == ["always", "system-only"]

    sink.clear()
    res2 = reg.dispatch_sync("on_prompt_build", phase="user")
    assert res2 == ["always"]


def test_sync_match_predicate_raising_is_treated_as_no_match():
    tracker = PluginErrorTracker()
    reg = HookRegistry(error_tracker=tracker)
    sink = _calls()

    def boom(**kwargs):
        raise ValueError("nope")

    reg.register(
        "on_prompt_build",
        _make_cb(sink, "p1"),
        plugin_id="p1",
        match=boom,
    )
    reg.register(
        "on_prompt_build",
        _make_cb(sink, "p2"),
        plugin_id="p2",
    )

    results = reg.dispatch_sync("on_prompt_build", phase="system")
    assert results == ["p2"]
    errs = tracker.get_errors("p1")
    assert any("hook:on_prompt_build:match" in e["context"] for e in errs)


# ---------- wrapped callback path ----------


class _BuiltinLike:
    """Callable whose attribute set raises AttributeError, forcing _wrap_callback."""

    __slots__ = ("_fn",)  # no __dict__ -> setattr arbitrary names raises

    def __init__(self, fn):
        object.__setattr__(self, "_fn", fn)

    def __call__(self, **kwargs):
        return object.__getattribute__(self, "_fn")(**kwargs)


def test_wrapped_callback_match_predicate_works():
    reg = HookRegistry()
    sink = _calls()

    inner = _make_cb(sink, "wrapped")
    builtin_like = _BuiltinLike(inner)

    # Sanity: confirm the helper actually rejects arbitrary setattr,
    # which is the exact condition that triggers _wrap_callback.
    with pytest.raises(AttributeError):
        builtin_like.__plugin_id__ = "x"  # type: ignore[attr-defined]

    reg.register(
        "on_init",
        builtin_like,
        plugin_id="p1",
        match=lambda **kw: kw.get("ok") is True,
    )

    callbacks = reg.get_hooks("on_init")
    assert len(callbacks) == 1
    assert callbacks[0] is not builtin_like, "must have been wrapped"
    assert getattr(callbacks[0], "__hook_match__", None) is not None

    res_skip = reg.dispatch_sync("on_init", ok=False)
    assert res_skip == []
    assert sink == []

    res_run = reg.dispatch_sync("on_init", ok=True)
    assert res_run == ["wrapped"]
    assert sink and sink[0]["tag"] == "wrapped"


def test_register_attaches_match_attribute_directly():
    reg = HookRegistry()

    def cb(**kwargs):
        return None

    pred = lambda **kw: True  # noqa: E731
    reg.register("on_init", cb, plugin_id="p1", match=pred)

    callbacks = reg.get_hooks("on_init")
    assert len(callbacks) == 1
    assert getattr(callbacks[0], "__hook_match__", None) is pred


def test_register_without_match_has_none_attribute():
    reg = HookRegistry()

    def cb(**kwargs):
        return None

    reg.register("on_init", cb, plugin_id="p1")
    callbacks = reg.get_hooks("on_init")
    assert getattr(callbacks[0], "__hook_match__", None) is None


# ---------- backward compat ----------


@pytest.mark.asyncio
async def test_old_callers_without_match_kwarg_still_work():
    """Existing plugins that don't pass match=... must keep working."""
    reg = HookRegistry()
    sink = _calls()
    reg.register("on_init", _make_cb(sink, "a"), plugin_id="p1")
    reg.register("on_init", _make_cb(sink, "b"), plugin_id="p2")
    results = await reg.dispatch("on_init")
    assert sorted(results) == ["a", "b"]
