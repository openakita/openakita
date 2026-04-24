"""Unit tests for ``mediapost_seo_generator``.

Coverage targets per ``docs/media-post-plan.md`` §6.4 + §11 Phase 3:

- 5 platforms in ``PLATFORM_PROMPTS``.
- All 5 succeed → returns 5 dicts.
- 1 platform raises → other 4 still return.
- All-None → raise ``MediaPostError("format")`` (handled by caller).
- Markdown fence stripping + 2-pass JSON fallback.
- Subtitle excerpt truncated to 6000 chars.
- Empty platform list raises ``format``.
- Unknown platforms are filtered out (kept-list flow).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from mediapost_models import ALLOWED_PLATFORMS, MediaPostError
from mediapost_seo_generator import (
    PLATFORM_PROMPTS,
    SUBTITLE_EXCERPT_LIMIT,
    _parse_seo_json,
    _strip_json_fence,
    generate_seo_pack,
)


def _run(coro: Any) -> Any:
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def test_five_platform_prompts_present() -> None:
    assert set(PLATFORM_PROMPTS.keys()) == set(ALLOWED_PLATFORMS)
    for prompt in PLATFORM_PROMPTS.values():
        assert "{video_title_hint}" in prompt
        assert "{subtitle_excerpt}" in prompt
        assert "{instruction}" in prompt
        assert "{chapters}" in prompt


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


class TestStripJsonFence:
    def test_no_fence(self) -> None:
        assert _strip_json_fence('{"a":1}') == '{"a":1}'

    def test_json_fence(self) -> None:
        assert _strip_json_fence('```json\n{"a":1}\n```') == '{"a":1}'

    def test_unlabeled_fence(self) -> None:
        assert _strip_json_fence('```\n{"x":2}\n```') == '{"x":2}'

    def test_empty_returns_empty(self) -> None:
        assert _strip_json_fence("") == ""


class TestParseSeoJson:
    def test_clean_dict(self) -> None:
        out = _parse_seo_json('{"title": "abc"}', "tiktok")
        assert out == {"title": "abc"}

    def test_fenced(self) -> None:
        out = _parse_seo_json('```json\n{"title": "abc"}\n```', "youtube")
        assert out == {"title": "abc"}

    def test_with_preamble_uses_substring(self) -> None:
        out = _parse_seo_json('Here it is: {"title": "abc"} thanks', "wechat")
        assert out == {"title": "abc"}

    def test_unparsable_returns_none(self) -> None:
        assert _parse_seo_json("not json at all", "tiktok") is None

    def test_non_dict_returns_none(self) -> None:
        assert _parse_seo_json("[1,2,3]", "tiktok") is None

    def test_empty_returns_none(self) -> None:
        assert _parse_seo_json("", "tiktok") is None


# ---------------------------------------------------------------------------
# generate_seo_pack — full path
# ---------------------------------------------------------------------------


class _ScriptedQwen:
    def __init__(self, by_excerpt: dict[str, str | Exception]) -> None:
        self._scripts = by_excerpt
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        prompt = kwargs["messages"][0]["content"]
        for key, val in self._scripts.items():
            if key in prompt:
                if isinstance(val, Exception):
                    raise val
                return val
        return '{"title":"default"}'


class TestGenerateSeoPack:
    def test_all_platforms_succeed(self) -> None:
        client = _ScriptedQwen(
            {
                "TikTok": '{"title":"T","description":"d","hashtags":["#a"]}',
                "Bilibili": '{"title":"B","description":"d","tags":[],"chapters":[]}',
                "微信": '{"title":"W","description":"d","topics":[]}',
                "小红书": '{"title":"X","body":"b","hashtags":[]}',
                "YouTube": '{"title":"Y","description":"d","tags":[]}',
            }
        )
        out = _run(
            generate_seo_pack(
                video_title_hint="hello",
                subtitle_excerpt="abc",
                instruction="for fun",
                platforms=sorted(ALLOWED_PLATFORMS),
                qwen_plus_call=client,
            )
        )
        assert set(out.keys()) == set(ALLOWED_PLATFORMS)
        for payload in out.values():
            assert isinstance(payload, dict)
            assert "title" in payload

    def test_one_platform_raises_others_still_return(self) -> None:
        client = _ScriptedQwen(
            {
                "TikTok": MediaPostError("auth", "no key"),
                "Bilibili": '{"title":"B"}',
                "微信": '{"title":"W"}',
                "小红书": '{"title":"X","body":"b"}',
                "YouTube": '{"title":"Y"}',
            }
        )
        out = _run(
            generate_seo_pack(
                video_title_hint="",
                subtitle_excerpt="",
                instruction="",
                platforms=sorted(ALLOWED_PLATFORMS),
                qwen_plus_call=client,
            )
        )
        assert out["tiktok"] is None
        for p in ("bilibili", "wechat", "xiaohongshu", "youtube"):
            assert isinstance(out[p], dict)

    def test_invalid_platforms_filtered(self) -> None:
        client = _ScriptedQwen({"TikTok": '{"title":"ok"}'})
        out = _run(
            generate_seo_pack(
                video_title_hint="",
                subtitle_excerpt="",
                instruction="",
                platforms=["tiktok", "made_up_platform"],
                qwen_plus_call=client,
            )
        )
        assert list(out.keys()) == ["tiktok"]

    def test_no_valid_platforms_raises_format(self) -> None:
        async def _qwen(**kwargs: Any) -> str:
            return ""

        with pytest.raises(MediaPostError) as ei:
            _run(
                generate_seo_pack(
                    video_title_hint="",
                    subtitle_excerpt="",
                    instruction="",
                    platforms=["nope"],
                    qwen_plus_call=_qwen,
                )
            )
        assert ei.value.kind == "format"

    def test_subtitle_excerpt_truncated(self) -> None:
        long_excerpt = "x" * (SUBTITLE_EXCERPT_LIMIT + 100)
        client = _ScriptedQwen({"TikTok": '{"title":"ok"}'})
        _run(
            generate_seo_pack(
                video_title_hint="",
                subtitle_excerpt=long_excerpt,
                instruction="",
                platforms=["tiktok"],
                qwen_plus_call=client,
            )
        )
        assert client.calls
        full_prompt = client.calls[0]["messages"][0]["content"]
        # Excerpt copied at most SUBTITLE_EXCERPT_LIMIT chars.
        assert "x" * (SUBTITLE_EXCERPT_LIMIT + 1) not in full_prompt

    def test_chapters_emitted_when_requested(self) -> None:
        client = _ScriptedQwen({"YouTube": '{"title":"ok"}'})
        _run(
            generate_seo_pack(
                video_title_hint="",
                subtitle_excerpt="",
                instruction="",
                platforms=["youtube"],
                qwen_plus_call=client,
                include_chapters=True,
                chapter_timestamps=[{"timestamp": "00:30", "title": "intro"}],
            )
        )
        prompt = client.calls[0]["messages"][0]["content"]
        assert "intro" in prompt

    def test_unparsable_response_marks_platform_none(self) -> None:
        client = _ScriptedQwen({"TikTok": "no json here just words"})
        out = _run(
            generate_seo_pack(
                video_title_hint="",
                subtitle_excerpt="",
                instruction="",
                platforms=["tiktok"],
                qwen_plus_call=client,
            )
        )
        assert out["tiktok"] is None
