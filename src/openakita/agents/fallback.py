"""
Agent capability boundaries and fallback strategy

When a specialized agent cannot handle a user request:
1. Detect capability boundaries (skill not covered, consecutive failures, etc.)
2. Suggest switching to a fallback agent (typically the default general-purpose agent)
3. Track health metrics for automatic degradation
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from .profile import AgentProfile, ProfileStore

logger = logging.getLogger(__name__)

_FAILURE_WINDOW_SECONDS = 300  # 5-minute window
_AUTO_DEGRADE_THRESHOLD = 3  # Auto-degrade after N consecutive failures


@dataclass
class _HealthEntry:
    profile_id: str
    consecutive_failures: int = 0
    total_requests: int = 0
    total_failures: int = 0
    last_failure_time: float = 0.0
    degraded: bool = False

    def record_success(self) -> None:
        self.total_requests += 1
        self.consecutive_failures = 0

    def record_failure(self) -> None:
        self.total_requests += 1
        self.total_failures += 1
        now = time.monotonic()
        if self.last_failure_time and (now - self.last_failure_time) > _FAILURE_WINDOW_SECONDS:
            self.consecutive_failures = 1
        else:
            self.consecutive_failures += 1
        self.last_failure_time = now

    @property
    def failure_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.total_failures / self.total_requests

    @property
    def should_degrade(self) -> bool:
        return self.consecutive_failures >= _AUTO_DEGRADE_THRESHOLD


class FallbackResolver:
    """
    Fallback resolver: decides whether to degrade to a fallback profile based on agent health.
    """

    def __init__(self, profile_store: ProfileStore):
        self._store = profile_store
        self._health: dict[str, _HealthEntry] = {}
        self._lock = threading.Lock()

    def resolve_fallback(self, profile_id: str) -> AgentProfile | None:
        """
        Look up the fallback profile. If the current profile has a fallback_profile_id
        and that profile exists, return the fallback profile; otherwise return None.
        """
        profile = self._store.get(profile_id)
        if not profile or not profile.fallback_profile_id:
            return None
        return self._store.get(profile.fallback_profile_id)

    def record_success(self, profile_id: str) -> None:
        with self._lock:
            entry = self._health.setdefault(profile_id, _HealthEntry(profile_id=profile_id))
            entry.record_success()
            if entry.degraded:
                entry.degraded = False
                logger.info(f"Agent {profile_id} recovered from degraded state")

    def record_failure(self, profile_id: str) -> None:
        with self._lock:
            entry = self._health.setdefault(profile_id, _HealthEntry(profile_id=profile_id))
            entry.record_failure()
            if entry.should_degrade and not entry.degraded:
                entry.degraded = True
                logger.warning(
                    f"Agent {profile_id} auto-degraded after "
                    f"{entry.consecutive_failures} consecutive failures"
                )

    def should_use_fallback(self, profile_id: str) -> bool:
        """Whether the current agent should degrade to its fallback."""
        with self._lock:
            entry = self._health.get(profile_id)
            return entry is not None and entry.degraded

    def get_effective_profile(self, profile_id: str) -> str:
        """
        Get the profile ID that should actually be used.

        If the current profile is degraded and has a fallback, return the fallback ID.
        """
        if not self.should_use_fallback(profile_id):
            return profile_id
        profile = self._store.get(profile_id)
        if profile and profile.fallback_profile_id:
            fb = self._store.get(profile.fallback_profile_id)
            if fb:
                return fb.id
        return profile_id

    def get_health_stats(self) -> dict[str, dict]:
        with self._lock:
            return {
                pid: {
                    "total_requests": e.total_requests,
                    "total_failures": e.total_failures,
                    "consecutive_failures": e.consecutive_failures,
                    "failure_rate": round(e.failure_rate, 3),
                    "degraded": e.degraded,
                }
                for pid, e in self._health.items()
            }

    def build_fallback_hint(self, profile_id: str) -> str | None:
        """
        Generate a fallback hint message for IM/Chat users.
        Returns None if no hint is needed.
        """
        if not self.should_use_fallback(profile_id):
            return None
        fb_profile = self.resolve_fallback(profile_id)
        if not fb_profile:
            return None
        return f"⚠️ The current agent has failed consecutively and has been automatically switched to **{fb_profile.get_display_name()}**."
