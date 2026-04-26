from __future__ import annotations

import json
from dataclasses import dataclass

import pytest
from ppt_brain_adapter import BrainAccessError, PptBrainAdapter
from ppt_models import DeckMode
from pydantic import ValidationError


@dataclass
class FakeResponse:
    content: str


class FakeBrain:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    async def think(self, prompt: str, *, system: str, max_tokens: int) -> FakeResponse:
        self.calls.append({"prompt": prompt, "system": system, "max_tokens": max_tokens})
        return FakeResponse(json.dumps(self.payload, ensure_ascii=False))


class FakeApi:
    def __init__(self, *, granted: bool, brain=None) -> None:
        self.granted = granted
        self.brain = brain

    def has_permission(self, name: str) -> bool:
        return self.granted and name == "brain.access"

    def get_brain(self):
        return self.brain


@pytest.mark.asyncio
async def test_build_requirement_questions_uses_brain_and_logs(tmp_path) -> None:
    brain = FakeBrain(
        {
            "mode": "topic_to_deck",
            "questions": [
                {
                    "id": "audience",
                    "question": "Who is the audience?",
                    "reason": "Deck tone depends on audience.",
                    "options": ["executives", "engineers"],
                    "required": True,
                }
            ],
            "recommended_slide_count": 8,
            "recommended_style": "tech_business",
        }
    )
    adapter = PptBrainAdapter(FakeApi(granted=True, brain=brain), data_root=tmp_path)

    result = await adapter.build_requirement_questions(
        mode=DeckMode.TOPIC_TO_DECK,
        user_prompt="OpenAkita plugin roadmap",
        project_id="ppt_1",
    )

    assert result.mode == DeckMode.TOPIC_TO_DECK
    assert result.questions[0].id == "audience"
    assert brain.calls[0]["max_tokens"] == 4096
    assert list((tmp_path / "projects" / "ppt_1" / "logs").glob("*_request.json"))
    assert list((tmp_path / "projects" / "ppt_1" / "logs").glob("*_response.json"))


def test_missing_brain_permission_raises(tmp_path) -> None:
    adapter = PptBrainAdapter(FakeApi(granted=False, brain=FakeBrain({})), data_root=tmp_path)

    with pytest.raises(BrainAccessError):
        adapter.get_brain()


@pytest.mark.asyncio
async def test_validation_error_is_logged(tmp_path) -> None:
    adapter = PptBrainAdapter(
        FakeApi(granted=True, brain=FakeBrain({"mode": "topic_to_deck", "questions": []})),
        data_root=tmp_path,
    )

    with pytest.raises(ValidationError):
        await adapter.generate_outline(
            mode=DeckMode.TOPIC_TO_DECK,
            requirements={"topic": "OpenAkita"},
            project_id="ppt_bad",
        )

    assert list((tmp_path / "projects" / "ppt_bad" / "logs").glob("*_validation_error.json"))


@pytest.mark.asyncio
async def test_generate_table_insights_validates_structured_output(tmp_path) -> None:
    brain = FakeBrain(
        {
            "key_findings": ["Revenue grew 12%"],
            "chart_suggestions": [{"type": "bar", "x": "month", "y": "revenue"}],
            "recommended_storyline": ["Overview", "Growth drivers"],
            "risks_and_caveats": ["Sample data only"],
        }
    )
    adapter = PptBrainAdapter(FakeApi(granted=True, brain=brain), data_root=tmp_path)

    result = await adapter.generate_table_insights(
        dataset_profile={"columns": [{"name": "revenue", "type": "number"}]}
    )

    assert result.key_findings == ["Revenue grew 12%"]
    assert result.chart_suggestions[0]["type"] == "bar"

