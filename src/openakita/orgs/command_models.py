"""Command models for the v2 ``OrgCommandService`` (P-RC-9 P9.4).

Duplicates v1 ``openakita.orgs.command_service`` data classes
under the v2 namespace so ``runtime/orgs/command_service.py``
has zero ``openakita.orgs.*`` imports (P-RC-9-PLAN section
0.3 invariant) and v1 can be deleted wholesale at P9.9.
Parity is enforced byte-for-byte via ``to_dict()`` round-trip.
``new_command_id`` follows the Nit-1 fold-in pattern from
``scheduler_models.new_schedule_id`` (wall-clock prefix +
monotonic counter + 6-hex random).

ADR refs: ADR-0011 (subsystem decomposition; shared model
layer), ADR-0012 (no shim under v1).
"""

from __future__ import annotations

import itertools
import secrets
import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "ForwardTarget",
    "OrgCommandConflict",
    "OrgCommandError",
    "OrgCommandRequest",
    "OrgCommandResponse",
    "OrgCommandSource",
    "OrgCommandSurface",
    "OrgOutputScope",
    "default_scope_for_surface",
    "new_command_id",
    "origin_surface_label_cn",
]


# ---------------------------------------------------------------------------
# ID minting (Nit-1 fold-in -- monotonic counter + wall-clock prefix)
# ---------------------------------------------------------------------------


# Process-wide monotonic counter + lock; identical pattern to
# ``scheduler_models.new_schedule_id`` (see that module for the long
# rationale -- duplicating it here adds no value).
_ID_COUNTER = itertools.count(0)
_ID_LOCK = threading.Lock()


def new_command_id() -> str:
    """Mint a fresh command id.

    Layout: ``cmd_<13-digit ms>_<8-digit counter>_<6 hex>``.
    Loosely sortable across runs; strictly monotonic within a
    process even on NTP rollback (Nit-1 from G-RC-9.2). The
    8-digit counter wraps at 10**8; the random suffix
    differentiates past that ceiling (Nit-2 of G-RC-9.3
    tracked nits, deferred to G-RC-9 final).
    """
    ts_ms = int(time.time() * 1000)
    with _ID_LOCK:
        seq = next(_ID_COUNTER)
    rand = secrets.token_hex(3)  # 6 hex chars
    return f"cmd_{ts_ms:013d}_{seq:08d}_{rand}"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class OrgCommandError(Exception):
    """Base error for organisation command submission.

    Mirrors v1 ``openakita.orgs.command_service.OrgCommandError`` --
    ``status_code = 400`` is read by the API layer to build the
    HTTP response.
    """

    status_code: int = 400


class OrgCommandConflict(OrgCommandError):
    """Raised when a root node already has a running command.

    Mirrors v1 ``OrgCommandConflict`` -- ``status_code = 409``
    plus a ``command_id`` field so the caller can present the
    in-flight command's id (often used by the UI to offer a
    "replace existing?" button).
    """

    status_code: int = 409

    def __init__(self, message: str, *, command_id: str) -> None:
        super().__init__(message)
        self.command_id = command_id


# ---------------------------------------------------------------------------
# Enums (StrEnum so JSON encoding is parity-safe)
# ---------------------------------------------------------------------------


class OrgOutputScope(StrEnum):
    """How much of the command output is delivered to which surface.

    Values match v1 ``OrgOutputScope`` byte-for-byte so REST
    payloads round-trip across the v1/v2 boundary.
    """

    INTERNAL = "internal"
    CONSOLE_FULL = "console_full"
    CHAT_SUMMARY = "chat_summary"
    IM_SUMMARY = "im_summary"
    FINAL_ONLY = "final_only"


class OrgCommandSurface(StrEnum):
    """The surface (UI / IM / desktop chat) that submitted the command.

    Values match v1 ``OrgCommandSurface`` byte-for-byte.
    """

    ORG_CONSOLE = "org_console"
    DESKTOP_CHAT = "desktop_chat"
    IM = "im"


# ---------------------------------------------------------------------------
# Source + forward target dataclasses
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class OrgCommandSource:
    """Who submitted the command + via which channel.

    ``to_dict`` shape matches v1 byte-for-byte (parity gate).
    """

    channel: str = "desktop"
    chat_id: str = ""
    user_id: str = "desktop_user"
    thread_id: str | None = None
    client_id: str = ""
    display_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "chat_id": self.chat_id,
            "user_id": self.user_id,
            "thread_id": self.thread_id,
            "client_id": self.client_id,
            "display_name": self.display_name,
        }


@dataclass(slots=True)
class ForwardTarget:
    """Where to mirror command status / results to an extra IM chat.

    ``channel`` matches the IM adapter key registered on the
    gateway (``feishu`` / ``telegram`` / ``dingtalk`` /
    ``wecom`` / ``qq``); ``chat_id`` is the conversation id.
    ``from_dict`` is permissive (returns ``None`` on malformed
    input) -- byte-for-byte parity with v1.
    """

    channel: str
    chat_id: str
    thread_id: str | None = None
    bot_instance_id: str = ""
    label: str = ""

    @classmethod
    def from_dict(cls, raw: Any) -> ForwardTarget | None:
        if not isinstance(raw, dict):
            return None
        channel = str(raw.get("channel") or "").strip()
        chat_id = str(raw.get("chat_id") or "").strip()
        if not channel or not chat_id:
            return None
        return cls(
            channel=channel,
            chat_id=chat_id,
            thread_id=(raw.get("thread_id") or None),
            bot_instance_id=str(raw.get("bot_instance_id") or ""),
            label=str(raw.get("label") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "chat_id": self.chat_id,
            "thread_id": self.thread_id,
            "bot_instance_id": self.bot_instance_id,
            "label": self.label,
        }


# ---------------------------------------------------------------------------
# Request / response (parity round-trip)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class OrgCommandRequest:
    """A user (or IM) command submission.

    v1 ``OrgCommandRequest`` has no ``to_dict`` but the parity
    contract (P-RC-9-PLAN section 5.2: *assert verb dispatch
    produces the same OrgCommandRequest.to_dict() on both
    paths*) requires one for byte-for-byte comparison. The
    parity runner constructs an equivalent dict view for the
    v1 side so the comparison is fair.

    ``forward_to`` is the list of extra IM destinations to
    mirror final result / cancellation to.
    """

    org_id: str
    content: str
    target_node_id: str | None = None
    source: OrgCommandSource = field(default_factory=OrgCommandSource)
    origin_surface: OrgCommandSurface = OrgCommandSurface.ORG_CONSOLE
    output_scope: OrgOutputScope = OrgOutputScope.CONSOLE_FULL
    replace_existing: bool = False
    continue_previous: bool = False
    forward_to: list[ForwardTarget] = field(default_factory=list)
    user_facing_content: str | None = None
    """Original user text to persist/render when ``content`` carries hidden
    attachment text (inlined file contents). Falls back to ``content``."""
    input_attachments: list[dict[str, Any]] = field(default_factory=list)
    """User-uploaded attachments shown in the command-console history bubble."""

    def to_dict(self) -> dict[str, Any]:
        """Byte-for-byte parity view of the request.

        Used by the P9.4c parity harness (P-RC-9-PLAN section
        5.2). Field order matches v1 ``submit`` record
        construction modulo the ignore set (``command_id`` /
        ``created_at`` / ``updated_at`` / ``delivered_to``).
        """
        return {
            "org_id": self.org_id,
            "content": self.content,
            "target_node_id": self.target_node_id,
            "source": self.source.to_dict(),
            "origin_surface": self.origin_surface.value,
            "output_scope": self.output_scope.value,
            "replace_existing": self.replace_existing,
            "continue_previous": self.continue_previous,
            "forward_to": [ft.to_dict() for ft in self.forward_to],
        }


@dataclass(slots=True)
class OrgCommandResponse:
    """Typed view of the ``submit`` return value.

    v1 returns a bare dict; v2 keeps the bare-dict return
    (parity gate) and exposes this typed view for the P9.4c
    parity harness ``to_dict()`` round-trip.
    """

    command_id: str
    status: str
    root_node_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "status": self.status,
            "root_node_id": self.root_node_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> OrgCommandResponse:
        return cls(
            command_id=str(d.get("command_id") or ""),
            status=str(d.get("status") or ""),
            root_node_id=str(d.get("root_node_id") or ""),
        )


# ---------------------------------------------------------------------------
# Helper labels (small enough to live next to the enums)
# ---------------------------------------------------------------------------


def origin_surface_label_cn(surface: OrgCommandSurface) -> str:
    """Short Chinese label for blackboard / operator visibility.

    Byte-for-byte mirror of v1 ``_origin_surface_label_cn``;
    the literals are part of the parity contract (visible on
    the v1 UI via the blackboard mirror body).
    """
    if surface == OrgCommandSurface.IM:
        return "即时通讯"  # IM
    if surface == OrgCommandSurface.DESKTOP_CHAT:
        return "桌面聊天"  # desktop chat
    if surface == OrgCommandSurface.ORG_CONSOLE:
        return "组织指挥台"  # org command console
    return str(surface.value)


def default_scope_for_surface(
    surface: OrgCommandSurface,
    *,
    chat_type: str | None = None,
) -> OrgOutputScope:
    """Pick the right :class:`OrgOutputScope` for a given surface.

    Byte-for-byte mirror of v1 ``default_scope_for_surface``.
    IM group -> FINAL_ONLY (chat-room noise control); IM
    private -> IM_SUMMARY; everything else -> the
    surface-natural scope or FINAL_ONLY as a safe default.
    """
    if surface == OrgCommandSurface.ORG_CONSOLE:
        return OrgOutputScope.CONSOLE_FULL
    if surface == OrgCommandSurface.DESKTOP_CHAT:
        return OrgOutputScope.CHAT_SUMMARY
    if surface == OrgCommandSurface.IM:
        return OrgOutputScope.FINAL_ONLY if chat_type == "group" else OrgOutputScope.IM_SUMMARY
    return OrgOutputScope.FINAL_ONLY
