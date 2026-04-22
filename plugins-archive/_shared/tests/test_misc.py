"""Tests for the smaller contrib helpers (slideshow_risk, delivery_promise,
provider_score, env_any_loader, ui_events, storage_stats)."""

from __future__ import annotations

import pytest

from _shared import (
    DeliveryPromise,
    EnvAnyEntry,
    ProviderScore,
    SlideshowRisk,
    UIEventEmitter,
    collect_storage_stats,
    evaluate_slideshow_risk,
    load_env_any,
    score_providers,
    strip_plugin_event_prefix,
    validate_cuts,
)


# ── slideshow_risk ──────────────────────────────────────────────────────


def test_slideshow_empty_plan_is_high_risk() -> None:
    out = evaluate_slideshow_risk([])
    assert out.verdict == "high"
    assert out.score == 0


def test_slideshow_motion_plan_is_low_risk() -> None:
    plan = [
        {"description": "镜头缓慢推进，主角走向窗口", "shot_type": "close-up",
         "transition": "cut", "duration": 3, "environment": "微风吹过"},
        {"description": "环绕镜头，主角举起手", "shot_type": "wide",
         "transition": "dissolve", "duration": 4},
        {"description": "推镜，光斑闪过", "shot_type": "medium",
         "transition": "fade", "duration": 5},
    ]
    out = evaluate_slideshow_risk(plan)
    assert out.score >= 4
    assert out.verdict == "low"


def test_slideshow_static_plan_is_high_risk() -> None:
    plan = [
        {"description": "静态全景", "shot_type": "wide", "duration": 12},
        {"description": "静态特写", "shot_type": "wide", "duration": 12},
    ]
    out = evaluate_slideshow_risk(plan)
    assert out.verdict in {"medium", "high"}
    assert out.score < 4


# ── delivery_promise ────────────────────────────────────────────────────


def test_delivery_honored_when_actual_meets_promise() -> None:
    cuts = [
        {"has_motion": True,  "duration": 3, "label": "shot1"},
        {"has_motion": False, "duration": 1, "label": "shot2"},
    ]
    out = validate_cuts(cuts, promised_motion_ratio=0.5)
    assert out.verdict == "honored"


def test_delivery_broken_when_actual_too_low() -> None:
    cuts = [{"has_motion": False, "duration": 10, "label": "x"}]
    out = validate_cuts(cuts, promised_motion_ratio=0.5)
    assert out.verdict == "broken"
    assert "x" in out.breached_segments


def test_delivery_almost_within_tolerance() -> None:
    cuts = [
        {"has_motion": True,  "duration": 4, "label": "ok"},
        {"has_motion": False, "duration": 6, "label": "no"},
    ]
    # actual = 0.40, promised = 0.45, tolerance 0.05 → almost
    out = validate_cuts(cuts, promised_motion_ratio=0.45, tolerance=0.05)
    assert out.verdict == "almost"


# ── provider_score ──────────────────────────────────────────────────────


def test_score_providers_ranks_by_total() -> None:
    cands = [
        {"id": "fast", "quality": 0.5, "speed": 1.0, "cost": 1.0, "reliability": 0.8,
         "control": 0.5, "latency": 1.0, "compatibility": 1.0},
        {"id": "best", "quality": 1.0, "speed": 0.6, "cost": 0.5, "reliability": 0.9,
         "control": 0.8, "latency": 0.5, "compatibility": 0.8},
    ]
    out = score_providers(cands)
    assert out[0].provider_id in {"best", "fast"}
    assert all(isinstance(p, ProviderScore) for p in out)


def test_score_providers_must_have_disqualifies() -> None:
    cands = [
        {"id": "noctl", "quality": 1.0, "speed": 1.0, "cost": 1.0, "reliability": 1.0,
         "control": 0.0, "latency": 1.0, "compatibility": 1.0},
        {"id": "withctl", "quality": 0.5, "speed": 0.5, "cost": 0.5, "reliability": 0.5,
         "control": 0.5, "latency": 0.5, "compatibility": 0.5},
    ]
    out = score_providers(cands, must_have=("control",))
    assert out[0].provider_id == "withctl"
    # noctl ranked last with total=0
    assert out[-1].total == 0.0


# ── env_any_loader ──────────────────────────────────────────────────────


def test_env_any_inline_list(tmp_path, monkeypatch) -> None:
    p = tmp_path / "SKILL.md"
    p.write_text("---\nname: test\nenv_any: [VAR_A, VAR_B]\n---\n# body\n", encoding="utf-8")
    monkeypatch.setenv("VAR_B", "xyz")
    out = load_env_any(p)
    assert out.satisfied
    assert out.first_present == "VAR_B"
    assert out.required == ["VAR_A", "VAR_B"]


def test_env_any_block_list(tmp_path, monkeypatch) -> None:
    p = tmp_path / "SKILL.md"
    p.write_text("---\nenv_any:\n  - X\n  - Y\n---\n", encoding="utf-8")
    monkeypatch.delenv("X", raising=False)
    monkeypatch.delenv("Y", raising=False)
    out = load_env_any(p)
    assert not out.satisfied
    assert out.required == ["X", "Y"]


def test_env_any_missing_file_returns_satisfied(tmp_path) -> None:
    out = load_env_any(tmp_path / "nope.md")
    assert out.satisfied
    assert out.required == []


# ── ui_events ──────────────────────────────────────────────────────────


def test_strip_plugin_event_prefix_basic() -> None:
    pid, evt = strip_plugin_event_prefix("plugin:my-plugin:task_updated")
    assert pid == "my-plugin"
    assert evt == "task_updated"


def test_strip_plugin_event_prefix_no_prefix() -> None:
    pid, evt = strip_plugin_event_prefix("task_updated")
    assert pid is None
    assert evt == "task_updated"


def test_ui_event_emitter_local_listeners() -> None:
    captured: list[dict] = []

    class _FakeApi:
        def broadcast_ui_event(self, event_type, data, **kw):
            captured.append({"type": event_type, "data": data})

    em = UIEventEmitter(_FakeApi())
    em.on("ping", lambda d: captured.append({"local": d}))
    em.emit("ping", {"x": 1})
    assert any("local" in c for c in captured)
    assert any(c.get("type") == "ping" for c in captured)


def test_ui_event_emitter_works_without_api() -> None:
    em = UIEventEmitter(object())  # api has no broadcast method — should not crash
    em.on("p", lambda d: None)
    em.emit("p", {})  # should not raise


# ── storage_stats ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_storage_stats_walks(tmp_path) -> None:
    (tmp_path / "a.txt").write_text("hello")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.png").write_bytes(b"\x00" * 100)

    stats = await collect_storage_stats(tmp_path, max_files=10)
    assert stats.total_files == 2
    assert stats.total_bytes == 5 + 100
    assert "txt" in stats.by_extension
    assert stats.by_extension["png"]["bytes"] == 100
    assert not stats.truncated


@pytest.mark.asyncio
async def test_storage_stats_truncates_when_capped(tmp_path) -> None:
    for i in range(20):
        (tmp_path / f"f{i}.bin").write_text(str(i))
    stats = await collect_storage_stats(tmp_path, max_files=5)
    assert stats.truncated
    assert stats.total_files == 5


@pytest.mark.asyncio
async def test_storage_stats_skip_hidden(tmp_path) -> None:
    (tmp_path / "good.txt").write_text("y")
    (tmp_path / ".hidden.txt").write_text("n")
    stats = await collect_storage_stats(tmp_path, skip_hidden=True)
    assert stats.total_files == 1
