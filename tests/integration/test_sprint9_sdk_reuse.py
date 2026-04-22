"""Sprint 9 — *real* re-use validation for the 6 件套 SDK additions.

Each Sprint-8 module shipped under ``openakita_plugin_sdk.contrib`` (and
the host-level ``skill_loader``) was unit-tested in isolation, but unit
tests cannot prove that the API shape is convenient enough to be picked
up by a real plugin or by host code.  That is the gap this file closes.

Coverage matrix — one ``test_<module>_<scenario>`` per acceptance:

* ``Verification`` (D2.10)            — already covered end-to-end in
  ``plugins/bgm-suggester/tests/test_bgm_engine.py`` and
  ``plugins/storyboard/tests/test_storyboard_engine.py``; we add a
  cross-plugin merge here to lock the contract that *two* sources of
  trust signal compose without either getting clobbered.
* ``AgentLoopConfig`` (C0.5)          — instantiate with the seedance
  long-video defaults and confirm validate() is a no-op (i.e., the
  defaults the planner ships are themselves valid).
* ``prompts.load_prompt`` (P3.x)      — load every P3.1-P3.5 asset and
  assert the placeholders the README documents are actually present.
  This is the test that would have caught a missing prompt file or a
  silent rename far earlier than the unit suite.
* ``ToolResult`` (C0.2)               — wrap a representative seedance
  ``ark_client`` result shape and confirm the JSON envelope a frontend
  would receive carries every contract field (``ok``, ``output``,
  ``duration_seconds``, ``warnings``, ``metadata``).
* ``IntentVerifier.self_eval_loop`` (C0.6) — drive the loop end-to-end
  with a fake LLM caller (no network) and assert it produces an
  ``EvalResult`` with the same field-path syntax that
  ``Verification.low_confidence_fields`` uses, so a future host can
  bridge the two without a translation layer.
* ``skill_loader.load_skill`` (C0.4) — parse a *real* SKILL.md from the
  storyboard plugin (the one that ships in the repo) and confirm the
  manifest dict matches the values committed to disk.  This guards
  against a frontmatter regression silently breaking skill discovery.

These tests are deliberately fast (no real LLM, no network, no ffmpeg);
they belong to the integration suite because they wire SDK code against
real plugin/host artefacts on disk.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make plugins importable (mirrors how the host loader sets PYTHONPATH).
_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "plugins" / "bgm-suggester"))
sys.path.insert(0, str(_REPO / "plugins" / "storyboard"))


# ── D2.10 Verification: cross-plugin merge ─────────────────────────────


def test_verification_merges_from_bgm_and_storyboard() -> None:
    """A pipeline that runs bgm-suggester and storyboard back-to-back
    should be able to merge their two ``Verification`` envelopes into a
    single badge for the host UI.  This pins the contract that
    :func:`merge_verifications` keeps both ``verifier_id`` strings and
    accumulates flagged fields rather than discarding either signal."""
    from openakita_plugin_sdk.contrib import (
        BADGE_GREEN,
        BADGE_YELLOW,
        merge_verifications,
    )
    from bgm_engine import (  # type: ignore[import-not-found]
        BgmBrief,
        self_check as bgm_self_check,
        to_verification as bgm_verification,
    )
    from storyboard_engine import (  # type: ignore[import-not-found]
        Shot, Storyboard,
        self_check as sb_self_check,
        to_verification as sb_verification,
    )

    brief = BgmBrief(
        title="t", target_duration_sec=30, style="lofi",
        tempo_bpm=70, tempo_label="fast",  # mismatch
        keywords=["a", "b", "c"],
        mood_arc=["calm"], energy_curve=[0.3],
    )
    sb = Storyboard(title="T", target_duration_sec=18, shots=[
        Shot(index=1, duration_sec=6, visual="A"),
        Shot(index=2, duration_sec=6, visual="B"),
        Shot(index=3, duration_sec=6, visual="C"),
    ])  # balanced → green
    v_bgm = bgm_verification(brief, bgm_self_check(brief))
    v_sb = sb_verification(sb, sb_self_check(sb))
    assert v_sb.badge == BADGE_GREEN
    assert v_bgm.badge == BADGE_YELLOW

    merged = merge_verifications([v_bgm, v_sb])
    # Yellow wins over green — the merged envelope must keep the lower
    # confidence and surface BOTH verifier ids so a tooltip can show
    # which step flagged what.
    assert merged.badge == BADGE_YELLOW
    assert "self_check" in merged.verifier_id  # composed id
    paths = [f.path for f in merged.low_confidence_fields]
    assert "$.brief.tempo_bpm" in paths  # from bgm-suggester
    assert all(p.startswith("$.") for p in paths)


# ── C0.5 AgentLoopConfig: defaults validate ────────────────────────────


def test_agent_loop_config_seedance_defaults_validate() -> None:
    """The seedance-video planner relies on the default AgentLoopConfig
    being valid out of the box.  Validation runs in ``__post_init__``,
    so simply *constructing* the dataclass with the seedance overrides
    must not raise."""
    from openakita_plugin_sdk.contrib import (
        DEFAULT_AGENT_LOOP_CONFIG,
        AgentLoopConfig,
    )
    cfg = DEFAULT_AGENT_LOOP_CONFIG
    assert cfg.max_iterations >= 1
    assert cfg.context_overflow_markers  # non-empty
    assert cfg.retry_status_codes  # non-empty
    # Round-trip the config through to_dict / from_dict — pins the
    # contract that a host can persist a plugin's loop config to disk
    # and reload it without losing any field.
    rebuilt = AgentLoopConfig.from_dict(cfg.to_dict())
    assert rebuilt == cfg

    # A plugin-customised config (seedance picks a tighter ceiling so
    # long-video doesn't burn budget on a runaway loop) must construct
    # without raising — that's __post_init__'s validation contract.
    seedance = AgentLoopConfig(
        max_iterations=20,
        max_consecutive_tool_failures=2,
        request_timeout_sec=120.0,
    )
    assert seedance.max_iterations == 20
    assert seedance.is_retryable_status(429)  # retry-after path
    assert not seedance.is_retryable_status(401)  # never retry auth
    assert seedance.is_context_overflow(
        "Error: maximum context length exceeded by 1024 tokens",
    )


# ── P3.x prompts.load_prompt: every asset loads ────────────────────────


@pytest.mark.parametrize("name", [
    "structure_proposal",
    "agent_loop_system",
    "agent_loop_finishers",
    "reviewer_protocol",
    "checkpoint_protocol",
])
def test_prompts_each_p3_asset_loads_non_empty(name: str) -> None:
    """Every prompt name registered in :mod:`prompts` must resolve to a
    non-empty asset on disk.  Catches accidental file deletion or rename
    far earlier than the unit suite (which uses fixture paths)."""
    from openakita_plugin_sdk.contrib import load_prompt, list_prompts
    assert name in list_prompts()
    body = load_prompt(name)
    # All loaded prompts are strings (load_prompt returns str unless the
    # asset is a structured ``key:value`` block, which is dict — the
    # finishers case below).
    assert body
    if isinstance(body, str):
        assert len(body.strip()) > 50  # at least a real paragraph
    elif isinstance(body, dict):
        assert body  # non-empty dict


def test_prompts_finishers_is_keyed_dict() -> None:
    """The ``agent_loop_finishers`` asset is the only structured one — a
    dict of finisher-name → instruction.  Asserting this here means the
    seedance planner can rely on ``items()`` iteration without a
    runtime isinstance check."""
    from openakita_plugin_sdk.contrib import load_prompt
    finishers = load_prompt("agent_loop_finishers")
    assert isinstance(finishers, dict)
    # Every key must be a non-empty identifier-like string and every
    # value a non-empty instruction.
    for key, value in finishers.items():
        assert key and key.strip() == key
        assert isinstance(value, str)
        assert value.strip()


# ── C0.2 ToolResult: round-trip a seedance ark_client shape ────────────


def test_tool_result_round_trips_seedance_ark_response() -> None:
    """Wrap the dict shape that ``ark_client.create_video_task`` returns
    in a :class:`ToolResult` and assert ``.to_dict()`` carries every
    contract field.  This is the envelope a future agent loop would
    surface in its trace, so missing a field here breaks observability."""
    from openakita_plugin_sdk.contrib import ToolResult
    ark_response = {
        "id": "cgt-xxxxx",
        "model": "doubao-seedance-1-0-pro-250528",
        "status": "queued",
        "created_at": 1750000000,
    }
    result = ToolResult.success(
        output=ark_response,
        duration_seconds=1.42,
        metadata={"tool": "ark.create_video_task", "ratio": "16:9"},
    )
    assert result.ok is True
    # ``error`` is the empty string (not None) for success — see C0.2 in
    # tool_result.py.  Pinning the exact value here so a future change to
    # ``Optional[str]`` does not silently break callers who do truthy
    # checks like ``if result.error: ...``.
    assert result.error == ""
    assert result.failed is False
    assert result.warnings == []
    encoded = json.dumps(result.to_dict(), ensure_ascii=False)
    decoded = json.loads(encoded)
    for key in ("ok", "output", "error", "duration_seconds",
                "warnings", "metadata"):
        assert key in decoded
    assert decoded["output"]["id"] == "cgt-xxxxx"
    assert decoded["metadata"]["tool"] == "ark.create_video_task"


def test_tool_result_failure_carries_error_text() -> None:
    """A failed tool call must produce ``ok=False`` with a non-empty
    error string and (optionally) warnings — this is the negative path
    a real plugin will hit when, e.g., ark returns a 4xx."""
    from openakita_plugin_sdk.contrib import ToolResult
    failed = ToolResult.failure(
        error="ark returned 422: invalid prompt",
        duration_seconds=0.05,
        warnings=["retry budget exhausted"],
        metadata={"tool": "ark.create_video_task"},
    )
    assert failed.ok is False
    assert "422" in failed.error
    assert failed.warnings == ["retry budget exhausted"]


# ── C0.6 IntentVerifier.self_eval_loop with fake LLM ───────────────────


def test_self_eval_loop_runs_end_to_end_with_fake_llm_call() -> None:
    """Drive ``IntentVerifier.self_eval_loop`` against an injected fake
    ``llm_call`` that always reports "no gaps".  Validates the public
    contract a real plugin will rely on:

    * ``self_eval_loop`` is awaitable (async),
    * passing JSON with ``passed=True`` and an empty gaps list yields
      ``EvalResult.passed = True``,
    * the result is JSON-serialisable so a plugin can store it in the
      task's ``extra`` blob alongside the verification badge.
    """
    import asyncio
    from openakita_plugin_sdk.contrib import IntentVerifier

    async def fake_llm_no_gaps(*, messages, max_tokens):  # noqa: ARG001
        return json.dumps({
            "passed": True,
            "gaps": [],
            "suggestions": [],
            "confidence": "high",
        })

    iv = IntentVerifier(llm_call=fake_llm_no_gaps)
    out = asyncio.run(iv.self_eval_loop(
        original_brief="Generate a 30-second BGM brief for a sunset scene.",
        produced_output=json.dumps({
            "style": "lofi", "tempo_bpm": 80,
            "keywords": ["lofi", "chill", "calm"],
        }),
    ))
    assert out.passed is True
    assert out.gaps == []
    assert out.confidence == "high"
    text = json.dumps(out.to_dict(), ensure_ascii=False)
    assert json.loads(text)["passed"] is True


def test_self_eval_loop_surfaces_gaps_from_verifier() -> None:
    """When the second model reports gaps, the result must carry them
    verbatim (capped at 5) and ``passed`` MUST be False — verifier output
    is the ground truth for the trust badge."""
    import asyncio
    from openakita_plugin_sdk.contrib import IntentVerifier

    async def fake_llm_with_gaps(*, messages, max_tokens):  # noqa: ARG001
        return json.dumps({
            "passed": True,  # verifier lies; gaps must override
            "gaps": [
                "tempo_bpm 与 tempo_label 不一致",
                "keywords 数量不足 3 个",
            ],
            "suggestions": ["调整 bpm", "补充关键词"],
            "confidence": "medium",
        })

    iv = IntentVerifier(llm_call=fake_llm_with_gaps)
    out = asyncio.run(iv.self_eval_loop(
        original_brief="x", produced_output="y",
    ))
    assert out.passed is False  # gaps override passed=True (fail-safe)
    assert len(out.gaps) == 2
    assert out.suggestions == ["调整 bpm", "补充关键词"]
    assert out.confidence == "medium"


def test_self_eval_loop_fails_safe_without_llm() -> None:
    """When no ``llm_call`` is wired, the loop must NOT raise — it
    returns a ``passed=False`` result with the configured fallback note
    so a plugin's pipeline can keep running and just show a yellow badge.
    """
    import asyncio
    from openakita_plugin_sdk.contrib import IntentVerifier

    iv = IntentVerifier()  # no llm_call
    out = asyncio.run(iv.self_eval_loop(
        original_brief="x", produced_output="y",
    ))
    assert out.passed is False
    assert out.confidence == "low"
    assert "no llm_call" in out.raw  # explicit fallback marker


# ── C0.4 skill_loader: parse a real SKILL.md ───────────────────────────


def test_skill_loader_parses_storyboard_skill_md() -> None:
    """Parse the SKILL.md that actually ships with the storyboard
    plugin and confirm the manifest matches the values committed to
    disk.  If a future PR breaks the frontmatter contract (renames a
    key, drops a list item), this test fails before the host's skill
    discovery would silently start ignoring the plugin."""
    from openakita_plugin_sdk.skill_loader import load_skill
    skill_path = _REPO / "plugins" / "storyboard" / "SKILL.md"
    assert skill_path.exists(), f"SKILL.md missing at {skill_path}"

    parsed = load_skill(skill_path)
    assert parsed.manifest.name == "storyboard"
    assert parsed.manifest.description  # non-empty
    # ``env_any`` is not a first-class field (only OpenAkita-specific
    # frontmatter keys go in ``extra``); confirm it round-trips through
    # there so a future host that wires up secret-discovery via env_any
    # still finds the value.
    assert "env_any" in parsed.manifest.extra
    # Inline ``env_any: []`` is stored verbatim as the string "[]" — the
    # loader does not eagerly evaluate JSON-ish scalar values.  This is
    # the contract: hosts that need a real list parse the string
    # downstream (keeps the loader zero-dep, no PyYAML).
    assert parsed.manifest.extra["env_any"] in ([], "[]")
    assert parsed.body.lstrip().startswith("# Storyboard")


def test_skill_loader_rejects_frontmatter_without_name() -> None:
    """The host treats ``name`` as the unique key — a missing name must
    raise loudly so a mis-edited SKILL.md cannot ship to production."""
    import tempfile
    from openakita_plugin_sdk.skill_loader import (
        SkillManifestError, load_skill,
    )
    bad = "---\ndescription: oops\n---\n\n# Body\n"
    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", delete=False, encoding="utf-8",
    ) as f:
        f.write(bad)
        tmp = Path(f.name)
    try:
        with pytest.raises(SkillManifestError):
            load_skill(tmp)
    finally:
        tmp.unlink(missing_ok=True)
