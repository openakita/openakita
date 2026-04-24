"""Feature registry — declarative feature definitions, parameter schemas, and examples.

Each sub-feature (e.g. "主图复刻", "爆款复刻") is a FeatureDefinition that declares
its input form, execution strategy, API provider, and example gallery.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class FeatureParam:
    """Declares one input control on the feature form."""

    id: str
    type: str  # text | textarea | select | image_upload | multi_image
    #            | number | toggle | slider | color
    label: str
    label_en: str = ""
    options: list[dict] | None = None
    default: Any = None
    required: bool = False
    placeholder: str = ""
    group: str = "basic"  # basic | advanced | style
    visible_when: list[dict] | None = None
    order: int = 0

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "label": self.label,
            "label_en": self.label_en,
            "required": self.required,
            "placeholder": self.placeholder,
            "group": self.group,
            "order": self.order,
        }
        if self.options is not None:
            d["options"] = self.options
        if self.default is not None:
            d["default"] = self.default
        if self.visible_when:
            d["visible_when"] = self.visible_when
        return d


@dataclass
class FeatureExample:
    """A gallery entry — clicking it auto-fills the form."""

    id: str
    title: str
    description: str = ""
    thumbnail: str = ""
    preset_params: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "thumbnail": self.thumbnail,
            "preset_params": self.preset_params,
            "tags": self.tags,
        }


@dataclass
class FeatureDefinition:
    """Complete declaration of one sub-feature."""

    id: str
    name: str
    name_en: str
    module: str  # video | image | detail | poster
    description: str
    icon: str = ""

    params: list[FeatureParam] = field(default_factory=list)

    output_type: str = "image"  # image | video | images
    execution_mode: str = "prompt_template"
    execution_config: dict = field(default_factory=dict)

    api_provider: str = "dashscope"  # dashscope | ark
    default_model: str = ""
    api_capability: str = "multimodal"

    prompt_template: str | None = None

    examples: list[FeatureExample] = field(default_factory=list)

    batch_capable: bool = False
    estimated_cost: str = ""

    def to_dict(self, include_examples: bool = True) -> dict:
        d: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "name_en": self.name_en,
            "module": self.module,
            "description": self.description,
            "icon": self.icon,
            "output_type": self.output_type,
            "execution_mode": self.execution_mode,
            "api_provider": self.api_provider,
            "default_model": self.default_model,
            "batch_capable": self.batch_capable,
            "estimated_cost": self.estimated_cost,
            "params": [p.to_dict() for p in self.params],
        }
        if include_examples:
            d["examples"] = [e.to_dict() for e in self.examples]
        return d


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_VALID_EXECUTION_MODES = {"prompt_template", "agent", "pipeline", "batch"}

_REQUIRED_CONFIG_KEYS: dict[str, set[str]] = {
    "agent": {"agent_system_prompt"},
    "pipeline": {"steps"},
    "batch": {"base_strategy", "variation_source"},
}


class FeatureRegistry:
    """Central registry for all features."""

    def __init__(self) -> None:
        self._features: dict[str, FeatureDefinition] = {}
        self._by_module: dict[str, list[FeatureDefinition]] = {}

    def register(self, feature: FeatureDefinition) -> None:
        if feature.execution_mode not in _VALID_EXECUTION_MODES:
            raise ValueError(
                f"Feature '{feature.id}': invalid execution_mode "
                f"'{feature.execution_mode}', must be one of {_VALID_EXECUTION_MODES}"
            )
        required = _REQUIRED_CONFIG_KEYS.get(feature.execution_mode, set())
        missing = required - set(feature.execution_config.keys())
        if missing:
            raise ValueError(
                f"Feature '{feature.id}': execution_config missing keys {missing} "
                f"for mode '{feature.execution_mode}'"
            )
        self._features[feature.id] = feature
        self._by_module.setdefault(feature.module, []).append(feature)

    def get(self, feature_id: str) -> FeatureDefinition | None:
        return self._features.get(feature_id)

    def list_by_module(self, module: str) -> list[FeatureDefinition]:
        return self._by_module.get(module, [])

    def list_all_grouped(self) -> dict[str, list[dict]]:
        result: dict[str, list[dict]] = {}
        for module in ("video", "image", "detail", "poster"):
            features = self._by_module.get(module, [])
            result[module] = [f.to_dict(include_examples=False) for f in features]
        return result

    @property
    def feature_ids(self) -> list[str]:
        return list(self._features.keys())
