"""Platform-specific default zone paths and blocked commands (C8b-2)。

替代 v1 ``core/policy.py`` 中的：
- ``_default_protected_paths()`` 函数
- ``_default_forbidden_paths()`` 函数
- ``_default_controlled_paths()`` 函数
- ``_DEFAULT_BLOCKED_COMMANDS`` 列表常量

这些是**平台相关**的兜底默认值，UI 在用户未在 ``POLICIES.yaml`` 中显式配置
zone 时返回这些；shell_risk classifier 在用户未自定义 ``blocked_commands``
时也用这些做基线 CRITICAL 列表。

设计要点
========

1. **纯函数**：每次调用重新计算 ``platform.system()``——v1 同行为，避免在
   import 时缓存导致跨平台测试桶（pytest-xdist 多进程切换 OS mock）出现
   stale 数据。每次调用 ~10 µs，调用频率低，可忽略。

2. **list 返回 fresh 实例**：caller 可能 ``.append`` / ``.extend``，每次
   返回新 list 防止意外共享。``_DEFAULT_BLOCKED_COMMANDS`` 用 tuple 暴露
   immutable view，``default_blocked_commands()`` 返回 list 拷贝。

3. **Backwards-compatible re-export**：v1 ``core/policy.py`` 中保留同名
   ``_default_*_paths()`` 函数与 ``_DEFAULT_BLOCKED_COMMANDS`` 常量，但都
   delegate 到本模块。这样 C8b-2 commit 后所有 v1 import 仍能工作；
   C8b-5 删 policy.py 时再统一去除。

4. **Naming**：v2 公开 API 用 ``default_*`` 不带下划线前缀（v1 用 ``_default_*``
   是因为 PolicyEngine 内部辅助函数）。下游 caller 应该用新名字；旧名字仍
   可访问以减少 import 风暴。
"""

from __future__ import annotations

import platform
from copy import deepcopy
from typing import Any

from .enums import ConfirmationMode


def default_protected_paths() -> list[str]:
    """Platform-specific default zone=PROTECTED paths.

    v1 ``_default_protected_paths`` 完全等价（C8b-1 audit 验证）。
    """
    paths: list[str] = [
        "${CWD}/identity/**",
        "${CWD}/data/**",
    ]
    if platform.system() == "Windows":
        paths.extend(
            [
                "C:/Program Files/**",
                "C:/Program Files (x86)/**",
                "C:/Windows/**",
                "C:/ProgramData/**",
            ]
        )
    else:
        paths.extend(
            [
                "/usr/**",
                "/bin/**",
                "/sbin/**",
                "/lib/**",
                "/lib64/**",
                "/boot/**",
                "/etc/**",
                "/dev/**",
                "/proc/**",
                "/sys/**",
            ]
        )
        if platform.system() == "Darwin":
            paths.extend(["/System/**", "/Library/**"])
    return paths


def default_forbidden_paths() -> list[str]:
    """Platform-specific default zone=FORBIDDEN paths."""
    paths: list[str] = ["~/.ssh/**", "~/.gnupg/**", "~/.aws/**", "~/.config/gcloud/**"]
    if platform.system() == "Windows":
        paths.extend(
            [
                "C:/Windows/System32/config/**",
                "~/.aws/credentials",
                "~/AppData/Roaming/gcloud/**",
            ]
        )
    else:
        paths.extend(["/etc/shadow", "/etc/gshadow"])
    return paths


def default_controlled_paths() -> list[str]:
    """Platform-specific default zone=CONTROLLED paths.

    P0-1：用户常用工作区目录（桌面/文档/下载）默认归 CONTROLLED，而非默认
    WORKSPACE。这样 smart/cautious 模式下 LLM 主动写入这些目录会触发
    risk_confirm；yolo（完全信任）模式下 baseline_protection 继续放行，不
    打断用户。
    """
    paths: list[str] = []
    if platform.system() == "Windows":
        paths.extend(
            [
                "~/Desktop/**",
                "~/Documents/**",
                "~/Downloads/**",
                "~/Pictures/**",
                "~/Videos/**",
                "~/Music/**",
                "~/桌面/**",
                "~/文档/**",
                "~/下载/**",
                "~/图片/**",
            ]
        )
    else:
        paths.extend(
            [
                "~/Desktop/**",
                "~/Documents/**",
                "~/Downloads/**",
                "~/Pictures/**",
                "~/Music/**",
            ]
        )
        if platform.system() == "Darwin":
            paths.extend(["~/Movies/**", "~/Public/**"])
    return paths


# DEFAULT_BLOCKED_COMMANDS：classifier baseline 与 UI default 是同一个语义列表
# （UI 展示"系统默认 blocked tokens"= classifier 用作 BLOCKED 等级的 token set）。
# 单一 source of truth：``shell_risk.DEFAULT_BLOCKED_COMMANDS``。
# 本模块重新导出仅为给 UI / config callsite 一个语义化的 import 路径
# （``policy_v2.defaults`` = "UI 看到的兜底默认值集合"）。
from .shell_risk import DEFAULT_BLOCKED_COMMANDS as _SHELL_RISK_DEFAULT_BLOCKED_COMMANDS

DEFAULT_BLOCKED_COMMANDS: tuple[str, ...] = tuple(_SHELL_RISK_DEFAULT_BLOCKED_COMMANDS)


def default_blocked_commands() -> list[str]:
    """Return a fresh list copy of ``DEFAULT_BLOCKED_COMMANDS``.

    UI / config callsite 把这个值合并到用户自定义列表，所以每次返回新 list
    避免意外共享 / mutate（v1 ``_DEFAULT_BLOCKED_COMMANDS`` 是直接暴露
    list；v2 用 tuple immutable 暴露 + 函数返回 list 更安全）。
    """
    return list(DEFAULT_BLOCKED_COMMANDS)


# ---------------------------------------------------------------------------
# Security profile bundles (single source of truth for "trust / protect /
# strict / off" presets)
# ---------------------------------------------------------------------------
#
# 历史上"出厂默认 = trust"的语义并行散落在三个位置：
#   1. ``policy_v2/schema.py`` 字段默认（``SecurityProfileConfig.current``、
#      ``ConfirmationConfig.mode``）
#   2. ``api/routes/config.py::_apply_security_profile_defaults`` 用户点
#      "信任 / 保护 / 严格 / 关闭"卡片时套用的 bundle
#   3. ``apps/setup-center/src/views/SecurityView.tsx`` loading 占位
#
# 三处任意一处偏移，都会出现"UI 显示 trust、引擎按 protect 运行"这类隐式
# 不一致（v1.27.12 → v1.27.13 默认值切换时就栽过这个坑）。本节把
# bundle 收成单一真源：schema 默认字段通过 ``factory_default_*`` helper
# 取出，``_apply_security_profile_defaults`` 直接 deep-copy ``PROFILE_BUNDLES``。
#
# bundle 的字段集与 ``POLICIES.yaml`` 的 raw dict 结构对齐——``security.enabled``
# / ``security.confirmation.mode`` / ``security.sandbox.enabled`` 等——所以
# 路由层套用 bundle 时只需 ``deep_merge`` 不需要 schema mapping。
#
# ``PolicyConfigV2`` 的字段级 schema 默认与 bundle 在以下字段**有意保留差异**：
# - ``sandbox.enabled``：schema 默认 ``True`` 作 belt-and-suspenders；
#   bundle ``trust`` 设 ``False`` 是 UI 套餐承诺"信任方案下不进沙箱"。
# 也就是 fresh install（走 schema 默认）= TRUST 模式 + sandbox 仍开；用户
# 主动点"信任方案"按钮（走 bundle）= TRUST 模式 + sandbox 关。这条非对称
# 由 ``PolicyConfigV2`` docstring 显式记录，本模块不强行抹平。

FACTORY_DEFAULT_PROFILE: str = "trust"
"""出厂默认 profile 名。fresh install / lenient fallback / 未知输入 兜底
都应落到这个值。改这里前请同步 ``docs/release-notes/``。"""

PROFILE_BUNDLES: dict[str, dict[str, Any]] = {
    "trust": {
        "enabled": True,
        "confirmation": {"mode": "trust"},
        "sandbox": {"enabled": False},
        "shell_risk": {"enabled": True},
        "death_switch": {"enabled": True},
        "checkpoint": {"enabled": True},
    },
    "protect": {
        "enabled": True,
        "confirmation": {"mode": "default"},
        "sandbox": {"enabled": True},
        "shell_risk": {"enabled": True},
        "death_switch": {"enabled": True},
        "checkpoint": {"enabled": True},
    },
    "strict": {
        "enabled": True,
        "confirmation": {"mode": "strict"},
        "sandbox": {"enabled": True},
        "shell_risk": {"enabled": True},
        "death_switch": {"enabled": True},
        "checkpoint": {"enabled": True},
    },
    "off": {
        # off 同时把 security.enabled 关掉，使二者保持单一语义：
        # "整套策略停摆"。engine.preflight 任一为关都会短路 ALLOW，
        # 但只有这里二者同时被写下，未来导出/迁移/审计才不会出现
        # "enabled=True 但 profile=off" 这种荒谬组合。
        "enabled": False,
        "confirmation": {"mode": "trust"},
        "sandbox": {"enabled": False},
        "shell_risk": {"enabled": False},
        "death_switch": {"enabled": False},
        "checkpoint": {"enabled": False},
    },
}
"""profile name → raw YAML-shape bundle。``custom`` 不在表中，因为它表示
"用户自己拼"，没有 baked bundle。"""


def profile_bundle(profile: str) -> dict[str, Any]:
    """返回 ``profile`` 对应 bundle 的 fresh deep-copy（caller 可放心 mutate）。

    Raises:
        KeyError: ``profile`` 不是 baked 名称之一。``custom`` 也会抛——它没有
            预制 bundle，调用方应该单独处理。
    """
    if profile not in PROFILE_BUNDLES:
        raise KeyError(
            f"unknown profile {profile!r}; "
            f"valid: {sorted(PROFILE_BUNDLES.keys())} (note: 'custom' has no bundle)"
        )
    return deepcopy(PROFILE_BUNDLES[profile])


def factory_default_confirmation_mode() -> ConfirmationMode:
    """schema ``ConfirmationConfig.mode`` 的工厂默认。

    单一真源从 ``PROFILE_BUNDLES[FACTORY_DEFAULT_PROFILE]`` 取，保证
    "schema 默认引擎模式" = "出厂 profile 套餐写下的引擎模式"。
    """
    raw = PROFILE_BUNDLES[FACTORY_DEFAULT_PROFILE]["confirmation"]["mode"]
    return ConfirmationMode(raw)


def factory_default_profile_current() -> str:
    """schema ``SecurityProfileConfig.current`` 的工厂默认。"""
    return FACTORY_DEFAULT_PROFILE


__all__ = [
    "DEFAULT_BLOCKED_COMMANDS",
    "FACTORY_DEFAULT_PROFILE",
    "PROFILE_BUNDLES",
    "default_blocked_commands",
    "default_controlled_paths",
    "default_forbidden_paths",
    "default_protected_paths",
    "factory_default_confirmation_mode",
    "factory_default_profile_current",
    "profile_bundle",
]
