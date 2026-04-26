from __future__ import annotations

import pytest
from pydantic import ValidationError

from ppt_models import (
    BUILTIN_TEMPLATES,
    ChartType,
    DeckMode,
    ERROR_HINTS,
    ErrorKind,
    ProjectCreate,
    SlideType,
    TemplateCategory,
)
from ppt_maker_inline.file_utils import (
    assert_within_root,
    resolve_plugin_data_root,
    safe_name,
    slugify,
)


def test_project_model_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ProjectCreate(mode=DeckMode.TOPIC_TO_DECK, title="Roadmap", unknown=True)  # type: ignore[call-arg]


def test_core_enums_cover_plan_scope() -> None:
    assert DeckMode.TABLE_TO_DECK.value == "table_to_deck"
    assert DeckMode.TEMPLATE_DECK.value == "template_deck"
    assert SlideType.DATA_TABLE.value == "data_table"
    assert ChartType.HORIZONTAL_BAR.value == "horizontal_bar"
    assert ErrorKind.TEMPLATE in ERROR_HINTS


def test_builtin_template_registry_has_five_categories() -> None:
    categories = {item.category for item in BUILTIN_TEMPLATES}

    assert categories == {
        TemplateCategory.BUSINESS,
        TemplateCategory.TECH,
        TemplateCategory.CONSULTING,
        TemplateCategory.EDUCATION,
        TemplateCategory.ACADEMIC,
    }


def test_path_helpers_keep_files_inside_data_root(tmp_path) -> None:
    data_root = resolve_plugin_data_root(tmp_path)
    child = data_root / "uploads" / safe_name("../bad name.pptx")

    assert data_root.name == "ppt-maker"
    assert safe_name("../bad name.pptx") == "bad_name.pptx"
    assert slugify("OpenAkita 插件生态") == "openakita-插件生态"
    assert assert_within_root(data_root, child) == child.resolve()
    with pytest.raises(ValueError):
        assert_within_root(data_root, tmp_path.parent / "escape.txt")

