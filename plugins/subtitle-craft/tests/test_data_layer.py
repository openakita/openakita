"""Phase 1 data-layer sanity checks.

Covers the Phase 1 DoD from ``docs/subtitle-craft-plan.md`` §11 and the
**Gate 1** invariants from the master execution plan:

- ``ERROR_HINTS`` is exactly the 9-key clip-sense taxonomy (no ``rate_limit``,
  yes ``duration``).
- ``estimate_cost`` produces sensible breakdowns for all 4 modes.
- ``SubtitleTaskManager`` creates the 4-table schema (with ``tasks.origin_*``
  fields and ``assets_bus`` table reserved for v2.0).
- ``_UPDATABLE_COLUMNS`` whitelist rejects unknown columns.
- ``assets_bus`` is *never* written by the v1.0 layer (init COUNT == 0,
  no public write API).
- ``map_vendor_kind_to_error_kind`` always returns one of the 9 canonical
  keys for every known ``vendor_client.ERROR_KIND_*`` value.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from subtitle_models import (
    ALLOWED_ERROR_KINDS,
    ERROR_HINTS,
    LANGUAGE_NAMES,
    MODES,
    MODES_BY_ID,
    SUBTITLE_STYLES,
    TRANSLATION_MODELS,
    estimate_cost,
    language_name,
    map_vendor_kind_to_error_kind,
)
from subtitle_task_manager import (
    _UPDATABLE_COLUMNS,
    DEFAULT_CONFIG,
    SubtitleTaskManager,
)

# ── ERROR_HINTS taxonomy invariants ───────────────────────────────────────────


def test_error_hints_is_exact_9_keys():
    expected = {
        "network",
        "timeout",
        "auth",
        "quota",
        "moderation",
        "dependency",
        "format",
        "duration",
        "unknown",
    }
    assert set(ERROR_HINTS.keys()) == expected
    assert frozenset(expected) == ALLOWED_ERROR_KINDS
    assert "rate_limit" not in ERROR_HINTS, "P-2: rate_limit must be removed"
    assert "duration" in ERROR_HINTS, "P-2: duration must be present"


def test_error_hints_each_entry_has_required_fields():
    required = {"label_zh", "label_en", "color", "hints_zh", "hints_en"}
    for kind, entry in ERROR_HINTS.items():
        missing = required - set(entry.keys())
        assert not missing, f"ERROR_HINTS[{kind}] missing fields: {missing}"
        assert entry["hints_zh"], f"ERROR_HINTS[{kind}].hints_zh is empty"
        assert entry["hints_en"], f"ERROR_HINTS[{kind}].hints_en is empty"


def test_map_vendor_kind_always_returns_canonical_9():
    for vendor_kind in [
        "network",
        "timeout",
        "rate_limit",
        "auth",
        "not_found",
        "moderation",
        "client",
        "server",
        "unknown",
        "totally-made-up-kind-xyz",
    ]:
        mapped = map_vendor_kind_to_error_kind(vendor_kind)
        assert mapped in ALLOWED_ERROR_KINDS, (
            f"vendor kind {vendor_kind!r} mapped to non-canonical {mapped!r}"
        )

    assert map_vendor_kind_to_error_kind("rate_limit") == "quota"


# ── Modes / styles / translation models ───────────────────────────────────────


def test_modes_exact_5():
    """v1.1: 5 modes (added hook_picker)."""
    assert {m.id for m in MODES} == {
        "auto_subtitle",
        "translate",
        "repair",
        "burn",
        "hook_picker",
    }
    assert MODES_BY_ID["repair"].requires_api_key is False
    assert MODES_BY_ID["burn"].requires_api_key is False
    assert MODES_BY_ID["auto_subtitle"].requires_api_key is True
    assert MODES_BY_ID["translate"].requires_api_key is True
    assert MODES_BY_ID["hook_picker"].requires_api_key is True
    assert MODES_BY_ID["hook_picker"].requires_ffmpeg is False


def test_subtitle_styles_at_least_5_presets():
    assert len(SUBTITLE_STYLES) >= 5
    fs = SUBTITLE_STYLES[0].to_force_style()
    assert "FontName=" in fs and "FontSize=" in fs and "PrimaryColour=" in fs


def test_translation_models_have_pricing():
    ids = {m.id for m in TRANSLATION_MODELS}
    assert {"qwen-mt-flash", "qwen-mt-plus", "qwen-mt-lite"}.issubset(ids)
    for m in TRANSLATION_MODELS:
        assert m.price_cny_per_k_token > 0


def test_language_name_mapping():
    assert language_name("zh") == "Chinese"
    assert language_name("en") == "English"
    assert language_name("ZH") == "Chinese"
    assert language_name("Chinese") == "Chinese"
    assert language_name("") == ""
    assert language_name("xx") == "Xx"


def test_language_names_cover_top_languages():
    assert {"zh", "en", "ja", "ko", "fr", "de", "es", "ru"}.issubset(LANGUAGE_NAMES.keys())


# ── estimate_cost across all 4 modes ──────────────────────────────────────────


def test_estimate_cost_auto_subtitle_basic():
    p = estimate_cost("auto_subtitle", duration_sec=600.0)
    assert p.total_cny == pytest.approx(0.48, abs=0.001)
    assert any(it["api"] == "paraformer-v2" for it in p.items)


def test_estimate_cost_auto_subtitle_with_character_id():
    p = estimate_cost(
        "auto_subtitle",
        duration_sec=600.0,
        character_identify=True,
        speaker_count=3,
    )
    assert any(it["api"] == "qwen-vl-max" for it in p.items)
    assert p.total_cny > 0.48


def test_estimate_cost_translate():
    p = estimate_cost("translate", char_count=10000, translation_model="qwen-mt-flash")
    assert p.total_cny > 0
    assert p.items and p.items[0]["api"] == "qwen-mt-flash"

    p_plus = estimate_cost("translate", char_count=10000, translation_model="qwen-mt-plus")
    assert p_plus.total_cny > p.total_cny


def test_estimate_cost_repair_burn_zero():
    assert estimate_cost("repair").total_cny == 0.0
    assert estimate_cost("burn").total_cny == 0.0


def test_estimate_cost_unknown_mode_returns_empty():
    p = estimate_cost("nonexistent")
    assert p.total_cny == 0.0
    assert p.items == []


# ── SubtitleTaskManager (4-table schema, whitelist, assets_bus invariants) ────


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "subtitle.db"


def _run(coro):
    return asyncio.run(coro)


def test_init_creates_4_tables(db_path: Path):
    async def go():
        tm = SubtitleTaskManager(db_path)
        await tm.init()
        try:
            assert tm._db is not None
            cur = await tm._db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            names = {r["name"] for r in await cur.fetchall()}
            for required in ("tasks", "transcripts", "assets_bus", "config"):
                assert required in names, f"missing table {required}"
        finally:
            await tm.close()

    _run(go())


def test_tasks_origin_columns_present_and_default_null(db_path: Path):
    async def go():
        tm = SubtitleTaskManager(db_path)
        await tm.init()
        try:
            assert tm._db is not None
            cur = await tm._db.execute("PRAGMA table_info(tasks)")
            cols = {r["name"]: r for r in await cur.fetchall()}
            assert "origin_plugin_id" in cols
            assert "origin_task_id" in cols

            t = await tm.create_task(mode="auto_subtitle")
            assert t["origin_plugin_id"] is None
            assert t["origin_task_id"] is None
        finally:
            await tm.close()

    _run(go())


def test_assets_bus_starts_empty_and_v1_never_writes(db_path: Path):
    """v1.0 invariant: no public write API for assets_bus; COUNT stays 0."""

    async def go():
        tm = SubtitleTaskManager(db_path)
        await tm.init()
        try:
            assert await tm.assets_bus_count() == 0
            await tm.create_task(mode="auto_subtitle")
            await tm.create_task(mode="translate")
            assert await tm.assets_bus_count() == 0
        finally:
            await tm.close()

        public_writers = [
            n for n in dir(SubtitleTaskManager) if not n.startswith("_") and "asset" in n.lower()
        ]
        assert public_writers == ["assets_bus_count", "get_asset"], (
            f"v1.0 must expose only read APIs for assets_bus; found: {public_writers}"
        )

    _run(go())


def test_default_config_seeded(db_path: Path):
    async def go():
        tm = SubtitleTaskManager(db_path)
        await tm.init()
        try:
            cfg = await tm.get_all_config()
            for k, v in DEFAULT_CONFIG.items():
                assert k in cfg, f"config key {k} missing"
                assert cfg[k] == v
        finally:
            await tm.close()

    _run(go())


def test_create_get_update_delete_task_roundtrip(db_path: Path):
    async def go():
        tm = SubtitleTaskManager(db_path)
        await tm.init()
        try:
            t = await tm.create_task(
                mode="auto_subtitle",
                source_kind="audio",
                source_path="/tmp/a.wav",
                source_duration_sec=12.5,
                source_lang="zh",
                params={"diarization_enabled": True, "language_hints": ["zh"]},
            )
            tid = t["id"]
            assert t["mode"] == "auto_subtitle"
            assert t["params"] == {
                "diarization_enabled": True,
                "language_hints": ["zh"],
            }

            await tm.update_task(
                tid,
                status="running",
                pipeline_step="step_4_asr_or_load",
            )
            t2 = await tm.get_task(tid)
            assert t2 is not None
            assert t2["status"] == "running"
            assert t2["pipeline_step"] == "step_4_asr_or_load"

            await tm.update_task(
                tid,
                error_kind="quota",
                error_message="429 from DashScope",
                error_hints=ERROR_HINTS["quota"]["hints_zh"],
            )
            t3 = await tm.get_task(tid)
            assert t3 is not None
            assert t3["error_kind"] == "quota"
            assert t3["error_hints"] == ERROR_HINTS["quota"]["hints_zh"]

            assert await tm.delete_task(tid) is True
            assert await tm.get_task(tid) is None
        finally:
            await tm.close()

    _run(go())


def test_update_task_rejects_non_whitelisted_columns(db_path: Path):
    async def go():
        tm = SubtitleTaskManager(db_path)
        await tm.init()
        try:
            t = await tm.create_task(mode="repair")
            with pytest.raises(ValueError, match="not whitelisted"):
                await tm.update_task(t["id"], mode="burn")
            with pytest.raises(ValueError, match="not whitelisted"):
                await tm.update_task(t["id"], created_at="2099-01-01")
            with pytest.raises(ValueError, match="not whitelisted"):
                await tm.update_task(t["id"], origin_plugin_id="clip-sense")
        finally:
            await tm.close()

    _run(go())


def test_update_task_safe_swallows_errors(db_path: Path):
    async def go():
        tm = SubtitleTaskManager(db_path)
        await tm.init()
        try:
            t = await tm.create_task(mode="auto_subtitle")
            await tm.update_task_safe(t["id"], totally_unknown_column="x")
            t2 = await tm.get_task(t["id"])
            assert t2 is not None and t2["status"] == "pending"
        finally:
            await tm.close()

    _run(go())


def test_list_tasks_filter_and_pagination(db_path: Path):
    async def go():
        tm = SubtitleTaskManager(db_path)
        await tm.init()
        try:
            for i in range(5):
                await tm.create_task(mode="translate" if i % 2 else "auto_subtitle")

            page = await tm.list_tasks(limit=3, offset=0)
            assert page["total"] == 5
            assert len(page["tasks"]) == 3

            translate_only = await tm.list_tasks(mode="translate")
            assert translate_only["total"] == 2
            assert all(t["mode"] == "translate" for t in translate_only["tasks"])
        finally:
            await tm.close()

    _run(go())


def test_transcript_cache_by_hash(db_path: Path):
    async def go():
        tm = SubtitleTaskManager(db_path)
        await tm.init()
        try:
            tr = await tm.create_transcript(
                source_hash="abc123_64",
                source_path="/tmp/a.wav",
                source_name="a.wav",
                duration_sec=12.5,
                language="zh",
            )
            await tm.update_transcript(
                tr["id"],
                status="ready",
                full_text="你好世界",
                words=[{"text": "你好", "start_ms": 0, "end_ms": 500}],
                speaker_count=1,
                speaker_map={"SPEAKER_00": "主持人"},
            )

            hit = await tm.get_transcript_by_hash("abc123_64")
            assert hit is not None
            assert hit["status"] == "ready"
            assert hit["words"][0]["text"] == "你好"
            assert hit["speaker_map"] == {"SPEAKER_00": "主持人"}

            miss = await tm.get_transcript_by_hash("nonexistent")
            assert miss is None
        finally:
            await tm.close()

    _run(go())


def test_cooperative_cancel(db_path: Path):
    async def go():
        tm = SubtitleTaskManager(db_path)
        await tm.init()
        try:
            t = await tm.create_task(mode="auto_subtitle")
            assert tm.is_canceled(t["id"]) is False
            tm.request_cancel(t["id"])
            assert tm.is_canceled(t["id"]) is True
            tm.clear_cancel(t["id"])
            assert tm.is_canceled(t["id"]) is False
        finally:
            await tm.close()

    _run(go())


def test_set_config_and_get(db_path: Path):
    async def go():
        tm = SubtitleTaskManager(db_path)
        await tm.init()
        try:
            await tm.set_config("dashscope_api_key", "sk-test-1234")
            assert await tm.get_config("dashscope_api_key") == "sk-test-1234"

            await tm.set_configs({"poll_interval_sec": "5", "default_burn_path": "html"})
            cfg = await tm.get_all_config()
            assert cfg["poll_interval_sec"] == "5"
            assert cfg["default_burn_path"] == "html"
        finally:
            await tm.close()

    _run(go())


# ── Whitelist invariants (used by Gate 1 grep / static check) ─────────────────


def test_whitelist_does_not_leak_immutable_columns():
    """``id``, ``mode``, ``source_*``, ``created_at`` must NOT be updatable.

    These are creation-only fields. Letting the pipeline change them silently
    is a recipe for ghost tasks. Documented in §8.3.
    """
    immutable = {
        "id",
        "mode",
        "source_kind",
        "source_path",
        "source_duration_sec",
        "source_lang",
        "target_lang",
        "asset_id",
        "created_at",
        "origin_plugin_id",
        "origin_task_id",
    }
    leak = immutable & _UPDATABLE_COLUMNS["tasks"]
    assert not leak, f"_UPDATABLE_COLUMNS leaks immutable columns: {leak}"


def test_whitelist_transcripts_excludes_id_and_hash():
    immutable = {"id", "source_hash", "source_path", "source_name", "created_at"}
    leak = immutable & _UPDATABLE_COLUMNS["transcripts"]
    assert not leak, f"transcripts whitelist leaks immutable columns: {leak}"


def test_assets_bus_writes_disabled_in_v1(db_path: Path):
    """No SQL INSERT/UPDATE on assets_bus is reachable from public methods."""
    import inspect

    src = inspect.getsource(SubtitleTaskManager)
    assert "INSERT INTO assets_bus" not in src
    assert "UPDATE assets_bus" not in src
    assert "INSERT OR REPLACE INTO assets_bus" not in src


# ── Schema-level red-line: SQL DDL for assets_bus exists, with reservation ────


def test_assets_bus_schema_has_v2_marker(db_path: Path):
    """The DDL must explicitly mark assets_bus as v2.0-reserved.

    This protects future maintainers from accidentally writing rows when they
    don't know the v2.0 Handoff design isn't shipped yet.
    """
    import inspect

    src = inspect.getsource(SubtitleTaskManager._create_tables)
    assert "v2.0" in src or "v2_0" in src, (
        "_create_tables must mark assets_bus as v2.0-only in a comment"
    )


def test_create_task_serializes_params_as_json(db_path: Path):
    """Params dict round-trips through SQL as JSON text."""

    async def go():
        tm = SubtitleTaskManager(db_path)
        await tm.init()
        try:
            payload = {"language_hints": ["zh", "en"], "diarization_enabled": True}
            t = await tm.create_task(mode="auto_subtitle", params=payload)
            assert t["params"] == payload

            assert tm._db is not None
            cur = await tm._db.execute("SELECT params_json FROM tasks WHERE id = ?", (t["id"],))
            row = await cur.fetchone()
            assert row is not None
            assert json.loads(row["params_json"]) == payload
        finally:
            await tm.close()

    _run(go())

