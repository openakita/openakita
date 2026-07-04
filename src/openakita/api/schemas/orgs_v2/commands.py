"""Command-level wire shapes (P9.7a-2b skeleton).

Matches ``runtime.orgs.command_service.OrgCommandService`` surface;
``source`` / ``forward_to`` ride as opaque dicts.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "CancelRequest",
    "CommandRead",
    "CommandSnapshot",
    "CommandSubmit",
    "OrgCommandSurface",
    "OrgOutputScope",
]


class OrgCommandSurface(StrEnum):
    """Parity with ``runtime.orgs.command_models.OrgCommandSurface``."""

    ORG_CONSOLE = "org_console"
    DESKTOP_CHAT = "desktop_chat"
    IM = "im"


class OrgOutputScope(StrEnum):
    """Parity with ``runtime.orgs.command_models.OrgOutputScope``."""

    INTERNAL = "internal"
    CONSOLE_FULL = "console_full"
    CHAT_SUMMARY = "chat_summary"
    IM_SUMMARY = "im_summary"
    FINAL_ONLY = "final_only"


# Frontend clients (OrgEditorView, PixelOfficeView) historically POSTed
# `origin_surface=desktop` or `web` against this endpoint. The canonical
# enum values are `desktop_chat` / `org_console` / `im`; without an alias
# layer those legacy payloads dead-end at 422. The alias map normalizes
# the common short forms before enum coercion so the wire contract stays
# canonical while older callers keep working.
_ORIGIN_SURFACE_ALIASES: dict[str, str] = {
    "desktop": OrgCommandSurface.DESKTOP_CHAT.value,
    "web": OrgCommandSurface.DESKTOP_CHAT.value,
    "console": OrgCommandSurface.ORG_CONSOLE.value,
}


class CommandSubmit(BaseModel):
    """Body for ``POST /api/v2/orgs/{id}/command`` -- ``content`` required."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., min_length=1)
    target_node_id: str | None = None
    source: dict[str, Any] | None = None
    origin_surface: OrgCommandSurface = OrgCommandSurface.ORG_CONSOLE
    # exploratory v12 §10.1: callers that omit ``output_scope`` (mobile,
    # CLI, IM bridge default body) used to land in ``command_service``
    # with a ``None``, which crashed on ``.value``. We default to
    # ``INTERNAL`` here because v12 §7 E5 verified 5 concurrent
    # internal-scope submits return 200. The field is intentionally
    # *not* ``Optional`` so an explicit ``null`` is now a 422 (was a
    # latent 500), which is the safer contract.
    output_scope: OrgOutputScope = OrgOutputScope.INTERNAL
    replace_existing: bool = Field(
        default=False,
        description=(
            "When ``true`` and another command for the same root node is "
            "already running, the service cancels that command (via the "
            "Supervisor's CancellationToken), waits for it to write a "
            "final 'cancelled' checkpoint, then submits this one. When "
            "``false`` the conflict raises 409 ``org_command_conflict`` "
            "and the new command is not accepted."
        ),
    )
    continue_previous: bool = Field(
        default=False,
        description=(
            "When ``true``, attempt to resume the most-recent terminated "
            "command for the same root node from its last checkpoint. "
            "The Supervisor restores TaskLedger / history / stall "
            "counters and treats the user's new ``content`` as a "
            "follow-up instruction. When the previous command has no "
            "checkpoint on disk the service falls back to the legacy "
            "content-concatenation continuation."
        ),
    )
    forward_to: list[dict[str, Any]] = Field(default_factory=list)
    attachments: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "User-uploaded files from the command console composer. Each item "
            "is a setup-center attachment descriptor (name / local_path / url / "
            "upload_id / size / mime_type). Text file contents are inlined into "
            "the execution prompt while the original text stays in the console "
            "history; non-text files contribute their local path."
        ),
    )

    @field_validator("origin_surface", mode="before")
    @classmethod
    def _normalize_origin_surface(cls, v: Any) -> Any:
        if isinstance(v, str):
            return _ORIGIN_SURFACE_ALIASES.get(v.lower(), v)
        return v


class CommandSnapshot(BaseModel):
    """Read shape for ``GET /api/v2/orgs/{id}/commands/{cid}``."""

    model_config = ConfigDict(extra="forbid")

    command_id: str
    org_id: str
    root_node_id: str = ""
    status: str
    content: str = ""
    origin_surface: str = ""
    output_scope: str = ""
    created_at: str = ""
    updated_at: str = ""
    delivered_to: list[dict[str, Any]] = Field(default_factory=list)


class CommandRead(BaseModel):
    """Sprint-9 wire shape for ``GET /api/v2/orgs/{id}/commands/{cid}``.

    Superset of :class:`CommandSnapshot` with the five
    supervisor-level observability fields the Supervisor HTTP
    takeover (Sprint-9) added:

    * ``progress_ledger`` -- the most recent
      :class:`~openakita.runtime.ledger.ProgressLedger.to_jsonable`
      output the brain produced for this command, or ``None`` when
      the supervisor has not run a turn yet.
    * ``n_stalls`` -- how many SUSPECT verdicts the stall detector
      has recorded; resets on a successful REPLAN.
    * ``n_turns`` -- turn counter incremented at the start of every
      inner-loop iteration.
    * ``last_checkpoint_id`` -- ULID-like checkpoint id written by
      :meth:`Supervisor._checkpoint`; clients can pass this back as
      a ``checkpoint_id`` query to fetch state diffs.
    * ``replan_count`` -- number of times the outer loop has reset
      facts + plan; bounded by ``Supervisor.cfg.max_replans``.

    Pre-Sprint-9 ``CommandSnapshot`` keeps shipping for callers that
    have not migrated yet; the dispatch route emits whichever shape
    the caller asked for via ``Accept`` header negotiation (default
    is the new ``CommandRead`` for ``application/json``).

    Field set is intentionally a strict superset of
    ``get_status`` so existing UI keeps reading the same keys; the
    new keys are opt-in (renderable as a "progress timeline" widget
    when present, hidden otherwise).
    """

    model_config = ConfigDict(extra="allow")

    command_id: str
    org_id: str = ""
    root_node_id: str = ""
    status: str
    phase: str = ""
    result: Any | None = None
    error: str | None = None
    elapsed_s: float | None = None
    origin_surface: str | None = None
    output_scope: str | None = None
    cancel_requested_by_user: bool = False
    event_ref: str | None = None
    # Sprint-9: supervisor-level observability.
    progress_ledger: dict[str, Any] | None = None
    n_stalls: int = 0
    n_turns: int = 0
    last_checkpoint_id: str | None = None
    replan_count: int = 0


class CancelRequest(BaseModel):
    """Body for ``POST .../commands/{cid}/cancel``."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(
        default=None,
        description=(
            "Optional free-form reason recorded on the Supervisor's "
            "CancellationToken; surfaces verbatim on the resulting "
            "cancelled checkpoint + lifecycle event payload."
        ),
    )
