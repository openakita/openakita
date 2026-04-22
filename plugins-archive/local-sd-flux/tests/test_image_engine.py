"""Unit tests for ``image_engine``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


def _ie():
    import image_engine
    return image_engine


def _cc():
    import comfy_client
    return comfy_client


# ── plan_image: input validation ──────────────────────────────────────


def test_plan_image_requires_non_empty_prompt(tmp_path: Path) -> None:
    ie = _ie()
    with pytest.raises(ValueError, match="prompt"):
        ie.plan_image(prompt="", output_dir=str(tmp_path))


def test_plan_image_requires_output_dir(tmp_path: Path) -> None:
    ie = _ie()
    with pytest.raises(ValueError, match="output_dir"):
        ie.plan_image(prompt="hi", output_dir="")


def test_plan_image_rejects_bad_output_format(tmp_path: Path) -> None:
    ie = _ie()
    with pytest.raises(ValueError, match="output_format"):
        ie.plan_image(prompt="hi", output_dir=str(tmp_path), output_format="bmp")


def test_plan_image_clamps_poll_interval(tmp_path: Path) -> None:
    ie = _ie()
    with pytest.raises(ValueError, match="poll_interval_sec"):
        ie.plan_image(prompt="hi", output_dir=str(tmp_path), poll_interval_sec=0.0)
    with pytest.raises(ValueError, match="poll_interval_sec"):
        ie.plan_image(prompt="hi", output_dir=str(tmp_path), poll_interval_sec=999.0)


def test_plan_image_clamps_timeout(tmp_path: Path) -> None:
    ie = _ie()
    with pytest.raises(ValueError, match="timeout_sec"):
        ie.plan_image(prompt="hi", output_dir=str(tmp_path), timeout_sec=1.0)
    with pytest.raises(ValueError, match="timeout_sec"):
        ie.plan_image(prompt="hi", output_dir=str(tmp_path), timeout_sec=99999.0)


def test_plan_image_rejects_unknown_preset(tmp_path: Path) -> None:
    ie = _ie()
    with pytest.raises(ValueError, match="preset_id"):
        ie.plan_image(prompt="hi", output_dir=str(tmp_path), preset_id="zzz")


def test_plan_image_rejects_empty_custom_workflow(tmp_path: Path) -> None:
    ie = _ie()
    with pytest.raises(ValueError, match="custom_workflow"):
        ie.plan_image(prompt="hi", output_dir=str(tmp_path), custom_workflow={})


# ── plan_image: happy paths ────────────────────────────────────────────


def test_plan_image_with_preset_substitutes_prompt(tmp_path: Path) -> None:
    ie = _ie()
    plan = ie.plan_image(
        prompt="a wizard cat", output_dir=str(tmp_path), preset_id="sd15_basic",
    )
    assert plan.preset_id == "sd15_basic"
    assert plan.is_custom_workflow is False
    txts = [n["inputs"]["text"]
            for n in plan.workflow.values()
            if n.get("class_type") == "CLIPTextEncode"]
    assert any("a wizard cat" in t for t in txts)


def test_plan_image_with_custom_workflow_marks_flag(tmp_path: Path) -> None:
    ie = _ie()
    custom = {"99": {"class_type": "FooBar", "inputs": {}}}
    plan = ie.plan_image(
        prompt="x", output_dir=str(tmp_path), custom_workflow=custom,
    )
    assert plan.is_custom_workflow is True
    assert plan.preset_id == "custom"
    assert plan.workflow["99"]["class_type"] == "FooBar"


def test_plan_image_creates_output_dir(tmp_path: Path) -> None:
    ie = _ie()
    target = tmp_path / "deep" / "nested" / "dir"
    ie.plan_image(prompt="hi", output_dir=str(target))
    assert target.is_dir()


def test_plan_image_overrides_take_priority_over_preset_defaults(tmp_path: Path) -> None:
    ie = _ie()
    plan = ie.plan_image(
        prompt="x", output_dir=str(tmp_path), preset_id="sdxl_basic",
        overrides={"width": 768, "height": 768, "steps": 40},
    )
    elatent = next(n for n in plan.workflow.values()
                   if n["class_type"] == "EmptyLatentImage")
    assert elatent["inputs"]["width"] == 768
    assert elatent["inputs"]["height"] == 768
    sampler = next(n for n in plan.workflow.values()
                   if n["class_type"] == "KSampler")
    assert sampler["inputs"]["steps"] == 40


def test_plan_image_to_dict_omits_workflow_blob(tmp_path: Path) -> None:
    ie = _ie()
    plan = ie.plan_image(prompt="x", output_dir=str(tmp_path))
    d = plan.to_dict()
    assert "workflow" not in d
    assert d["preset_id"] == "sdxl_basic"


# ── extract_vram_signal ────────────────────────────────────────────────


def test_extract_vram_signal_normal_case() -> None:
    ie = _ie()
    stats = {"devices": [{"vram_total": 24_000_000_000, "vram_free": 18_000_000_000}]}
    signal = ie.extract_vram_signal(stats)
    assert 0.74 < signal < 0.76


def test_extract_vram_signal_no_devices_returns_zero() -> None:
    ie = _ie()
    assert ie.extract_vram_signal({"devices": []}) == 0.0


def test_extract_vram_signal_handles_zero_total() -> None:
    ie = _ie()
    assert ie.extract_vram_signal(
        {"devices": [{"vram_total": 0, "vram_free": 0}]},
    ) == 0.0


def test_extract_vram_signal_clamps_above_one() -> None:
    ie = _ie()
    out = ie.extract_vram_signal(
        {"devices": [{"vram_total": 1, "vram_free": 999}]},
    )
    assert out == 1.0


def test_extract_vram_signal_handles_garbage() -> None:
    ie = _ie()
    assert ie.extract_vram_signal({"devices": [{"vram_total": "x"}]}) == 0.0


# ── rank_image_providers ───────────────────────────────────────────────


def test_rank_image_providers_orders_by_total() -> None:
    ie = _ie()
    cands = [
        {"id": "weak", "label": "Weak", "base_url": "http://w",
         "quality": 0.2, "speed": 0.2, "cost": 0.5, "reliability": 0.3,
         "control": 0.5, "latency": 0.2, "compatibility": 0.5},
        {"id": "strong", "label": "Strong", "base_url": "http://s",
         "quality": 0.95, "speed": 0.9, "cost": 0.7, "reliability": 0.9,
         "control": 0.9, "latency": 0.9, "compatibility": 0.95},
    ]
    ranked = ie.rank_image_providers(cands)
    assert ranked[0].label == "Strong"
    assert ranked[0].base_url == "http://s"
    assert ranked[0].score.total > ranked[1].score.total


def test_rank_image_providers_to_dict_round_trips() -> None:
    ie = _ie()
    ranked = ie.rank_image_providers([
        {"id": "x", "label": "X", "base_url": "http://x",
         "quality": 0.5, "speed": 0.5, "cost": 0.5, "reliability": 0.5,
         "control": 0.5, "latency": 0.5, "compatibility": 0.5},
    ])
    d = ranked[0].to_dict()
    assert d["label"] == "X"
    assert "dimensions" in d and "quality" in d["dimensions"]


def test_rank_image_providers_empty_candidates() -> None:
    ie = _ie()
    assert ie.rank_image_providers([]) == []


# ── run_image (with fake client) ───────────────────────────────────────


class _FakeClient:
    """Minimal in-process stand-in for :class:`ComfyClient`."""

    def __init__(
        self, *,
        outputs: list[dict] | None = None,
        polls_until_done: int = 1,
        bytes_per_image: int = 64,
    ) -> None:
        from comfy_client import ComfyClient
        self._delegate = ComfyClient
        if outputs is None:
            outputs = [
                {"filename": "a.png", "subfolder": "", "type": "output"},
            ]
        self._outputs = outputs
        self._polls_until_done = polls_until_done
        self._poll_count = 0
        self._bytes_per_image = bytes_per_image

    async def submit_prompt(self, workflow: Any) -> str:
        return "fake-prompt-id"

    async def get_history(self, prompt_id: str) -> dict:
        self._poll_count += 1
        if self._poll_count < self._polls_until_done:
            return {}
        return {"outputs": {"7": {"images": list(self._outputs)}}}

    def is_history_complete(self, history: dict) -> bool:
        return self._delegate.is_history_complete(history)

    def parse_history_outputs(self, prompt_id: str, history: dict):
        return self._delegate.parse_history_outputs(prompt_id, history)

    async def download_image_bytes(self, image: Any) -> bytes:
        return b"x" * self._bytes_per_image


@pytest.mark.asyncio
async def test_run_image_writes_images_to_disk(tmp_path: Path) -> None:
    ie = _ie()
    plan = ie.plan_image(prompt="cat", output_dir=str(tmp_path))
    fake = _FakeClient(
        outputs=[
            {"filename": "img1.png", "subfolder": "", "type": "output"},
            {"filename": "img2.png", "subfolder": "sub", "type": "output"},
        ],
        polls_until_done=2,
        bytes_per_image=32,
    )
    progress: list[tuple[str, int, int]] = []

    async def _no_sleep(_d: float) -> None:
        return None

    res = await ie.run_image(plan, client=fake, sleep=_no_sleep,
                             on_progress=lambda s, d, t: progress.append((s, d, t)))
    assert res.image_count == 2
    for p in res.image_paths:
        path = Path(p)
        assert path.is_file()
        assert path.read_bytes() == b"x" * 32
    assert res.bytes_total == 64
    assert res.prompt_id == "fake-prompt-id"
    assert res.polls == 2
    assert any(s[0] == "submit" for s in progress)
    assert any(s[0] == "download" for s in progress)


@pytest.mark.asyncio
async def test_run_image_times_out_when_history_never_completes(tmp_path: Path) -> None:
    ie = _ie()
    plan = ie.plan_image(
        prompt="x", output_dir=str(tmp_path),
        poll_interval_sec=0.1, timeout_sec=10.0,
    )

    class _NeverDone(_FakeClient):
        async def get_history(self, prompt_id: str) -> dict:
            self._poll_count += 1
            return {}

    async def _no_sleep(_d: float) -> None:
        return None

    fake = _NeverDone(polls_until_done=10**9)

    # Force timeout deterministically by zeroing the budget mid-flight.
    plan.timeout_sec = 0.001
    with pytest.raises(TimeoutError):
        await ie.run_image(plan, client=fake, sleep=_no_sleep)


@pytest.mark.asyncio
async def test_run_image_returns_zero_images_when_outputs_empty(tmp_path: Path) -> None:
    ie = _ie()
    plan = ie.plan_image(prompt="x", output_dir=str(tmp_path))
    fake = _FakeClient(outputs=[])

    async def _no_sleep(_d: float) -> None:
        return None

    res = await ie.run_image(plan, client=fake, sleep=_no_sleep)
    assert res.image_count == 0
    assert res.bytes_total == 0


# ── verification ───────────────────────────────────────────────────────


def _make_result(ie, *, paths: list[str], bytes_total: int,
                 elapsed: float, timeout: float = 60.0,
                 is_custom: bool = False, workflow: dict | None = None) -> Any:
    plan = ie.ImagePlan(
        preset_id="sdxl_basic",
        workflow=workflow or {"7": {"class_type": "SaveImage"}},
        overrides={},
        output_dir="/tmp",
        output_format="png",
        poll_interval_sec=1.0,
        timeout_sec=timeout,
        is_custom_workflow=is_custom,
    )
    return ie.ImageResult(
        plan=plan, prompt_id="x", image_paths=paths,
        elapsed_sec=elapsed, polls=1, bytes_total=bytes_total,
    )


def test_verification_green_when_images_and_bytes_present() -> None:
    ie = _ie()
    res = _make_result(ie, paths=["/tmp/a.png"], bytes_total=1024, elapsed=5.0)
    v = ie.to_verification(res)
    assert v.verified is True
    assert v.low_confidence_fields == []


def test_verification_flags_zero_images() -> None:
    ie = _ie()
    res = _make_result(ie, paths=[], bytes_total=0, elapsed=5.0)
    v = ie.to_verification(res)
    assert v.verified is False
    assert any(f.path == "$.image_count" for f in v.low_confidence_fields)


def test_verification_flags_zero_bytes_when_images_present() -> None:
    ie = _ie()
    res = _make_result(ie, paths=["/tmp/a.png"], bytes_total=0, elapsed=5.0)
    v = ie.to_verification(res)
    assert v.verified is False
    assert any(f.path == "$.bytes_total" for f in v.low_confidence_fields)


def test_verification_flags_custom_workflow_without_save_image() -> None:
    ie = _ie()
    res = _make_result(
        ie, paths=["/tmp/a.png"], bytes_total=64, elapsed=5.0,
        is_custom=True, workflow={"7": {"class_type": "Foo"}},
    )
    v = ie.to_verification(res)
    assert v.verified is False
    assert any(f.path == "$.plan.workflow" for f in v.low_confidence_fields)


def test_verification_does_not_flag_custom_with_save_image() -> None:
    ie = _ie()
    res = _make_result(
        ie, paths=["/tmp/a.png"], bytes_total=64, elapsed=5.0,
        is_custom=True, workflow={"7": {"class_type": "SaveImageAdvanced"}},
    )
    v = ie.to_verification(res)
    assert v.verified is True


def test_verification_flags_elapsed_above_eighty_percent_of_budget() -> None:
    ie = _ie()
    res = _make_result(
        ie, paths=["/tmp/a.png"], bytes_total=64,
        elapsed=85.0, timeout=100.0,
    )
    v = ie.to_verification(res)
    assert v.verified is False
    assert any(f.path == "$.elapsed_sec" for f in v.low_confidence_fields)


def test_verification_does_not_flag_elapsed_below_eighty_percent() -> None:
    ie = _ie()
    res = _make_result(
        ie, paths=["/tmp/a.png"], bytes_total=64,
        elapsed=70.0, timeout=100.0,
    )
    v = ie.to_verification(res)
    assert v.verified is True


# ── describe / list_available_presets ──────────────────────────────────


def test_describe_returns_normalized_dict() -> None:
    ie = _ie()
    d = ie.describe("sdxl_basic")
    assert d["id"] == "sdxl_basic"
    assert d["family"] == "sdxl"
    assert d["width_default"] == 1024


def test_list_available_presets_returns_three_items() -> None:
    ie = _ie()
    assert len(ie.list_available_presets()) == 3
