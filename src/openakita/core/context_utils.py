"""
上下文窗口工具函数

从 agent.py / context_manager.py 提取的公共逻辑:
- estimate_tokens: 中英文感知的 token 估算
- get_max_context_tokens: 根据端点配置计算可用上下文 token 数
- get_raw_context_window: 获取端点原始 context_window 值
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MAX_CONTEXT_TOKENS = 160000


def estimate_tokens(text: str) -> int:
    """估算文本的 token 数量（中英文感知）。

    中文约 1.5 字符/token，英文约 4 字符/token。
    """
    if not text:
        return 0
    chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    total_chars = len(text)
    english_chars = total_chars - chinese_chars
    chinese_tokens = chinese_chars / 1.5
    english_tokens = english_chars / 4
    return max(int(chinese_tokens + english_tokens), 1)


def get_raw_context_window(brain: Any) -> int:
    """获取当前端点配置的原始 context_window 值。

    Args:
        brain: Brain 实例

    Returns:
        context_window 值，获取失败返回 0
    """
    try:
        info = brain.get_current_model_info()
        ep_name = info.get("name", "")
        for ep in brain._llm_client.endpoints:
            if ep.name == ep_name:
                return getattr(ep, "context_window", 0) or 0
    except Exception:
        pass
    return 0


def get_max_context_tokens(
    brain: Any,
    conversation_id: str | None = None,
) -> int:
    """根据端点配置计算可用上下文 token 数。

    优先级:
    1. 端点配置的 context_window（本地端点缺失时已在 EndpointConfig 归一化为小窗口）
    2. 减去 max_tokens 输出预留和 5% buffer
    3. 完全无法获取时 fallback 到 DEFAULT_MAX_CONTEXT_TOKENS (160K)
    """
    from ..config import settings
    from ..llm.types import DEFAULT_CONTEXT_WINDOW

    try:
        info = brain.get_current_model_info(conversation_id=conversation_id)
        ep_name = info.get("name", "")
        for ep in brain._llm_client.endpoints:
            if ep.name == ep_name:
                ctx = getattr(ep, "context_window", 0) or 0
                if ctx <= 0:
                    ctx = DEFAULT_CONTEXT_WINDOW
                if settings.context_max_window > 0:
                    ctx = min(ctx, settings.context_max_window)
                output_reserve = ep.max_tokens or 4096
                output_reserve = min(output_reserve, ctx // 3)
                result = int((ctx - output_reserve) * 0.95)
                if result < 1024:
                    return max(int(ctx * 0.5), 1024)
                return result
        return DEFAULT_MAX_CONTEXT_TOKENS
    except Exception:
        return DEFAULT_MAX_CONTEXT_TOKENS

