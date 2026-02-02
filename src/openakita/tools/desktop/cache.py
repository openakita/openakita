"""
Windows 桌面自动化 - 元素缓存

缓存 UI 元素信息，避免重复解析
"""

import time
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field
from collections import OrderedDict
import logging

from .types import UIElement, WindowInfo, ElementLocation

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """缓存条目"""
    key: str
    value: Any
    created_at: float = field(default_factory=time.time)
    accessed_at: float = field(default_factory=time.time)
    access_count: int = 0
    ttl: float = 60.0  # 默认 60 秒过期
    
    @property
    def is_expired(self) -> bool:
        """是否已过期"""
        return time.time() - self.created_at > self.ttl
    
    def touch(self) -> None:
        """更新访问时间和次数"""
        self.accessed_at = time.time()
        self.access_count += 1


class ElementCache:
    """
    UI 元素缓存
    
    特性：
    - LRU 淘汰策略
    - 过期自动清理
    - 窗口级别的缓存分区
    """
    
    def __init__(
        self,
        max_size: int = 1000,
        default_ttl: float = 60.0,
    ):
        """
        Args:
            max_size: 最大缓存条目数
            default_ttl: 默认过期时间（秒）
        """
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._window_cache: Dict[str, Dict[str, CacheEntry]] = {}
    
    def _make_key(self, *parts: Any) -> str:
        """生成缓存键"""
        return ":".join(str(p) for p in parts)
    
    def get(self, key: str) -> Optional[Any]:
        """
        获取缓存
        
        Args:
            key: 缓存键
            
        Returns:
            缓存值，不存在或过期返回 None
        """
        entry = self._cache.get(key)
        if entry is None:
            return None
        
        if entry.is_expired:
            del self._cache[key]
            return None
        
        # 更新访问记录并移到末尾（LRU）
        entry.touch()
        self._cache.move_to_end(key)
        
        return entry.value
    
    def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[float] = None,
    ) -> None:
        """
        设置缓存
        
        Args:
            key: 缓存键
            value: 缓存值
            ttl: 过期时间（秒）
        """
        # 检查容量
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
        删除缓存
        
        Args:
            key: 缓存键
            
        Returns:
            是否删除成功
        """
        if key in self._cache:
            del self._cache[key]
            return True
        return False
    
    def clear(self) -> None:
        """清空所有缓存"""
        self._cache.clear()
        self._window_cache.clear()
    
    def _evict(self) -> None:
        """淘汰最旧的条目"""
        # 先清理过期条目
        expired_keys = [
            k for k, v in self._cache.items() if v.is_expired
        ]
        for key in expired_keys:
            del self._cache[key]
        
        # 如果还是满了，删除最旧的
        while len(self._cache) >= self._max_size:
            self._cache.popitem(last=False)
    
    # ==================== 元素缓存便捷方法 ====================
    
    def cache_element(
        self,
        element: UIElement,
        window_handle: Optional[int] = None,
        ttl: Optional[float] = None,
    ) -> str:
        """
        缓存 UI 元素
        
        Args:
            element: UI 元素
            window_handle: 所属窗口句柄
            ttl: 过期时间
            
        Returns:
            缓存键
        """
        # 生成唯一键
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
    
    def get_element(self, key: str) -> Optional[UIElement]:
        """获取缓存的元素"""
        value = self.get(key)
        if isinstance(value, UIElement):
            return value
        return None
    
    def cache_window_elements(
        self,
        window_handle: int,
        elements: List[UIElement],
        ttl: Optional[float] = None,
    ) -> None:
        """
        缓存窗口的所有元素
        
        Args:
            window_handle: 窗口句柄
            elements: 元素列表
            ttl: 过期时间
        """
        key = self._make_key("window_elements", window_handle)
        self.set(key, elements, ttl)
    
    def get_window_elements(
        self,
        window_handle: int,
    ) -> Optional[List[UIElement]]:
        """获取缓存的窗口元素"""
        key = self._make_key("window_elements", window_handle)
        value = self.get(key)
        if isinstance(value, list):
            return value
        return None
    
    def invalidate_window(self, window_handle: int) -> None:
        """
        使窗口相关的缓存失效
        
        Args:
            window_handle: 窗口句柄
        """
        prefix = f"element:{window_handle}:"
        window_key = self._make_key("window_elements", window_handle)
        
        # 删除窗口元素缓存
        self.delete(window_key)
        
        # 删除该窗口下的所有元素缓存
        keys_to_delete = [k for k in self._cache.keys() if k.startswith(prefix)]
        for key in keys_to_delete:
            del self._cache[key]
    
    def cache_vision_result(
        self,
        query: str,
        screenshot_hash: str,
        result: Any,
        ttl: Optional[float] = None,
    ) -> None:
        """
        缓存视觉识别结果
        
        Args:
            query: 查询描述
            screenshot_hash: 截图哈希
            result: 识别结果
            ttl: 过期时间
        """
        key = self._make_key("vision", screenshot_hash, query)
        self.set(key, result, ttl or 30.0)  # 视觉结果默认 30 秒过期
    
    def get_vision_result(
        self,
        query: str,
        screenshot_hash: str,
    ) -> Optional[Any]:
        """获取缓存的视觉识别结果"""
        key = self._make_key("vision", screenshot_hash, query)
        return self.get(key)
    
    # ==================== 统计信息 ====================
    
    def stats(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        total = len(self._cache)
        expired = sum(1 for e in self._cache.values() if e.is_expired)
        
        return {
            "total_entries": total,
            "expired_entries": expired,
            "active_entries": total - expired,
            "max_size": self._max_size,
            "default_ttl": self._default_ttl,
        }


# 全局缓存实例
_cache: Optional[ElementCache] = None


def get_cache() -> ElementCache:
    """获取全局缓存实例"""
    global _cache
    if _cache is None:
        _cache = ElementCache()
    return _cache


def clear_cache() -> None:
    """清空全局缓存"""
    global _cache
    if _cache is not None:
        _cache.clear()
