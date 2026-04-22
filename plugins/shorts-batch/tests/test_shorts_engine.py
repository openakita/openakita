"""Unit tests for ``shorts_engine`` — planning + risk + render."""

from __future__ import annotations

from pathlib import Path

import pytest


def _se():
    import shorts_engine
    return shorts_engine


# ── ShortBrief / validation ───────────────────────────────────────────


def test_brief_to_dict_round_trip() -> None:
    se = _se()
    b = se.ShortBrief(topic="cats", duration_sec=8.0, style="news",
                       target_aspect="1:1", language="en", extra={"x": 1})
    d = b.to_dict()
    assert d["topic"] == "cats"
    assert d["target_aspect"] == "1:1"
    assert d["extra"] == {"x": 1}


def test_plan_brief_rejects_empty_topic() -> None:
    se = _se()
    with pytest.raises(ValueError, match="topic"):
        se.plan_brief(se.ShortBrief(topic=""))


def test_plan_brief_rejects_out_of_range_duration() -> None:
    se = _se()
    with pytest.raises(ValueError, match="duration_sec"):
        se.plan_brief(se.ShortBrief(topic="x", duration_sec=0.5))
    with pytest.raises(ValueError, match="duration_sec"):
        se.plan_brief(se.ShortBrief(topic="x", duration_sec=999.0))


def test_plan_brief_rejects_unknown_aspect() -> None:
    se = _se()
    with pytest.raises(ValueError, match="target_aspect"):
        se.plan_brief(se.ShortBrief(topic="x", target_aspect="3:2"))


def test_plan_brief_rejects_invalid_min_max() -> None:
    se = _se()
    with pytest.raises(ValueError):
        se.plan_brief(se.ShortBrief(topic="x"), min_shots=5, max_shots=2)


# ── default_scene_planner ─────────────────────────────────────────────


def test_default_scene_planner_respects_min_shots() -> None:
    se = _se()
    plan = se.default_scene_planner(se.ShortBrief(topic="x", duration_sec=2.0))
    assert len(plan) >= se.DEFAULT_MIN_SHOTS


def test_default_scene_planner_caps_max_shots() -> None:
    se = _se()
    plan = se.default_scene_planner(se.ShortBrief(topic="x", duration_sec=600.0))
    assert len(plan) <= se.DEFAULT_MAX_SHOTS


def test_default_scene_planner_assigns_increasing_indexes() -> None:
    se = _se()
    plan = se.default_scene_planner(se.ShortBrief(topic="cats", duration_sec=15.0))
    indexes = [s["index"] for s in plan]
    assert indexes == sorted(indexes)
    assert indexes[0] == 1


# ── plan_brief: risk scoring ──────────────────────────────────────────


def test_plan_brief_with_default_planner_returns_short_plan() -> None:
    se = _se()
    plan = se.plan_brief(se.ShortBrief(topic="cats", duration_sec=15.0))
    assert plan.brief.topic == "cats"
    assert len(plan.scene_plan) >= se.DEFAULT_MIN_SHOTS
    assert plan.risk.verdict in {"low", "medium", "high"}
    assert plan.estimated_cost_usd > 0


def test_plan_brief_with_custom_planner_uses_it() -> None:
    se = _se()
    custom_plan = [
        {"shot_type": "wide", "duration": 1.0, "transition": "cut",
         "description": "pan slowly across the city"},
        {"shot_type": "medium", "duration": 1.0, "transition": "dissolve",
         "description": "subject runs across screen"},
        {"shot_type": "close-up", "duration": 1.0, "transition": "cut",
         "description": "wind blowing leaves"},
        {"shot_type": "extreme-close", "duration": 1.0, "transition": "wipe",
         "description": "subject jumps"},
    ]
    p = se.plan_brief(
        se.ShortBrief(topic="x", duration_sec=4.0),
        scene_planner=lambda _b: custom_plan,
    )
    assert p.scene_plan == custom_plan
    # This plan covers 4+ motion dimensions → low risk.
    assert p.risk.verdict == "low"


def test_plan_brief_high_risk_when_planner_returns_static_shots() -> None:
    se = _se()
    static_plan = [
        {"shot_type": "wide", "duration": 10.0, "transition": "cut",
         "description": "static landscape"},
        {"shot_type": "wide", "duration": 10.0, "transition": "cut",
         "description": "static landscape, slightly later"},
        {"shot_type": "wide", "duration": 10.0, "transition": "cut",
         "description": "static landscape, even later"},
    ]
    p = se.plan_brief(
        se.ShortBrief(topic="x", duration_sec=30.0),
        scene_planner=lambda _b: static_plan,
    )
    assert p.risk.verdict == "high"


def test_plan_brief_raises_when_planner_returns_too_few() -> None:
    se = _se()
    with pytest.raises(ValueError, match="min_shots"):
        se.plan_brief(
            se.ShortBrief(topic="x"),
            scene_planner=lambda _b: [{"shot_type": "wide", "duration": 1.0}],
            min_shots=3,
        )


def test_plan_brief_trims_when_planner_overshoots_max_shots() -> None:
    se = _se()
    overshoot = [
        {"shot_type": f"shot{i}", "duration": 1.0, "transition": "cut",
         "description": f"shot {i}"}
        for i in range(20)
    ]
    p = se.plan_brief(
        se.ShortBrief(topic="x"),
        scene_planner=lambda _b: overshoot,
        max_shots=12,
    )
    assert len(p.scene_plan) == 12


def test_plan_brief_cost_estimator_can_be_overridden() -> None:
    se = _se()
    p = se.plan_brief(
        se.ShortBrief(topic="x", duration_sec=15.0),
        cost_estimator=lambda _plan, _dur: 99.99,
    )
    assert p.estimated_cost_usd == 99.99


def test_estimate_cost_increases_with_shot_count() -> None:
    se = _se()
    a = se.estimate_cost([{"shot_type": "wide"}], 10.0)
    b = se.estimate_cost([{"shot_type": "wide"}] * 5, 10.0)
    assert b > a


def test_plan_briefs_returns_one_plan_per_brief() -> None:
    se = _se()
    plans = se.plan_briefs([
        se.ShortBrief(topic="a", duration_sec=10.0),
        se.ShortBrief(topic="b", duration_sec=10.0),
    ])
    assert len(plans) == 2
    assert {p.brief.topic for p in plans} == {"a", "b"}


# ── run_brief / run_briefs ─────────────────────────────────────────────


def _stub_renderer(out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)

    def _render(plan):
        path = out_dir / f"{plan.brief.topic}.mp4"
        path.write_bytes(b"x" * 16)
        return str(path), 16

    return _render


def test_run_brief_calls_renderer_and_returns_result(tmp_path: Path) -> None:
    se = _se()
    p = se.plan_brief(se.ShortBrief(topic="cats", duration_sec=10.0))
    r = se.run_brief(p, renderer=_stub_renderer(tmp_path))
    assert r.succeeded is True
    assert Path(r.output_path).is_file()
    assert r.bytes_total == 16
    assert r.error is None


def test_run_brief_catches_renderer_exception(tmp_path: Path) -> None:
    se = _se()
    p = se.plan_brief(se.ShortBrief(topic="x", duration_sec=10.0))

    def _bad(_plan):
        raise RuntimeError("renderer exploded")

    r = se.run_brief(p, renderer=_bad)
    assert r.succeeded is False
    assert "renderer exploded" in (r.error or "")


def test_run_brief_skips_when_risk_blocks(tmp_path: Path) -> None:
    se = _se()
    static = [
        {"shot_type": "wide", "duration": 10.0, "description": "still 1"},
        {"shot_type": "wide", "duration": 10.0, "description": "still 2"},
        {"shot_type": "wide", "duration": 10.0, "description": "still 3"},
    ]
    p = se.plan_brief(
        se.ShortBrief(topic="x", duration_sec=30.0),
        scene_planner=lambda _b: static,
    )
    assert p.risk.verdict == "high"
    r = se.run_brief(p, renderer=_stub_renderer(tmp_path),
                       risk_block_threshold="high")
    assert r.succeeded is False
    assert "block" in (r.error or "").lower()


def test_run_brief_does_not_skip_when_risk_below_threshold(tmp_path: Path) -> None:
    se = _se()
    p = se.plan_brief(se.ShortBrief(topic="x", duration_sec=10.0))
    # default plan is medium → 'high' threshold should not block.
    r = se.run_brief(p, renderer=_stub_renderer(tmp_path),
                       risk_block_threshold="high")
    if p.risk.verdict != "high":
        assert r.succeeded is True


def test_run_briefs_aggregates_risk_distribution_and_cost(tmp_path: Path) -> None:
    se = _se()
    high_plan = [
        {"shot_type": "wide", "duration": 10.0, "description": "still 1"},
        {"shot_type": "wide", "duration": 10.0, "description": "still 2"},
        {"shot_type": "wide", "duration": 10.0, "description": "still 3"},
    ]
    low_plan = [
        {"shot_type": "wide", "duration": 1.0, "transition": "cut",
         "description": "pan across the city"},
        {"shot_type": "medium", "duration": 1.0, "transition": "dissolve",
         "description": "subject runs"},
        {"shot_type": "close-up", "duration": 1.0, "transition": "wipe",
         "description": "wind blows leaves"},
        {"shot_type": "extreme", "duration": 1.0, "transition": "fade",
         "description": "subject jumps"},
    ]
    p_high = se.plan_brief(
        se.ShortBrief(topic="static", duration_sec=30.0),
        scene_planner=lambda _b: high_plan,
    )
    p_low = se.plan_brief(
        se.ShortBrief(topic="action", duration_sec=4.0),
        scene_planner=lambda _b: low_plan,
    )
    batch = se.run_briefs([p_high, p_low], renderer=_stub_renderer(tmp_path))
    assert batch.total == 2
    assert batch.risk_distribution.get("high", 0) == 1
    assert batch.risk_distribution.get("low", 0) == 1
    assert batch.total_cost_usd == p_high.estimated_cost_usd + p_low.estimated_cost_usd


def test_run_briefs_invokes_progress_callback(tmp_path: Path) -> None:
    se = _se()
    plans = [
        se.plan_brief(se.ShortBrief(topic=f"t{i}", duration_sec=10.0))
        for i in range(3)
    ]
    progress = []
    se.run_briefs(
        plans, renderer=_stub_renderer(tmp_path),
        on_progress=lambda done, total, _r: progress.append((done, total)),
    )
    assert progress == [(1, 3), (2, 3), (3, 3)]


def test_run_briefs_continues_after_single_failure(tmp_path: Path) -> None:
    se = _se()
    plans = [
        se.plan_brief(se.ShortBrief(topic=f"t{i}", duration_sec=10.0))
        for i in range(3)
    ]
    calls = {"n": 0}

    def _flaky(plan):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("oops")
        path = tmp_path / f"{plan.brief.topic}.mp4"
        path.write_bytes(b"x")
        return str(path), 1

    batch = se.run_briefs(plans, renderer=_flaky)
    assert batch.total == 3
    assert batch.succeeded == 2
    assert batch.failed == 1


def test_run_briefs_with_empty_input() -> None:
    se = _se()
    batch = se.run_briefs([], renderer=lambda _p: ("", 0))
    assert batch.total == 0
    assert batch.succeeded == 0
    assert batch.failed == 0


# ── verification (D2.10) ──────────────────────────────────────────────


def _make_batch(se, *, succeeded: int, failed: int,
                 risk_high: int = 0, bytes_total: int = 1) -> object:
    plans = [
        se.plan_brief(se.ShortBrief(topic=f"t{i}", duration_sec=10.0))
        for i in range(succeeded + failed)
    ]
    results = []
    for i, p in enumerate(plans):
        ok = i < succeeded
        results.append(se.ShortResult(
            plan=p,
            output_path=f"/tmp/{p.brief.topic}.mp4" if ok else None,
            elapsed_sec=0.01,
            bytes_total=bytes_total if ok else 0,
            error=None if ok else "boom",
        ))
    distribution = {"low": 0, "medium": 0, "high": risk_high}
    distribution["medium"] = max(0, len(plans) - risk_high)
    return se.BatchResult(
        results=results,
        risk_distribution=distribution,
        succeeded=succeeded,
        failed=failed,
        elapsed_sec=0.1,
        total_cost_usd=1.0,
    )


def test_verification_green_on_clean_batch() -> None:
    se = _se()
    batch = _make_batch(se, succeeded=3, failed=0, risk_high=0, bytes_total=1024)
    v = se.to_verification(batch)
    assert v.verified is True


def test_verification_flags_failures() -> None:
    se = _se()
    batch = _make_batch(se, succeeded=2, failed=1, bytes_total=1024)
    v = se.to_verification(batch)
    assert v.verified is False
    assert any(f.path == "$.failed" for f in v.low_confidence_fields)


def test_verification_flags_majority_high_risk() -> None:
    se = _se()
    batch = _make_batch(se, succeeded=3, failed=0, risk_high=2, bytes_total=1024)
    v = se.to_verification(batch)
    # 2 of 3 are high → majority → flagged.
    assert v.verified is False
    assert any("risk_distribution.high" in f.path for f in v.low_confidence_fields)


def test_verification_does_not_flag_minority_high_risk() -> None:
    se = _se()
    batch = _make_batch(se, succeeded=3, failed=0, risk_high=1, bytes_total=1024)
    v = se.to_verification(batch)
    # 1 of 3 high → minority → no flag for risk dimension.
    assert all("risk_distribution.high" not in f.path
                for f in v.low_confidence_fields)


def test_verification_flags_zero_byte_outputs_when_all_succeeded() -> None:
    se = _se()
    batch = _make_batch(se, succeeded=2, failed=0, bytes_total=0)
    v = se.to_verification(batch)
    assert v.verified is False
    assert any("bytes_total" in f.path for f in v.low_confidence_fields)
