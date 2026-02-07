"""
IM 上下文（协程隔离）

历史上项目使用 `Agent._current_im_session/_current_im_gateway` 作为全局类变量，
在并发（多会话 IM / 多定时任务并行）时会导致串台。

这里改用 contextvars，实现每个协程/任务独立的上下文。
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

# Session / MessageGateway 类型在不同模块里定义，这里用 Any 避免循环依赖
current_im_session: ContextVar[Any | None] = ContextVar("current_im_session", default=None)
current_im_gateway: ContextVar[Any | None] = ContextVar("current_im_gateway", default=None)


def get_im_session() -> Any | None:
    return current_im_session.get()


def get_im_gateway() -> Any | None:
    return current_im_gateway.get()


def set_im_context(*, session: Any | None, gateway: Any | None) -> tuple[Any, Any]:
    """
    设置 IM 上下文，返回 token 用于 reset。
    """
    tok1 = current_im_session.set(session)
    tok2 = current_im_gateway.set(gateway)
    return tok1, tok2


def reset_im_context(tokens: tuple[Any, Any]) -> None:
    tok1, tok2 = tokens
    current_im_session.reset(tok1)
    current_im_gateway.reset(tok2)

