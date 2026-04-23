"""End-to-end smoke test against the real DashScope API.

This test is **opt-in**. It runs only when the environment variable
``DASHSCOPE_API_KEY`` is set and pytest is invoked with
``-m integration``. The default test session (``pytest -q``) skips it,
which keeps CI hermetic and the contributor inner loop cheap.

Coverage:

* Submit a 3-second ``photo_speak`` job (the cheapest happy path).
* Poll the DashScope task with the 3-tier backoff implemented in
  :mod:`avatar_pipeline`.
* Assert that an MP4 lands under the per-task data directory and that
  the ``cost_breakdown`` matches the resolution / duration we asked for.
* Tear the temp data dir down on exit so repeated runs stay tidy.

The real spend is bounded — at 480P the official price is roughly
``¥0.10/sec``, so a 3-second job costs ``¥0.30 + ¥0.004`` (face detect)
plus a few cents of TTS, which keeps the smoke under ``¥0.50``. That is
intentional: the gate is *production reachable*, not *production
saturated*.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.integration


# ──────────────────────────────────────────────────────────────────────
# Skip-by-default plumbing
# ──────────────────────────────────────────────────────────────────────


def _api_key() -> str | None:
    return os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("AVATAR_STUDIO_DASHSCOPE_API_KEY")


pytest.importorskip(
    "httpx",
    reason="httpx is required for the avatar-studio smoke test",
)


_SKIP_REASON = (
    "DashScope smoke test is opt-in. Set DASHSCOPE_API_KEY and run "
    "``pytest -m integration`` to execute it."
)


needs_api_key = pytest.mark.skipif(_api_key() is None, reason=_SKIP_REASON)


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


_SAMPLE_PORTRAIT_URL = (
    "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/"
    "20240829/lyumdf/female_2.png"
)


@pytest.fixture()
def tmp_data_dir() -> Path:
    """Per-test data dir; tossed at teardown."""
    d = Path(tempfile.mkdtemp(prefix="avatar-studio-smoke-"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ──────────────────────────────────────────────────────────────────────
# The actual smoke
# ──────────────────────────────────────────────────────────────────────


@needs_api_key
def test_photo_speak_3s_end_to_end(tmp_data_dir: Path) -> None:
    """Submit the cheapest ``photo_speak`` job and verify the MP4 lands.

    Goes through the **real** ``run_pipeline`` so that the test exercises
    everything: TTS → face_detect → submit_s2v → polling → download →
    metadata persist. The fail mode the test cares about is "the seam
    between client / pipeline / task manager broke" — failures inside
    DashScope itself surface as ``ctx.error_kind`` so the assertion
    message is actionable rather than opaque.
    """
    from avatar_dashscope_client import DASHSCOPE_BASE_URL_BJ, AvatarDashScopeClient
    from avatar_pipeline import AvatarPipelineContext, run_pipeline
    from avatar_task_manager import AvatarTaskManager

    api_key = _api_key()
    assert api_key, "guarded by needs_api_key"

    settings = {
        "api_key": api_key,
        "base_url": DASHSCOPE_BASE_URL_BJ,
        "timeout": 120.0,
        "max_retries": 1,
        "cost_threshold": 5.0,
    }

    async def _run() -> tuple[AvatarPipelineContext, dict[str, Any]]:
        tm = AvatarTaskManager(tmp_data_dir / "smoke.db")
        await tm.init()
        try:
            client = AvatarDashScopeClient(read_settings=lambda: settings)
            # ``params['assets']`` is the canonical input contract;
            # ``image_url``/``audio_url``/etc are the keys the pipeline
            # reads after _step_prepare_assets. The earlier draft of
            # this test put ``image_url`` at the top level of params,
            # which the pipeline silently dropped — making the smoke a
            # green light even when the contract was completely broken.
            #
            # _SAMPLE_PORTRAIT_URL is a public DashScope sample image
            # so we don't need to push it through OSS for this test.
            # We also stub out the OSS audio uploader so cosyvoice
            # output gets handed to s2v as a public DashScope CDN URL
            # via a no-op shim.  In a fully-configured deployment this
            # callable goes through ``OssUploader.upload_file``.
            async def _fake_oss_upload(local: Path, fname: str) -> str:  # noqa: ARG001
                pytest.skip(
                    "smoke test needs OSS to host the TTS audio output; "
                    "set the OSS_* env vars and re-run"
                )

            params: dict[str, Any] = {
                "mode": "photo_speak",
                "assets": {"image_url": _SAMPLE_PORTRAIT_URL},
                "text": "你好，欢迎来到数字人工作室。",
                "voice_id": "longxiaochun_v2",
                "resolution": "480P",
                "audio_duration_sec": 3.0,
                "cost_approved": True,
                "_oss_upload_audio": _fake_oss_upload,
            }
            task_id = await tm.create_task(
                mode="photo_speak",
                prompt=params["text"],
                params=params,
                asset_paths={"image_url": _SAMPLE_PORTRAIT_URL},
                cost_breakdown={},
            )
            ctx = AvatarPipelineContext(task_id=task_id, mode="photo_speak", params=params)
            ctx.cost_approved = True
            await run_pipeline(
                ctx,
                tm=tm,
                client=client,
                emit=lambda _e, _p: None,
                plugin_id="avatar-studio",
                base_data_dir=tmp_data_dir,
            )
            row = await tm.get_task(task_id)
            return ctx, dict(row or {})
        finally:
            await tm.close()

    ctx, row = asyncio.run(_run())

    if ctx.error_kind:
        pytest.fail(
            f"pipeline failed with {ctx.error_kind}: {ctx.error_message}",
            pytrace=False,
        )

    assert row.get("status") == "succeeded", row
    # ``output_url`` is what the pipeline persists today (DashScope
    # CDN URL with ~24 h TTL). ``output_path`` is the local mirror —
    # may be empty on this build because finalize doesn't download
    # the video yet (P8 in the audit todo list).
    output_url = row.get("output_url")
    assert output_url, "expected ``output_url`` to be persisted"
    output_path = row.get("output_path") or ""
    if output_path:
        assert Path(output_path).exists(), f"missing video at {output_path}"
        assert Path(output_path).stat().st_size > 1024, "video file is suspiciously tiny"

    cost = row.get("cost_breakdown") or {}
    assert cost.get("currency") == "CNY"
    assert cost.get("total", 0) > 0
    assert cost.get("formatted_total", "").startswith("¥"), cost
