"""Aggregated health events for noisy background subsystems."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class HealthEvent:
    category: str
    key: str
    message: str
    severity: str = "warning"
    count: int = 1
    first_seen: float = 0.0
    last_seen: float = 0.0
    suppressed: int = 0
    suggestion: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["first_seen_iso"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.first_seen))
        data["last_seen_iso"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.last_seen))
        return data


class LogHealthRegistry:
    def __init__(self, rate_limit_seconds: float = 60.0) -> None:
        self.rate_limit_seconds = rate_limit_seconds
        self._events: dict[tuple[str, str], HealthEvent] = {}

    def record(
        self,
        category: str,
        key: str,
        message: str,
        *,
        severity: str = "warning",
        suggestion: str = "",
    ) -> bool:
        now = time.time()
        event_key = (category, key)
        existing = self._events.get(event_key)
        if existing is None:
            self._events[event_key] = HealthEvent(
                category=category,
                key=key,
                message=message[:1000],
                severity=severity,
                first_seen=now,
                last_seen=now,
                suggestion=suggestion,
            )
            return True
        previous_last_seen = existing.last_seen
        existing.count += 1
        existing.last_seen = now
        existing.message = message[:1000]
        existing.severity = severity
        if suggestion:
            existing.suggestion = suggestion
        if now - previous_last_seen < self.rate_limit_seconds:
            existing.suppressed += 1
            return False
        return True

    def summary(self) -> dict[str, Any]:
        events = sorted(self._events.values(), key=lambda item: item.last_seen, reverse=True)
        return {
            "status": "ok",
            "event_count": len(events),
            "events": [event.to_dict() for event in events[:200]],
        }

    def clear(self) -> None:
        self._events.clear()


_registry: LogHealthRegistry | None = None


def get_log_health_registry() -> LogHealthRegistry:
    global _registry
    if _registry is None:
        _registry = LogHealthRegistry()
    return _registry


def record_health_event(
    category: str,
    key: str,
    message: str,
    *,
    severity: str = "warning",
    suggestion: str = "",
) -> bool:
    return get_log_health_registry().record(
        category,
        key,
        message,
        severity=severity,
        suggestion=suggestion,
    )
