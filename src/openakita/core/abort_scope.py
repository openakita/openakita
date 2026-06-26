"""AbortScope —— 取消信号的层级传播树（S3, plan: conversation concurrency v1.28）

替代 ``TaskState.cancel_event`` 单点 ``asyncio.Event`` 的设计。原方案存在两类问题：

1. **扇出靠零散读取**：``tool_executor.execute_tool``、``brain.messages_create_async``、
   ``reasoning_engine._run_inner`` 各自从 ``state.cancel_event`` 拿事件再 race；
   ``orchestrator.delegate`` 把父 cancel 传给 sub-agent 时是另写一套，
   tool 内 spawn 子协程时则完全没有信号传递。
2. **重置语义模糊**：reasoning_engine retry path 写 ``state.cancel_event =
   asyncio.Event()`` 直接换实例，旧 race waiter 永远不会触发，是历史 bug 温床。

AbortScope 形成 **父 → 子 → 孙** 的取消树：

- ``abort_root`` 挂在 ``TaskState``，代表整个 chat turn 的取消域
- 每个工具调用、每个 sub-agent 委派都 ``create_child`` 派生
- 父 ``abort()`` 自动级联到所有子（包括尚未创建的——通过 ``create_child`` 时检测）
- 子 ``abort()`` 不向上冒泡（局部 cancel：一个工具被 skip 不影响整个 turn）

向后兼容：``TaskState.cancel_event`` 改为 property，委托给 ``abort_root.event``——
所有既有读取 ``task.cancel_event.wait()`` / ``task.cancel_event.is_set()`` 零改动。
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# Per-task current AbortScope.  ``reason_stream`` / ``run()`` set this to
# ``state.abort_root`` at entry; downstream code (tool_executor, orchestrator,
# tool handlers) reads it to derive child scopes without explicit parameter
# threading.  ContextVar gives per-asyncio-task isolation — nested awaits
# inherit, but sibling tasks (concurrent tool dispatches across different
# conversations) each see their own value.
#
# Typed as ``Any`` at runtime to avoid forward-reference resolution on the
# ContextVar generic; readers should treat the value as ``AbortScope | None``.
current_abort_scope: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "current_abort_scope", default=None
)


@dataclass
class AbortScope:
    """单个取消域。父 abort 自动扇出到所有 children；子 abort 不冒泡。

    线程/事件循环安全：``event`` 是 ``asyncio.Event``，必须在持有事件循环的协程内
    构造和 set；构造时若无运行循环会延迟到首次访问（通过 ``_lazy_event`` 兜底）。
    """

    name: str
    event: asyncio.Event = field(default_factory=asyncio.Event)
    parent: AbortScope | None = None
    children: list[AbortScope] = field(default_factory=list)
    reason: str = ""
    # 防止 ``abort()`` 重入扇出 + 调试链路：记录是从哪个 scope 传播来的
    _aborted_by: str | None = None

    def is_aborted(self) -> bool:
        return self.event.is_set()

    def abort(self, reason: str = "", _from: str | None = None) -> None:
        """触发当前 scope 取消，并扇出到所有未取消的 children。

        Args:
            reason: 取消原因（首次设置生效；后续调用忽略以保留第一现场）
            _from: 内部参数，传播链中上一个 scope 的 name；外部调用勿传
        """
        if self.event.is_set():
            return
        self.reason = reason or self.reason
        self._aborted_by = _from
        try:
            self.event.set()
        except RuntimeError:
            # 事件循环关闭等边界情形：忽略，避免在 cleanup 路径炸链路
            logger.debug("[AbortScope] event.set() raised on %s; ignoring", self.name)

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "[AbortScope] %s aborted (reason=%r, from=%s, children=%d)",
                self.name,
                reason,
                _from or "self",
                len(self.children),
            )

        # 扇出（list() 拷贝防迭代时 mutate）
        for child in list(self.children):
            child.abort(reason, _from=self.name)

    def create_child(self, name: str) -> AbortScope:
        """派生子 scope。若父已 aborted，子立即 aborted（保证新派生的 tool/subagent
        不会在 cancel 后还跑一帧）。"""
        child = AbortScope(name=name, parent=self)
        self.children.append(child)
        if self.event.is_set():
            child.abort(self.reason, _from=self.name)
        return child

    def remove_child(self, child: AbortScope) -> None:
        """工具/子任务正常结束时调用，避免父 scope 持有大量已完成的子引用。"""
        try:
            self.children.remove(child)
        except ValueError:
            pass

    def reset_event(self) -> None:
        """**Deprecated** —— 仅为 ``reasoning_engine`` retry path 的历史行为保留。

        原代码在 LLM 重试时 ``state.cancel_event = asyncio.Event()`` 直接换实例，
        语义是"清除上一轮残留的取消信号、让本轮重新开始"。新设计里这等价于
        把当前 scope 的 event 换成一个新的（children 不受影响，因为 children
        持有的是各自的 event 引用）。

        新代码不应调用此方法——应该直接 ``create_child`` 派生新 scope。
        """
        if self.event.is_set():
            self.event = asyncio.Event()
            self._aborted_by = None
            self.reason = ""

    def depth(self) -> int:
        d = 0
        cur = self.parent
        while cur is not None:
            d += 1
            cur = cur.parent
        return d

    def __repr__(self) -> str:
        return (
            f"AbortScope(name={self.name!r}, aborted={self.is_aborted()}, "
            f"children={len(self.children)}, depth={self.depth()})"
        )


def root_scope(name: str = "root") -> AbortScope:
    """便捷构造一个根 scope，主要用于测试。生产代码应直接通过
    ``TaskState.abort_root`` 取得。"""
    return AbortScope(name=name)
