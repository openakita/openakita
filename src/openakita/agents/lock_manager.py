"""
LockManager — fine-grained per-resource async locks for multi-agent mode.

These locks prevent concurrent access to shared resources by multiple
agent instances in the always-on multi-agent system.
"""

import asyncio
import logging
import time
from contextlib import AbstractAsyncContextManager as AsyncContextManager

logger = logging.getLogger(__name__)


class ResourceLock:
    """A named async lock with metadata."""

    def __init__(self, name: str):
        self.name = name
        self._lock = asyncio.Lock()
        self.holder: str | None = None
        self.acquired_at: float = 0.0
        self.acquire_count: int = 0

    async def acquire(self, holder: str = "") -> None:
        await self._lock.acquire()
        self.holder = holder
        self.acquired_at = time.time()
        self.acquire_count += 1

    def release(self) -> None:
        self.holder = None
        self._lock.release()

    @property
    def locked(self) -> bool:
        return self._lock.locked()


class LockManager:
    """
    Per-resource async lock manager.

    Usage:
        lm = LockManager()
        async with lm.lock("file:/path/to/file", holder="code-assistant"):
            # exclusive access to the resource
            ...
    """

    def __init__(self):
        self._locks: dict[str, ResourceLock] = {}
        self._meta_lock = asyncio.Lock()

    async def _get_or_create(self, resource: str) -> ResourceLock:
        if resource not in self._locks:
            async with self._meta_lock:
                if resource not in self._locks:
                    self._locks[resource] = ResourceLock(resource)
        return self._locks[resource]

    def lock(
        self, resource: str, *, holder: str = "", timeout: float | None = None
    ) -> AsyncContextManager:
        """
        Get an async context manager for a resource lock.

        Args:
            resource: Resource identifier (e.g. "file:/path", "memory:agent_id", "tool:browser")
            holder: Who is holding the lock (agent_profile_id)
            timeout: Optional timeout in seconds
        """
        return _LockContext(self, resource, holder, timeout)

    async def is_locked(self, resource: str) -> bool:
        if resource in self._locks:
            return self._locks[resource].locked
        return False

    async def get_holder(self, resource: str) -> str | None:
        if resource in self._locks:
            return self._locks[resource].holder
        return None

    def get_stats(self) -> dict:
        return {
            "total_locks": len(self._locks),
            "active_locks": sum(1 for lk in self._locks.values() if lk.locked),
            "locks": {
                name: {
                    "locked": lk.locked,
                    "holder": lk.holder,
                    "acquire_count": lk.acquire_count,
                }
                for name, lk in self._locks.items()
                if lk.locked
            },
        }

    async def cleanup_stale(self, max_age: float = 300.0) -> int:
        """Release locks held longer than max_age seconds."""
        now = time.time()
        released = 0
        for name, lock in list(self._locks.items()):
            if lock.locked and (now - lock.acquired_at) > max_age:
                logger.warning(f"[LockManager] Releasing stale lock: {name} (holder={lock.holder})")
                try:
                    lock.release()
                    released += 1
                except RuntimeError:
                    logger.warning(
                        f"[LockManager] Cannot release lock {name} — not owned by current task"
                    )
        # Clean up unused lock entries to prevent memory growth
        async with self._meta_lock:
            stale_keys = [k for k, v in self._locks.items() if not v.locked and v.acquire_count > 0]
            for k in stale_keys:
                del self._locks[k]
        return released


class _LockContext:
    def __init__(self, manager: LockManager, resource: str, holder: str, timeout: float | None):
        self._manager = manager
        self._resource = resource
        self._holder = holder
        self._timeout = timeout
        self._lock: ResourceLock | None = None
        self._acquired = False

    async def __aenter__(self):
        self._lock = await self._manager._get_or_create(self._resource)
        if self._timeout:
            await asyncio.wait_for(self._lock.acquire(self._holder), timeout=self._timeout)
        else:
            await self._lock.acquire(self._holder)
        self._acquired = True
        return self._lock

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._lock and self._acquired:
            self._lock.release()
        return False
