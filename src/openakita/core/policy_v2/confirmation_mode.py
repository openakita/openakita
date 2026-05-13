"""Confirmation mode v2↔v1 label adapter (C8b-4)。

替代 v1 ``PolicyEngine._frontend_mode`` 字段——后者是个手工维护的 string
shim，每次 YAML reload 都要在 ``policy.py`` 三个地方同步赋值，且通过
``getattr(pe, "_frontend_mode", "yolo")`` 在 ``permission-mode`` GET 端点暴
露给前端。

C8b-4 用本模块函数取代该字段：
- 读：``read_permission_mode_label()`` → 直接拉 v2 ``get_config_v2().confirmation.mode``
  并用 ``LEGACY_MODE_BACK_ALIASES`` 映射回 v1 product label
- 写：v2 唯一写入路径是"先 YAML 持久化 → ``reset_policy_v2_layer()`` 触发 lazy
  re-load"。``permission-mode`` POST 端点不再需要二次写 v1 字段。

设计动机
========

1. **single source of truth**：v2 ``PolicyConfigV2.confirmation.mode`` 是真正的
   决策状态；v1 ``_frontend_mode`` 只是 UI 用的便利字段。两边同步靠人工保证
   不可靠（C8a 里因为漏写 ``_frontend_mode`` 已经踩过一次 P2 bug）。

2. **product label vs. internal enum 分离**：v2 enum 用 ``trust/default/strict``
   （内部语义），UI 仍然用 v1 ``yolo/smart/cautious`` (产品 label，前端
   翻译过的）。本模块是两者间的唯一翻译点；端点不需要再各自维护映射。

3. **fail-soft 默认**：v2 config 拉取失败时回到 ``"yolo"``（前端兼容），
   避免 startup 早期或测试场景下 endpoint 直接 500。
"""

from __future__ import annotations

import logging
from typing import Literal

from .enums import LEGACY_MODE_ALIASES, ConfirmationMode

logger = logging.getLogger(__name__)


# v2 enum value → v1 product label 的反向映射（``LEGACY_MODE_ALIASES`` 是 v1→v2）。
# 显式 dict 而不是 dict 反转：v2 ConfirmationMode 比 v1 多两档（accept_edits / dont_ask），
# 它们没有 v1 product 对应；这里映射到最接近的 v1 标签以保证 UI 不崩。
_V2_TO_V1_LABEL: dict[ConfirmationMode, Literal["cautious", "smart", "yolo"]] = {
    ConfirmationMode.TRUST: "yolo",        # v1 alias
    ConfirmationMode.DEFAULT: "smart",     # v1 alias
    ConfirmationMode.STRICT: "cautious",   # v1 alias
    ConfirmationMode.ACCEPT_EDITS: "smart",  # v2-only：归并到 smart（最接近的 UX）
    ConfirmationMode.DONT_ASK: "yolo",       # v2-only：归并到 trust（最宽松）
}


def read_permission_mode_label() -> Literal["cautious", "smart", "yolo"]:
    """读取当前 v2 confirmation.mode 并返回 v1 product label。

    与 v1 ``getattr(pe, "_frontend_mode", "yolo")`` 等价但完全脱离 v1
    字段。``permission-mode`` GET endpoint 直接调本函数。
    """
    try:
        from .global_engine import get_config_v2

        cfg_mode = get_config_v2().confirmation.mode
        return _V2_TO_V1_LABEL.get(cfg_mode, "yolo")
    except Exception as exc:
        logger.debug(
            "[PolicyV2 ConfirmationMode] failed to read v2 mode, falling back: %s", exc
        )
        return "yolo"


def coerce_v1_label_to_v2_mode(label: str) -> ConfirmationMode:
    """v1 product label (``cautious``/``smart``/``yolo``/``trust``) → v2 enum。

    与 ``policy_v2.context._coerce_mode`` 同语义但不依赖 ContextVar；适合
    config endpoint / migration / 任何把 UI 字符串落到 schema field 的场景。
    """
    normalized = (label or "yolo").strip().lower()
    canonical = LEGACY_MODE_ALIASES.get(normalized, normalized)
    try:
        return ConfirmationMode(canonical)
    except ValueError:
        return ConfirmationMode.DEFAULT


__all__ = [
    "coerce_v1_label_to_v2_mode",
    "read_permission_mode_label",
]
