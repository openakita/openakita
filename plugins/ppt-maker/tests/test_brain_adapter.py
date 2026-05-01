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


# ── Three-stage Brain pipeline (outline → layout → per-slide content) ────


@pytest.mark.asyncio
async def test_generate_outline_returns_body_and_bullets(tmp_path) -> None:
    payload = {
        "title": "AI 平台 2026",
        "mode": "topic_to_deck",
        "audience": "engineers",
        "storyline": ["背景", "方案", "落地"],
        "slides": [
            {
                "index": 1,
                "title": "封面",
                "purpose": "建立主题",
                "slide_type": "cover",
                "key_points": [],
                "body": "AI 平台 2026 战略汇报。",
                "speaker_note": "欢迎大家。",
                "image_query": "modern data center",
                "icon_query": None,
            },
            {
                "index": 2,
                "title": "核心方案",
                "purpose": "讲方案",
                "slide_type": "content",
                "key_points": ["统一接入", "自动化", "可观测"],
                "body": "用三层架构整合现有能力，按季度推进试点。",
                "speaker_note": "方案三大支柱。",
                "image_query": None,
                "icon_query": "rocket",
            },
        ],
        "confirmation_questions": ["页数是否合适？"],
    }
    brain = FakeBrain(payload)
    adapter = PptBrainAdapter(FakeApi(granted=True, brain=brain), data_root=tmp_path)

    outline = await adapter.generate_outline(
        mode=DeckMode.TOPIC_TO_DECK,
        requirements={"title": "AI 平台 2026", "slide_count": 2},
        context="## Source\nKey context here",
        project_id="ppt_outline",
        verbosity="balanced",
    )

    assert outline.title == "AI 平台 2026"
    assert len(outline.slides) == 2
    assert outline.slides[0].slide_type.value == "cover"
    assert all(slide.body for slide in outline.slides)
    # The verbosity hint and context must reach the prompt.
    sent_prompt = brain.calls[0]["prompt"]
    assert "40 words" in sent_prompt
    assert "Source" in sent_prompt


@pytest.mark.asyncio
async def test_select_layout_per_slide_validates_slide_types(tmp_path) -> None:
    payload = {
        "slides": [
            {"index": 1, "slide_type": "cover"},
            {"index": 2, "slide_type": "agenda"},
            {"index": 3, "slide_type": "comparison"},
            {"index": 4, "slide_type": "summary"},
        ]
    }
    brain = FakeBrain(payload)
    adapter = PptBrainAdapter(FakeApi(granted=True, brain=brain), data_root=tmp_path)

    plan = await adapter.select_layout_per_slide(
        outline={
            "slides": [
                {"index": 1, "title": "Cover", "key_points": [], "body": "Hi"},
                {"index": 2, "title": "Agenda", "key_points": ["A"], "body": ""},
                {"index": 3, "title": "Compare", "key_points": ["A vs B"], "body": ""},
                {"index": 4, "title": "End", "key_points": ["next"], "body": ""},
            ]
        },
        project_id="ppt_layout",
    )

    assert [c.index for c in plan.slides] == [1, 2, 3, 4]
    assert plan.slides[0].slide_type.value == "cover"
    assert plan.slides[2].slide_type.value == "comparison"


@pytest.mark.asyncio
async def test_select_layout_invalid_slide_type_is_rejected(tmp_path) -> None:
    brain = FakeBrain({"slides": [{"index": 1, "slide_type": "totally_made_up"}]})
    adapter = PptBrainAdapter(FakeApi(granted=True, brain=brain), data_root=tmp_path)

    with pytest.raises(ValidationError):
        await adapter.select_layout_per_slide(
            outline={"slides": [{"index": 1, "title": "X"}]},
            project_id="ppt_layout_bad",
        )


@pytest.mark.asyncio
async def test_generate_slide_content_returns_validated_dict(tmp_path) -> None:
    from ppt_models import SlideType

    payload = {
        "body": "围绕统一接入展开三大支柱。",
        "bullets": ["统一身份", "事件中心", "可观测平台"],
        "image_query": "platform architecture",
        "icon_query": "rocket",
        "speaker_note": "重点强调统一接入。",
    }
    brain = FakeBrain(payload)
    adapter = PptBrainAdapter(FakeApi(granted=True, brain=brain), data_root=tmp_path)

    content = await adapter.generate_slide_content_per_slide(
        slide_outline={
            "index": 2,
            "title": "核心方案",
            "body": "讲方案",
            "key_points": ["统一接入"],
        },
        slide_type=SlideType.CONTENT,
        deck_title="AI 平台 2026",
        project_id="ppt_slide",
    )

    assert content["body"].startswith("围绕统一接入")
    assert len(content["bullets"]) == 3
    # Schema is layout_for(CONTENT), so the dict only contains allowed keys.
    assert "speaker_note" in content


@pytest.mark.asyncio
async def test_generate_slide_content_rejects_garbage_json(tmp_path) -> None:
    from ppt_models import SlideType

    class BadBrain:
        async def think(self, prompt, *, system, max_tokens):
            return FakeResponse("not even close to JSON")

    adapter = PptBrainAdapter(FakeApi(granted=True, brain=BadBrain()), data_root=tmp_path)

    with pytest.raises((ValueError, ValidationError)):
        await adapter.generate_slide_content_per_slide(
            slide_outline={"index": 1, "title": "x", "body": "", "key_points": []},
            slide_type=SlideType.CONTENT,
            project_id="ppt_bad_json",
        )

