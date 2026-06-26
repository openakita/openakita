from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class ClientContext:
    install_id_hash: str
    version: str | None = None
    platform: str | None = None
    channel: str | None = None


@dataclass
class InboxMessage:
    id: str
    title: str
    body_markdown: str
    type: str = "notice"
    priority: str = "normal"
    cta: dict[str, Any] | None = None
    target_rule: dict[str, Any] = field(default_factory=dict)
    rollout_percent: int = 100
    publish_at: str | None = None
    expire_at: str | None = None
    source: str = "l0_broadcast"
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any], *, source: str) -> InboxMessage:
        cta = payload.get("cta") if isinstance(payload.get("cta"), dict) else None
        if cta is None and (payload.get("cta_label") or payload.get("cta_url")):
            cta = {"label": payload.get("cta_label"), "url": payload.get("cta_url")}
        return cls(
            id=str(payload.get("id") or payload.get("campaign_id") or ""),
            title=str(payload.get("title") or ""),
            body_markdown=str(payload.get("body_markdown") or payload.get("body") or ""),
            type=str(payload.get("type") or "notice"),
            priority=str(payload.get("priority") or "normal"),
            cta=cta,
            target_rule=payload.get("target_rule")
            if isinstance(payload.get("target_rule"), dict)
            else {},
            rollout_percent=int(payload.get("rollout_percent") or 100),
            publish_at=payload.get("publish_at") or payload.get("start_at"),
            expire_at=payload.get("expire_at") or payload.get("end_at"),
            source=source,
            raw=dict(payload),
        )


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()
