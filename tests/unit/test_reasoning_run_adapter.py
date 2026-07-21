from types import SimpleNamespace

import pytest

from openakita.core._reasoning_engine_legacy import ReasoningEngine


def _engine_with_events(events):
    async def _reason_stream(*_args, **_kwargs):
        for event in events:
            yield event

    return SimpleNamespace(reason_stream=_reason_stream)


@pytest.mark.asyncio
async def test_run_aggregates_the_canonical_stream_events():
    engine = _engine_with_events(
        [
            {"type": "text_delta", "content": "draft"},
            {"type": "text_replace", "content": "final"},
            {"type": "text_delta", "content": " answer"},
            {"type": "done"},
        ]
    )

    result = await ReasoningEngine.run(engine, [], tools=[])

    assert result == "final answer"


@pytest.mark.asyncio
async def test_run_propagates_stream_errors_when_no_answer_exists():
    engine = _engine_with_events(
        [
            {"type": "error", "message": "provider unavailable"},
            {"type": "done"},
        ]
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await ReasoningEngine.run(engine, [], tools=[])


@pytest.mark.asyncio
async def test_run_forwards_chain_progress_to_legacy_callback():
    seen = []
    engine = _engine_with_events(
        [
            {"type": "chain_text", "content": "checking"},
            {"type": "text_delta", "content": "done"},
            {"type": "done"},
        ]
    )

    async def _progress(text):
        seen.append(text)

    result = await ReasoningEngine.run(engine, [], tools=[], progress_callback=_progress)

    assert result == "done"
    assert seen == ["checking"]
