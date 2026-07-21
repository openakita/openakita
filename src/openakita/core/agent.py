"""Backwards-compat shim for the pre-ADR-0003 import path.

Historically the agent lived in the monolithic ``openakita.core.agent`` module.
Under ADR-0003 it was split into the ``openakita.agent`` subpackage plus
``openakita.core._agent_legacy``. This thin module re-exports the canonical
:class:`Agent` so legacy import paths (``from openakita.core.agent import Agent``
/ ``import openakita.core.agent``) and upstream tests keep resolving.

It re-exports through the :mod:`openakita.core` package attribute so the object
is identical to ``from openakita.core import Agent`` and the safe lazy import
order (pre-loading ``openakita.agent`` to break the brain/llm/errors cycle) is
preserved. See ``docs/follow-ups/skipped-items-roadmap.md``.
"""

from __future__ import annotations

from openakita.config import settings
from openakita.core import Agent

# Desktop/IM attachment + vision-gating helpers historically lived at module
# scope in the monolithic ``core.agent``. Upstream unit tests import them via
# ``from openakita.core.agent import _format_vision_unavailable_notice`` etc.,
# so re-export the canonical aliases from ``_agent_legacy``.
from openakita.core._agent_legacy import (  # noqa: E402
    _format_desktop_attachment_reference,
    _format_vision_unavailable_notice,
    _has_pending_media_or_attachments,
)

# ``settings`` is re-exported because the monolithic ``core.agent`` historically
# exposed the global Settings singleton at module scope. Upstream tests still do
# ``monkeypatch.setattr(openakita.core.agent.settings, ...)``; since this is the
# same ``openakita.config.settings`` singleton that ``_agent_legacy`` reads, the
# patch is observed by ``Agent.execute_task`` and friends.
__all__ = [
    "Agent",
    "settings",
    "_format_desktop_attachment_reference",
    "_format_vision_unavailable_notice",
    "_has_pending_media_or_attachments",
]
