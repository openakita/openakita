"""Tests for clip_asr_client.py — mock httpx for Paraformer + Qwen."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from clip_asr_client import (
    AsrError,
    ClipAsrClient,
    _flatten_sentences,
    _format_sentences_for_prompt,
)


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestFlattenSentences:
    def test_basic(self):
        payload = {
            "transcripts": [{
                "sentences": [
                    {"begin_time": 1000, "end_time": 3500, "text": "Hello world", "confidence": 0.95},
                    {"begin_time": 4000, "end_time": 6000, "text": "Test", "confidence": 0.88},
                ]
            }]
        }
        result = _flatten_sentences(payload)
        assert len(result) == 2
        assert result[0].start == 1.0
        assert result[0].end == 3.5
        assert result[0].text == "Hello world"
        assert result[0].confidence == 0.95

    def test_millisecond_to_second_conversion(self):
        payload = {"transcripts": [{"sentences": [
            {"begin_time": 500, "end_time": 1500, "text": "half sec"}
        ]}]}
        result = _flatten_sentences(payload)
        assert result[0].start == 0.5
        assert result[0].end == 1.5

    def test_empty_transcripts(self):
        assert _flatten_sentences({}) == []
        assert _flatten_sentences({"transcripts": []}) == []
        assert _flatten_sentences({"transcripts": [{"sentences": []}]}) == []

    def test_malformed_sentence_skipped(self):
        payload = {"transcripts": [{"sentences": [
            {"begin_time": "not_a_number", "end_time": 1000, "text": "bad"},
            {"begin_time": 2000, "end_time": 3000, "text": "good"},
        ]}]}
        result = _flatten_sentences(payload)
        assert len(result) == 1
        assert result[0].text == "good"


class TestFormatSentences:
    def test_basic(self):
        sentences = [
            {"start": 0.0, "end": 2.5, "text": "Hello"},
            {"start": 3.0, "end": 5.0, "text": "World"},
        ]
        result = _format_sentences_for_prompt(sentences)
        assert "[0.0s-2.5s] Hello" in result
        assert "[3.0s-5.0s] World" in result

    def test_truncation(self):
        sentences = [{"start": i, "end": i + 1, "text": "x" * 100} for i in range(300)]
        result = _format_sentences_for_prompt(sentences, max_chars=500)
        assert "截断" in result


class TestClipAsrClient:
    def test_init(self):
        c = ClipAsrClient(
            "sk-test",
            analysis_provider="dashscope",
            analysis_api_key="sk-analysis",
        )
        assert c._api_key == "sk-test"

    def test_update_api_key(self):
        c = ClipAsrClient("old")
        c.update_api_key("new")
        assert c._api_key == "new"

    def test_auth_headers_no_key(self):
        c = ClipAsrClient("")
        with pytest.raises(AsrError, match="API key"):
            c._auth_headers()

    def test_transcribe_no_url(self):
        c = ClipAsrClient(
            "sk-test",
            analysis_provider="dashscope",
            analysis_api_key="sk-analysis",
        )
        with pytest.raises(AsrError, match="source_url"):
            run(c.transcribe(""))


class TestQwenAnalysis:
    """Test Qwen analysis with mocked httpx."""

    @patch("clip_asr_client.ClipAsrClient._ensure_client")
    def test_analyze_highlights_success(self, mock_ensure):
        mock_client = AsyncMock()
        mock_ensure.return_value = mock_client

        highlights = [{"start_sec": 10, "end_sec": 40, "reason": "funny", "score": 8}]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": json.dumps(highlights)}}]
        }
        mock_client.post.return_value = mock_resp

        c = ClipAsrClient(
            "sk-test",
            analysis_provider="dashscope",
            analysis_api_key="sk-analysis",
        )
        sentences = [{"start": 0, "end": 60, "text": "test content"}]
        result = run(c.analyze_highlights("test", sentences))
        assert len(result) == 1
        assert result[0]["start_sec"] == 10

    @patch("clip_asr_client.ClipAsrClient._ensure_client")
    def test_analyze_with_markdown_fence(self, mock_ensure):
        mock_client = AsyncMock()
        mock_ensure.return_value = mock_client

        topics = [{"title": "Intro", "start_sec": 0, "end_sec": 60, "summary": "intro"}]
        fenced = f"```json\n{json.dumps(topics)}\n```"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": fenced}}]
        }
        mock_client.post.return_value = mock_resp

        c = ClipAsrClient(
            "sk-test",
            analysis_provider="dashscope",
            analysis_api_key="sk-analysis",
        )
        sentences = [{"start": 0, "end": 60, "text": "test"}]
        result = run(c.analyze_topics("test", sentences))
        assert len(result) == 1
        assert result[0]["title"] == "Intro"

    @patch("clip_asr_client.ClipAsrClient._ensure_client")
    def test_analyze_retry_on_bad_json(self, mock_ensure):
        mock_client = AsyncMock()
        mock_ensure.return_value = mock_client

        bad_resp = MagicMock()
        bad_resp.status_code = 200
        bad_resp.json.return_value = {
            "choices": [{"message": {"content": "not json at all"}}]
        }

        good_data = [{"start_sec": 1, "end_sec": 2, "type": "filler", "content": "um"}]
        good_resp = MagicMock()
        good_resp.status_code = 200
        good_resp.json.return_value = {
            "choices": [{"message": {"content": json.dumps(good_data)}}]
        }

        mock_client.post.side_effect = [bad_resp, good_resp]

        c = ClipAsrClient(
            "sk-test",
            analysis_provider="dashscope",
            analysis_api_key="sk-analysis",
        )
        c._client = mock_client
        sentences = [{"start": 0, "end": 5, "text": "um test"}]
        result = run(c.analyze_filler("um test", sentences))
        assert len(result) == 1

    @patch("clip_asr_client.ClipAsrClient._ensure_client")
    def test_analyze_all_retries_exhausted(self, mock_ensure):
        mock_client = AsyncMock()
        mock_ensure.return_value = mock_client

        bad_resp = MagicMock()
        bad_resp.status_code = 200
        bad_resp.json.return_value = {
            "choices": [{"message": {"content": "not json"}}]
        }
        mock_client.post.return_value = bad_resp

        c = ClipAsrClient(
            "sk-test",
            analysis_provider="dashscope",
            analysis_api_key="sk-analysis",
        )
        sentences = [{"start": 0, "end": 5, "text": "test"}]
        result = run(c.analyze_highlights("test", sentences))
        assert result == []

    def test_host_brain_analysis_default(self):
        class StubBrain:
            async def chat(self, **kwargs):
                return json.dumps([{"start_sec": 1, "end_sec": 3, "reason": "ok", "score": 8}])

        c = ClipAsrClient("sk-test", analysis_brain=StubBrain())
        sentences = [{"start": 0, "end": 5, "text": "test content"}]
        result = run(c.analyze_highlights("test", sentences))
        assert result[0]["start_sec"] == 1
