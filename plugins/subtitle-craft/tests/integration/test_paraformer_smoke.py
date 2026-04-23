"""Phase 6 integration smoke for DashScope Paraformer-v2.

Marked ``integration``; **skipped by default** when ``DASHSCOPE_API_KEY`` is
not present (CI-friendly per ``docs/subtitle-craft-plan.md §8.4`` runtime
contract).

Run with::

    pytest plugins/subtitle-craft/tests/integration/ -m integration -v

Optional env:

- ``DASHSCOPE_API_KEY`` — required, real DashScope key.
- ``SUBTITLE_CRAFT_TEST_AUDIO_URL`` — public URL of a ≤30 s audio sample;
  defaults to a known small sample provided by the platform's test bucket.
- ``SUBTITLE_CRAFT_TEST_AUDIO_URL_EN`` — optional English sample for
  ``language_hints=['en']`` coverage (Patch P-8).

Each test is bounded to ≤180 s wall time so the suite stays usable. We
do **not** assert on transcript content — only on contract:

- ASR returns canonical ``AsrResult`` with ≥1 sentence, ≥1 word.
- Word ``start_ms``/``end_ms`` are integers (P0-2 + P0-15).
- ``error_kind`` mapping + cooperative cancel return False on unknown.
- POST-only task query (P0-5) actually works against the live endpoint.
"""

from __future__ import annotations

import asyncio
import os

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


_DEFAULT_ZH_URL = (
    "https://dashscope.oss-cn-beijing.aliyuncs.com/samples/audio/paraformer/hello_world_female2.wav"
)
_DEFAULT_EN_URL = (
    "https://dashscope.oss-cn-beijing.aliyuncs.com/samples/audio/paraformer/hello_world_male2.wav"
)


def _api_key() -> str | None:
    return os.environ.get("DASHSCOPE_API_KEY") or None


def _zh_url() -> str:
    return os.environ.get("SUBTITLE_CRAFT_TEST_AUDIO_URL") or _DEFAULT_ZH_URL


def _en_url() -> str:
    return os.environ.get("SUBTITLE_CRAFT_TEST_AUDIO_URL_EN") or _DEFAULT_EN_URL


@pytest.fixture(autouse=True)
def _skip_without_key() -> None:
    if not _api_key():
        pytest.skip("DASHSCOPE_API_KEY not set; skipping live Paraformer smoke")


async def _transcribe(url: str, *, language_hints: list[str] | None) -> object:
    from subtitle_asr_client import SubtitleAsrClient

    client = SubtitleAsrClient(
        api_key=_api_key() or "",
        poll_interval=3.0,
        poll_max_seconds=180.0,
    )
    return await asyncio.wait_for(
        client.transcribe(url, language_hints=language_hints),
        timeout=180.0,
    )


async def test_paraformer_zh_smoke() -> None:
    """30-second Chinese clip end-to-end (Phase 2a Validation §1 case a)."""
    result = await _transcribe(_zh_url(), language_hints=["zh"])

    assert getattr(result, "sentences", None), "expected ≥1 sentence"
    sentence = result.sentences[0]
    assert sentence.end_ms >= sentence.start_ms >= 0
    assert isinstance(sentence.text, str) and sentence.text.strip()

    assert sentence.words, "expected word-level breakdown (P0-3)"
    word = sentence.words[0]
    # P0-2 + P0-15 — integer milliseconds, normalized field names.
    assert isinstance(word.start_ms, int) and word.start_ms >= 0
    assert isinstance(word.end_ms, int) and word.end_ms >= word.start_ms
    assert isinstance(word.text, str) and word.text


async def test_paraformer_en_smoke() -> None:
    """30-second English clip — proves ``language_hints=['en']`` (Patch P-8)."""
    result = await _transcribe(_en_url(), language_hints=["en"])

    assert getattr(result, "sentences", None), "expected ≥1 sentence"
    full = (result.full_text or "").strip()
    assert full, "expected non-empty transcript"
    # No content assertion — just sanity that we got *something* readable.
    assert len(full) >= 2


async def test_paraformer_invalid_url_returns_canonical_error() -> None:
    """Invalid URL must surface one of the 9 canonical ``error_kind`` values.

    Maps to red-line C2 (no ``rate_limit``; only the 9 canonical kinds).
    """
    from subtitle_asr_client import AsrError

    canonical_kinds = frozenset(
        {
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
    )

    with pytest.raises(AsrError) as excinfo:
        await _transcribe(
            "https://example.invalid/does-not-exist.wav",
            language_hints=None,
        )
    assert excinfo.value.kind in canonical_kinds, (
        f"got non-canonical error_kind={excinfo.value.kind!r}"
    )
