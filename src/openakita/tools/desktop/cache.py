"""
Windows desktop automation - Element cache

Cache UI element information to avoid repeated parsing
"""

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from .types import UIElement

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """Cache entry"""

    key: str
    value: Any
    created_at: float = field(default_factory=time.time)
    accessed_at: float = field(default_factory=time.time)
    access_count: int = 0
    ttl: float = 60.0  # Default 60-second expiration

    @property
    def is_expired(self) -> bool:
        """Whether the entry has expired"""
        return time.time() - self.created_at > self.ttl

    def touch(self) -> None:
        """Update access time and count"""
        self.accessed_at = time.time()
        self.access_count += 1


class ElementCache:
    """
    UI element cache

    Features:
    - LRU eviction policy
    - Automatic expiration cleanup
    - Window-level cache partitioning
    """

    def __init__(
        self,
        max_size: int = 1000,
        default_ttl: float = 60.0,
    ):
        """
        Args:
            max_size: Maximum number of cache entries
            default_ttl: Default expiration time (seconds)
        """
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._window_cache: dict[str, dict[str, CacheEntry]] = {}

    def _make_key(self, *parts: Any) -> str:
        """Generate cache key"""
        return ":".join(str(p) for p in parts)

    def get(self, key: str) -> Any | None:
        """
        Get a cached value

        Args:
            key: Cache key

        Returns:
            Cached value, or None if not present or expired
        """
        entry = self._cache.get(key)
        if entry is None:
            return None

        if entry.is_expired:
            del self._cache[key]
            return None

        # Update access record and move to end (LRU)
        entry.touch()
        self._cache.move_to_end(key)

        return entry.value

    def set(
        self,
        key: str,
        value: Any,
        ttl: float | None = None,
    ) -> None:
        """
        Set a cache entry

        Args:
            key: Cache key
            value: Cache value
            ttl: Expiration time (seconds)
        """
        # Check capacity
        if len(self._cache) >= self._max_size:
            self._evict()

        entry = CacheEntry(
            key=key,
            value=value,
            ttl=ttl or self._default_ttl,
        )
        self._cache[key] = entry
        self._cache.move_to_end(key)

    def delete(self, key: str) -> bool:
        """
        Delete a cache entry

        Args:
            key: Cache key

        Returns:
            Whether the deletion succeeded
        """
        if key in self._cache:
            del self._cache[key]
            return True
        return False

    def clear(self) -> None:
        """Clear all cache entries"""
        self._cache.clear()
        self._window_cache.clear()

    def _evict(self) -> None:
        """Evict the oldest entry"""
        # First clean up expired entries
        expired_keys = [k for k, v in self._cache.items() if v.is_expired]
        for key in expired_keys:
            del self._cache[key]

        # If still full, remove the oldest
        while len(self._cache) >= self._max_size:
            self._cache.popitem(last=False)

    # ==================== Element cache convenience methods ====================

    def cache_element(
        self,
        element: UIElement,
        window_handle: int | None = None,
        ttl: float | None = None,
    ) -> str:
        """
        Cache a UI element

        Args:
            element: UI element
            window_handle: Owning window handle
            ttl: Expiration time

        Returns:
            Cache key
        """
        # Generate a unique key
        key_parts = ["element"]
        if window_handle:
            key_parts.append(str(window_handle))
        if element.automation_id:
            key_parts.append(element.automation_id)
        elif element.name:
            key_parts.append(element.name)
        else:
            key_parts.append(f"{element.control_type}_{id(element)}")

        key = self._make_key(*key_parts)
        self.set(key, element, ttl)

        return key

    def get_element(self, key: str) -> UIElement | None:
        """Get a cached element"""
        value = self.get(key)
        if isinstance(value, UIElement):
            return value
        return None

    def cache_window_elements(
        self,
        window_handle: int,
        elements: list[UIElement],
        ttl: float | None = None,
    ) -> None:
        """
        Cache all elements of a window

        Args:
            window_handle: Window handle
            elements: Element list
            ttl: Expiration time
        """
        key = self._make_key("window_elements", window_handle)
        self.set(key, elements, ttl)

    def get_window_elements(
        self,
        window_handle: int,
    ) -> list[UIElement] | None:
        """Get cached window elements"""
        key = self._make_key("window_elements", window_handle)
        value = self.get(key)
        if isinstance(value, list):
            return value
        return None

    def invalidate_window(self, window_handle: int) -> None:
        """
        Invalidate cache entries related to a window

        Args:
            window_handle: Window handle
        """
        prefix = f"element:{window_handle}:"
        window_key = self._make_key("window_elements", window_handle)

        # Delete window element cache
        self.delete(window_key)

        # Delete all element caches under this window
        keys_to_delete = [k for k in self._cache if k.startswith(prefix)]
        for key in keys_to_delete:
            del self._cache[key]

    def cache_vision_result(
        self,
        query: str,
        screenshot_hash: str,
        result: Any,
        ttl: float | None = None,
    ) -> None:
        """
        Cache a vision recognition result

        Args:
            query: Query description
            screenshot_hash: Screenshot hash
            result: Recognition result
            ttl: Expiration time
        """
        key = self._make_key("vision", screenshot_hash, query)
        self.set(key, result, ttl or 30.0)  # Vision results default to 30-second expiration

    def get_vision_result(
        self,
        query: str,
        screenshot_hash: str,
    ) -> Any | None:
        """Get a cached vision recognition result"""
        key = self._make_key("vision", screenshot_hash, query)
        return self.get(key)

    # ==================== Statistics ====================

    def stats(self) -> dict[str, Any]:
        """Get cache statistics"""
        total = len(self._cache)
        expired = sum(1 for e in self._cache.values() if e.is_expired)

        return {
            "total_entries": total,
            "expired_entries": expired,
            "active_entries": total - expired,
            "max_size": self._max_size,
            "default_ttl": self._default_ttl,
        }


# Global cache instance
_cache: ElementCache | None = None


def get_cache() -> ElementCache:
    """Get the global cache instance"""
    global _cache
    if _cache is None:
        _cache = ElementCache()
    return _cache


def clear_cache() -> None:
    """Clear the global cache"""
    global _cache
    if _cache is not None:
        _cache.clear()
