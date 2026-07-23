"""Built-in :class:`TemplateSpec` records.

Each module under this package contributes one template via the
:func:`runtime.templates.template` decorator. Importing the package
(typically through :func:`runtime.templates.discover_builtins`) is
enough to enqueue every built-in for registration; the registry's
``bootstrap`` then drains the queue.

Module-per-template is deliberate. The pre-v2 implementation had a single
1234-line ``orgs/templates.py`` that mixed 7 unrelated organisation
shapes together, which made every change a merge-conflict magnet and
made it impossible to diff a single template's evolution. We keep
templates one-file-per-template so that:

* ``git log -- runtime/templates/builtin/aigc_video_studio.py`` tells
  the full story of that one template.
* Adding a new template never touches an existing file (no churn).
* Removing or hot-swapping a template is a clean delete.
"""

from __future__ import annotations

__all__: list[str] = []
