"""Organization templates — first-class blueprints for an :class:`OrgV2`.

Per ADR-0008, every built-in template (and every user-authored
custom template) declares its node graph as a typed
:class:`TemplateSpec`. The :class:`TemplateRegistry` validates,
stores, and instantiates templates into live ``OrgV2`` records.

This package is populated incrementally during Phase 5:

* :mod:`runtime.templates.schema` — typed dataclasses (this commit).
* :mod:`runtime.templates.registry` — registry + decorator + bootstrap
  (next commit).
* :mod:`runtime.templates.builtin.*` — one file per built-in
  template (subsequent commits, starting with
  ``aigc_video_studio.py``).
"""

from __future__ import annotations

from .schema import (
    DefaultsSpec,
    EdgeSpec,
    GuardrailSpec,
    NodeRuntimeOverridesSpec,
    NodeSpec,
    TemplateSpec,
    TemplateValidationError,
    WorkbenchBindingSpec,
)

__all__ = [
    "DefaultsSpec",
    "EdgeSpec",
    "GuardrailSpec",
    "NodeRuntimeOverridesSpec",
    "NodeSpec",
    "TemplateSpec",
    "TemplateValidationError",
    "WorkbenchBindingSpec",
]
