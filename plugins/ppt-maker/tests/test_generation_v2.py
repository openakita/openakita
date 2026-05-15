from __future__ import annotations

import json

from ppt_design import DesignBuilder
from ppt_generation_models import GenerationBrief, RenderModel
from ppt_ir import SlideIrBuilder
from ppt_models import DeckMode, ProjectCreate
from ppt_outline import OutlineBuilder
from ppt_render_model import save_generation_artifacts


def test_generation_v2_models_validate_required_contract() -> None:
    brief = GenerationBrief(title="Roadmap", mode=DeckMode.TOPIC_TO_DECK)

    assert brief.title == "Roadmap"
    assert brief.output_mode == "editable"


def test_save_generation_artifacts_writes_expected_files(tmp_path) -> None:
    project = ProjectCreate(
        mode=DeckMode.TOPIC_TO_DECK,
        title="Roadmap",
        prompt="Build a roadmap deck",
        style="tech_business",
        slide_count=3,
    )
    project = project.model_copy(update={"id": "project_1"})
    outline = OutlineBuilder().build(mode=DeckMode.TOPIC_TO_DECK, title="Roadmap", slide_count=3)
    design = DesignBuilder().build(outline=outline)
    ir = SlideIrBuilder().build(outline=outline, spec_lock=design["spec_lock"])

    paths = save_generation_artifacts(
        project=project,
        settings={"quality_mode": "standard", "output_mode": "editable"},
        outline=outline,
        spec_lock=design["spec_lock"],
        slides_ir=ir,
        output_dir=tmp_path,
    )

    assert {
        "brief.json",
        "context_pack.json",
        "story_plan.json",
        "design_system.json",
        "slide_specs.json",
        "render_model.json",
    } <= set(paths)
    render_model = RenderModel.model_validate(
        json.loads((tmp_path / "render_model.json").read_text(encoding="utf-8"))
    )
    assert render_model.slides
    assert render_model.design_system.primary_color


def test_swiss_generation_artifacts_carry_image_guidance(tmp_path) -> None:
    project = ProjectCreate(
        mode=DeckMode.TOPIC_TO_DECK,
        title="Swiss Roadmap",
        prompt="Build a Swiss style roadmap deck",
        style="swiss_ikb",
        slide_count=3,
    )
    project = project.model_copy(update={"id": "project_swiss"})
    outline = OutlineBuilder().build(mode=DeckMode.TOPIC_TO_DECK, title="Swiss Roadmap", slide_count=3)
    design = DesignBuilder().build(outline=outline, style="swiss_ikb")
    ir = SlideIrBuilder().build(outline=outline, spec_lock=design["spec_lock"])

    save_generation_artifacts(
        project=project,
        settings={"quality_mode": "standard", "output_mode": "editable"},
        outline=outline,
        spec_lock=design["spec_lock"],
        slides_ir=ir,
        output_dir=tmp_path,
    )

    design_system = json.loads((tmp_path / "design_system.json").read_text(encoding="utf-8"))
    assert design_system["theme_id"] == "swiss_ikb"
    assert "no title, no footer" in design_system["image_style"]

