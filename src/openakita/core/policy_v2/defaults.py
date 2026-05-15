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


def default_protected_paths() -> list[str]:
    """Platform-specific default zone=PROTECTED paths.

    v1 ``_default_protected_paths`` 完全等价（C8b-1 audit 验证）。
    """
    paths: list[str] = []
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


__all__ = [
    "DEFAULT_BLOCKED_COMMANDS",
    "default_blocked_commands",
    "default_controlled_paths",
    "default_forbidden_paths",
    "default_protected_paths",
]
