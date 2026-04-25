"""Phase 5 — unit tests for the shared query service.

Covers three contracts the Agent-tools handler depends on:

* :func:`_clamp` (and its float twin) keeps missing / non-numeric /
  out-of-range values snapping to
  ``default`` / ``lo`` / ``hi`` instead of raising.
* Every service function returns a plain dict with ``ok`` and either a
  typed payload or an ``error`` envelope; no function raises on a bad
  LLM payload.
* :func:`build_tool_dispatch` maps the 7 V1.0 tool names to coroutines
  and the serialiser round-trips into valid JSON.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from finpulse_services import query as query_svc
from finpulse_services.query import (
    _clamp,
    _clamp_float,
    build_tool_dispatch,
    serialize_tool_result,
)
from finpulse_task_manager import FinpulseTaskManager


def _run(coro):
    return asyncio.run(coro)


# ── Clamp helpers ────────────────────────────────────────────────────


class TestClamp:
    def test_none_returns_default(self) -> None:
        assert _clamp(None, 1, 100, 50) == 50

    def test_non_numeric_returns_default(self) -> None:
        assert _clamp("abc", 1, 100, 50) == 50
        assert _clamp({"x": 1}, 1, 100, 50) == 50

    def test_below_lo_snaps_to_lo(self) -> None:
        assert _clamp(0, 1, 100, 50) == 1
        assert _clamp(-999, 1, 100, 50) == 1

    def test_above_hi_snaps_to_hi(self) -> None:
        assert _clamp(99999, 1, 200, 50) == 200

    def test_in_range_preserved(self) -> None:
        assert _clamp(42, 1, 200, 50) == 42

    def test_string_numeric_coerced(self) -> None:
        assert _clamp("17", 1, 200, 50) == 17

    def test_float_truncated(self) -> None:
        # int("7.5") would raise; _clamp falls back to default.
        assert _clamp("7.5", 1, 200, 50) == 50


class TestClampFloat:
    def test_none_returns_default(self) -> None:
        assert _clamp_float(None, 0.0, 10.0, 3.0) == 3.0

    def test_none_default_preserved(self) -> None:
        assert _clamp_float(None, 0.0, 10.0, None) is None

    def test_in_range(self) -> None:
        assert _clamp_float(5.5, 0.0, 10.0, 0.0) == pytest.approx(5.5)

    def test_clamped(self) -> None:
        assert _clamp_float(99, 0.0, 10.0, 0.0) == 10.0
        assert _clamp_float(-5, 0.0, 10.0, 0.0) == 0.0


# ── Fixture ──────────────────────────────────────────────────────────


async def _make_tm(tmp_path: Path) -> FinpulseTaskManager:
    tm = FinpulseTaskManager(tmp_path / "fin_pulse.sqlite")
    await tm.init()
    return tm


# ── get_settings / set_settings ──────────────────────────────────────


def test_get_settings_redacts_secrets(tmp_path: Path) -> None:
    async def _body() -> None:
        tm = await _make_tm(tmp_path)
        try:
            await tm.set_configs(
                {"brain_api_key": "sk-super-secret", "feishu_webhook": "https://hook"}
            )
            res = await query_svc.get_settings(tm=tm)
            assert res["ok"] is True
            assert res["config"]["brain_api_key"] == "***"
            assert res["config"]["feishu_webhook"] == "***"
            # Non-secret entries must stay visible.
            assert "ai_interests" in res["config"]
            assert res["config"]["ai_interests"] != "***"
        finally:
            await tm.close()

    _run(_body())


def test_set_settings_rejects_empty(tmp_path: Path) -> None:
    async def _body() -> None:
        tm = await _make_tm(tmp_path)
        try:
            assert (await query_svc.set_settings(tm=tm, args={}))["ok"] is False
            assert (
                await query_svc.set_settings(tm=tm, args={"updates": {}})
            )["ok"] is False
            res = await query_svc.set_settings(
                tm=tm, args={"updates": {"brain_model": "gpt-4.1-mini"}}
            )
            assert res["ok"] is True
            assert res["applied"] == ["brain_model"]
            assert await tm.get_config("brain_model") == "gpt-4.1-mini"
        finally:
            await tm.close()

    _run(_body())


def test_set_settings_coerces_non_string_values(tmp_path: Path) -> None:
    async def _body() -> None:
        tm = await _make_tm(tmp_path)
        try:
            res = await query_svc.set_settings(
                tm=tm, args={"updates": {"digest_top_k": 30, "enable_x": True}}
            )
            assert res["ok"] is True
            assert await tm.get_config("digest_top_k") == "30"
            assert await tm.get_config("enable_x") == "True"
        finally:
            await tm.close()

    _run(_body())


# ── list_tasks / get_status / cancel_task ────────────────────────────


def test_list_and_status_roundtrip(tmp_path: Path) -> None:
    async def _body() -> None:
        tm = await _make_tm(tmp_path)
        try:
            t1 = await tm.create_task(mode="ingest", params={"n": 1})
            t2 = await tm.create_task(mode="daily_brief", params={"session": "morning"})

            listed = await query_svc.list_tasks(tm=tm, args={"limit": 10})
            assert listed["ok"] is True
            ids = {row["id"] for row in listed["items"]}
            assert t1["id"] in ids and t2["id"] in ids

            mode_only = await query_svc.list_tasks(tm=tm, args={"mode": "daily_brief"})
            assert {r["id"] for r in mode_only["items"]} == {t2["id"]}

            # Limit is clamped — giant request should not be rejected.
            big = await query_svc.list_tasks(tm=tm, args={"limit": 999_999})
            assert big["limit"] == 200

            got = await query_svc.get_status(tm=tm, args={"task_id": t1["id"]})
            assert got["ok"] is True and got["task"]["id"] == t1["id"]

            missing = await query_svc.get_status(tm=tm, args={"task_id": "does-not-exist"})
            assert missing["ok"] is False and missing["error"] == "not_found"

            cancelled = await query_svc.cancel_task(tm=tm, args={"task_id": t1["id"]})
            assert cancelled["ok"] is True
            row = await tm.get_task(t1["id"])
            assert row and row["status"] == "canceled"
        finally:
            await tm.close()

    _run(_body())


def test_cancel_requires_task_id(tmp_path: Path) -> None:
    async def _body() -> None:
        tm = await _make_tm(tmp_path)
        try:
            res = await query_svc.cancel_task(tm=tm, args={})
            assert res["ok"] is False and "task_id" in res["error"]
        finally:
            await tm.close()

    _run(_body())


# ── search_news ──────────────────────────────────────────────────────


def _seed_article(tm: FinpulseTaskManager, **overrides: Any) -> dict[str, Any]:
    """Insert a canned article with a stable url_hash. Helpers like
    :func:`tm.upsert_article` would be equally valid but we'd have to
    import the normalizer — this direct insert keeps the test self
    contained.
    """

    import hashlib
    import json as _json
    import uuid

    base = {
        "id": str(uuid.uuid4()),
        "source_id": "wallstreetcn",
        "url": f"https://example.com/{uuid.uuid4()}",
        "title": "Sample headline",
        "summary": "Sample summary",
        "content": None,
        "published_at": None,
        "ai_score": None,
        "raw": {"note": "seed"},
    }
    base.update(overrides)
    base["url_hash"] = hashlib.sha256(base["url"].encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    base.setdefault("fetched_at", now)
    base.setdefault("published_at", now)

    async def _insert() -> None:
        await tm._db.execute(  # type: ignore[union-attr]
            (
                "INSERT INTO articles (id, source_id, url, url_hash, title, summary, "
                "content, published_at, fetched_at, raw_json, ai_score) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            ),
            (
                base["id"],
                base["source_id"],
                base["url"],
                base["url_hash"],
                base["title"],
                base["summary"],
                base.get("content"),
                base.get("published_at"),
                base["fetched_at"],
                _json.dumps(base.get("raw") or {}, ensure_ascii=False),
                base.get("ai_score"),
            ),
        )
        await tm._db.commit()  # type: ignore[union-attr]

    return base, _insert


def test_search_news_clamps_days_and_limit(tmp_path: Path) -> None:
    async def _body() -> None:
        tm = await _make_tm(tmp_path)
        try:
            for _ in range(3):
                _, insert = _seed_article(tm)
                await insert()

            # Huge `days` + huge `limit` + bogus `min_score` all clamp silently.
            res = await query_svc.search_news(
                tm=tm,
                args={"days": 10_000, "limit": 10_000, "min_score": "oops"},
            )
            assert res["ok"] is True
            assert res["limit"] == 200
            assert res["window"]["days"] == 90
            assert len(res["items"]) == 3

            filtered = await query_svc.search_news(
                tm=tm, args={"source_id": "no-such-source", "limit": 5}
            )
            assert filtered["total"] == 0
        finally:
            await tm.close()

    _run(_body())


def test_search_news_keyword_filter(tmp_path: Path) -> None:
    async def _body() -> None:
        tm = await _make_tm(tmp_path)
        try:
            _, ins_a = _seed_article(tm, title="Fed holds rates steady")
            _, ins_b = _seed_article(tm, title="Iron ore prices rally")
            await ins_a()
            await ins_b()
            res = await query_svc.search_news(tm=tm, args={"q": "Fed"})
            assert res["total"] == 1
            assert res["items"][0]["title"].startswith("Fed")
        finally:
            await tm.close()

    _run(_body())


# ── create_task validation ───────────────────────────────────────────


class _StubPipeline:
    """Minimal stand-in — records the call so we can assert on
    dispatch, but never reaches out to fetchers / LLM.
    """

    def __init__(self) -> None:
        self.ingest_calls: list[dict[str, Any]] = []
        self.brief_calls: list[dict[str, Any]] = []
        self.radar_calls: list[dict[str, Any]] = []

    async def ingest(
        self, *, sources: Any, since_hours: int, task_id: str
    ) -> dict[str, Any]:
        self.ingest_calls.append(
            {"sources": sources, "since_hours": since_hours, "task_id": task_id}
        )
        return {"received": 0}

    async def run_daily_brief(
        self,
        *,
        session: str,
        since_hours: int,
        top_k: int,
        lang: str,
        task_id: str,
    ) -> dict[str, Any]:
        self.brief_calls.append(
            {
                "session": session,
                "since_hours": since_hours,
                "top_k": top_k,
                "lang": lang,
                "task_id": task_id,
            }
        )
        return {"digest_id": "fake-digest", "session": session}

    async def run_hot_radar(
        self,
        dispatch: Any,
        *,
        rules_text: str,
        targets: list[dict[str, str]],
        since_hours: int,
        limit: int,
        min_score: float | None,
        title: str | None,
        cooldown_s: float,
        task_id: str,
    ) -> dict[str, Any]:
        self.radar_calls.append(
            {
                "rules_text": rules_text,
                "targets": targets,
                "since_hours": since_hours,
                "limit": limit,
                "min_score": min_score,
                "title": title,
                "cooldown_s": cooldown_s,
                "task_id": task_id,
            }
        )
        return {"hits": 0, "dispatched": []}


def test_create_rejects_bad_mode(tmp_path: Path) -> None:
    async def _body() -> None:
        tm = await _make_tm(tmp_path)
        try:
            res = await query_svc.create_task(
                tm=tm, pipeline=_StubPipeline(), dispatch=object(), args={"mode": "bogus"}
            )
            assert res["ok"] is False
            assert res["error"] == "invalid_mode"
        finally:
            await tm.close()

    _run(_body())


def test_create_ingest_runs_pipeline(tmp_path: Path) -> None:
    async def _body() -> None:
        tm = await _make_tm(tmp_path)
        pipe = _StubPipeline()
        try:
            res = await query_svc.create_task(
                tm=tm,
                pipeline=pipe,
                dispatch=None,
                args={"mode": "ingest", "since_hours": 200},  # clamps to 72
            )
            assert res["ok"] is True
            assert len(pipe.ingest_calls) == 1
            assert pipe.ingest_calls[0]["since_hours"] == 72
        finally:
            await tm.close()

    _run(_body())


def test_create_daily_brief_validates_session(tmp_path: Path) -> None:
    async def _body() -> None:
        tm = await _make_tm(tmp_path)
        try:
            res = await query_svc.create_task(
                tm=tm,
                pipeline=_StubPipeline(),
                dispatch=None,
                args={"mode": "daily_brief", "session": "midnight"},
            )
            assert res["ok"] is False
            assert "session" in res["error"]
        finally:
            await tm.close()

    _run(_body())


def test_create_hot_radar_requires_targets(tmp_path: Path) -> None:
    async def _body() -> None:
        tm = await _make_tm(tmp_path)
        pipe = _StubPipeline()
        try:
            # Missing targets.
            bad = await query_svc.create_task(
                tm=tm,
                pipeline=pipe,
                dispatch=object(),
                args={"mode": "hot_radar", "rules_text": "+fed"},
            )
            assert bad["ok"] is False

            # Good path.
            ok = await query_svc.create_task(
                tm=tm,
                pipeline=pipe,
                dispatch=object(),
                args={
                    "mode": "hot_radar",
                    "rules_text": "+fed",
                    "targets": [{"channel": "feishu", "chat_id": "oc_xxx"}],
                    "since_hours": 9999,  # clamps to 168
                },
            )
            assert ok["ok"] is True
            assert len(pipe.radar_calls) == 1
            assert pipe.radar_calls[0]["since_hours"] == 168
        finally:
            await tm.close()

    _run(_body())


def test_create_hot_radar_requires_dispatch(tmp_path: Path) -> None:
    async def _body() -> None:
        tm = await _make_tm(tmp_path)
        try:
            res = await query_svc.create_task(
                tm=tm,
                pipeline=_StubPipeline(),
                dispatch=None,
                args={
                    "mode": "hot_radar",
                    "rules_text": "+fed",
                    "targets": [{"channel": "feishu", "chat_id": "oc_xxx"}],
                },
            )
            assert res["ok"] is False
            assert res["error"] == "dispatch_unavailable"
        finally:
            await tm.close()

    _run(_body())


# ── Dispatch table / serialisation ────────────────────────────────────


def test_build_tool_dispatch_covers_seven_tools(tmp_path: Path) -> None:
    async def _body() -> None:
        tm = await _make_tm(tmp_path)
        try:
            table = build_tool_dispatch(tm=tm, pipeline=None, dispatch=None)
            expected = {
                "fin_pulse_create",
                "fin_pulse_cancel",
                "fin_pulse_status",
                "fin_pulse_list",
                "fin_pulse_settings_get",
                "fin_pulse_settings_set",
                "fin_pulse_search_news",
            }
            assert set(table.keys()) == expected
            # get_settings path works with no pipeline/dispatch injected.
            resp = await table["fin_pulse_settings_get"]({})
            assert resp["ok"] is True
        finally:
            await tm.close()

    _run(_body())


def test_serialize_tool_result_roundtrips() -> None:
    raw = {"ok": True, "items": [1, 2, {"title": "hi"}]}
    encoded = serialize_tool_result(raw)
    parsed = json.loads(encoded)
    assert parsed == raw


def test_serialize_tool_result_handles_unencodable() -> None:
    class Weird:
        pass

    encoded = serialize_tool_result({"ok": True, "weird": Weird()})
    parsed = json.loads(encoded)
    # Falls back to ``str()`` via default=str, so the nested object
    # becomes a ``"<...Weird object at ...>"`` string rather than
    # exploding.
    assert parsed["ok"] is True
    assert isinstance(parsed["weird"], str)
