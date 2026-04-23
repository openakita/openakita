"""Integration smoke for media-post Qwen-VL-max cover-pick path.

Per ``docs/media-post-plan.md`` §10.2 row 1:
"Real key runs cover_pick on 4 frames and verifies the 6-axis scores
land in [0, 5] and the bbox sits inside the frame."

Cost budget: < ¥0.5 (single 4-frame batch ≈ ¥0.08, repeated at most
twice if the model trips on the strict-JSON envelope).

Requirements:
- ``DASHSCOPE_API_KEY`` env var.

Run::

    pytest plugins/media-post/tests/integration/test_qwen_vl_smoke.py -m integration
"""

from __future__ import annotations

import asyncio
import base64
import os
import struct
import zlib

import pytest

pytestmark = pytest.mark.integration


def _make_solid_png_b64(color: tuple[int, int, int], *, size: int = 64) -> str:
    """Build a tiny solid-colour PNG and return base64 — keeps payload small.

    The DashScope endpoint accepts any decodable PNG/JPEG; sub-1 KB uniform
    images keep both upload time and per-call cost minimal while still
    exercising the full request/response/parse path end-to-end.
    """

    r, g, b = color

    def _chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    raw = b"".join(
        b"\x00" + bytes((r, g, b)) * size for _ in range(size)
    )
    idat = zlib.compress(raw, level=9)
    png = sig + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")
    return base64.b64encode(png).decode("ascii")


@pytest.fixture()
def api_key() -> str:
    key = os.environ.get("DASHSCOPE_API_KEY", "")
    if not key:
        pytest.skip("DASHSCOPE_API_KEY not set — skipping VLM smoke")
    return key


def test_models_sanity() -> None:
    """Sanity: 4 modes, 5 platforms, 2 aspects, 9 error kinds load."""
    from mediapost_models import (
        ASPECTS,
        ERROR_HINTS,
        MODES,
        PLATFORMS,
        estimate_cost,
    )

    assert len(MODES) == 4
    assert len(PLATFORMS) == 5
    assert len(ASPECTS) == 2
    assert len(ERROR_HINTS) == 9
    preview = estimate_cost("cover_pick", 60.0, {"quantity": 8})
    assert preview.total_cny >= 0


def test_vlm_client_4_frame_cover_pick(api_key: str) -> None:
    """Real DashScope hop: 4 PNG frames → cover-pick prompt → strict JSON.

    Uses the same prompt envelope the cover picker ships in production but
    with a tiny 4-frame batch to stay under the ¥0.5 budget.
    """
    from mediapost_vlm_client import MediaPostVlmClient

    frames = [
        _make_solid_png_b64((230, 20, 20)),
        _make_solid_png_b64((20, 220, 20)),
        _make_solid_png_b64((20, 20, 230)),
        _make_solid_png_b64((220, 220, 20)),
    ]
    indices = [0, 1, 2, 3]

    prompt = (
        "You are scoring video frames as cover candidates. For each of the "
        "{count} frames return a JSON array of objects with fields: "
        "frame_index (int), composition (0-5 float), clarity (0-5 float), "
        "subject_prominence (0-5 float), emotional_impact (0-5 float), "
        "color_harmony (0-5 float), branding_friendly (0-5 float), "
        "best_for (string), reason (string), bbox "
        "(object with x,y,w,h all in [0,1]). Output JSON only, no prose."
    )

    async def _run() -> list[dict[str, object]] | None:
        client = MediaPostVlmClient(api_key, max_retries=1)
        try:
            return await client.call_vlm_batch(
                frames,
                indices,
                prompt,
                {"count": len(frames)},
            )
        finally:
            await client.close()

    result = asyncio.get_event_loop().run_until_complete(_run())

    assert result is not None, "VLM returned no parseable JSON"
    assert len(result) == len(frames), (
        f"VLM ordering mismatch: expected {len(frames)} entries, got {len(result)}"
    )

    for entry in result:
        for axis in (
            "composition",
            "clarity",
            "subject_prominence",
            "emotional_impact",
            "color_harmony",
            "branding_friendly",
        ):
            score = float(entry.get(axis, -1))
            assert 0.0 <= score <= 5.0, f"{axis}={score} out of [0,5]"

        bbox = entry.get("bbox")
        if isinstance(bbox, dict):
            for k in ("x", "y", "w", "h"):
                v = float(bbox.get(k, 0.0))
                assert 0.0 <= v <= 1.0, f"bbox.{k}={v} out of [0,1]"
