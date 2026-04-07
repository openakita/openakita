"""
Agent 能力边界与 Fallback 策略

当专用 Agent 无法处理用户请求时:
1. 检测能力边界（技能未覆盖、连续失败等）
2. 建议切换到 fallback Agent（通常是 default 通用 Agent）
3. 记录健康度指标用于自动降级
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from .profile import AgentProfile, ProfileStore

logger = logging.getLogger(__name__)

_FAILURE_WINDOW_SECONDS = 300  # 5 分钟窗口
_AUTO_DEGRADE_THRESHOLD = 3  # 连续失败 N 次自动降级


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
    Fallback 解析器：根据 Agent 健康度决定是否降级到 fallback Profile。
    """

    def __init__(self, profile_store: ProfileStore):
        self._store = profile_store
        self._health: dict[str, _HealthEntry] = {}
        self._lock = threading.Lock()

    def resolve_fallback(self, profile_id: str) -> AgentProfile | None:
        """
        查找 fallback Profile。如果当前 profile 有 fallback_profile_id
        且该 profile 存在，返回 fallback profile；否则返回 None。
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
        """当前 agent 是否应降级到 fallback"""
        with self._lock:
            entry = self._health.get(profile_id)
            return entry is not None and entry.degraded

    def get_effective_profile(self, profile_id: str) -> str:
        """
        获取实际应使用的 profile ID。

        如果当前 profile 已降级且有 fallback，返回 fallback ID。
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
        为 IM/Chat 用户生成 fallback 建议文案。
        返回 None 表示不需要建议。
        """
        if not self.should_use_fallback(profile_id):
            return None
        fb_profile = self.resolve_fallback(profile_id)
        if not fb_profile:
            return None
        return f"⚠️ 当前 Agent 连续处理失败，已自动切换到 **{fb_profile.get_display_name()}** 处理。"
