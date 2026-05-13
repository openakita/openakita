"""POLICIES.yaml v2 loader（migration + Pydantic 校验 + deep-merge defaults）。

调用流程：

1. ``load_policies_yaml(path)`` —— 读 YAML → bytes → safe_load
2. ``detect_schema_version()`` 判 v1/v2/mixed/empty
3. v1/mixed → ``migrate_v1_to_v2()``；v2 直接通过
4. ``deep_merge_defaults()``：与 ``PolicyConfigV2()`` 默认值 deep-merge（用户只配
   局部，其他用默认）
5. ``PolicyConfigV2.model_validate()`` 严格校验（``extra='forbid'`` 让 typo 报错）
6. ``cfg.expand_placeholders()`` 展开 ``${CWD}`` / ``~``

异常策略：
- 文件不存在 → 返回默认 ``PolicyConfigV2()`` + WARN log
- YAML 解析失败 → 返回默认 + ERROR log（不阻断启动）
- Pydantic 校验失败 → 抛 ``PolicyConfigError``（**阻断启动**，配置错误必须修）
- I/O 异常 → 返回默认 + ERROR log

C4 阶段不做 hot-reload；C18 接入 watchdog/inotify 触发 ``classifier.invalidate()`` +
``engine`` 配置 swap。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .migration import MigrationReport, migrate_v1_to_v2
from .schema import PolicyConfigV2

logger = logging.getLogger(__name__)


class PolicyConfigError(Exception):
    """POLICIES.yaml 校验失败 —— 应阻断启动。"""

    def __init__(self, message: str, validation_error: ValidationError | None = None) -> None:
        super().__init__(message)
        self.validation_error = validation_error


def load_policies_yaml(
    path: str | Path | None = None,
    *,
    cwd: Path | None = None,
    strict: bool = True,
) -> tuple[PolicyConfigV2, MigrationReport]:
    """加载 POLICIES.yaml 并返回校验后的 v2 config + 迁移报告。

    Args:
        path: YAML 路径。None 时返回纯默认配置（用于测试 / CLI minimal 模式）。
        cwd: workspace 占位符 ``${CWD}`` 展开依据。None 用 ``Path.cwd()``。
        strict: True 时 ValidationError 抛 PolicyConfigError；False 时降级为
            WARN + 默认配置（CLI / 紧急启动场景；生产应保持 strict）。

    Returns:
        (PolicyConfigV2, MigrationReport)
    """
    if path is None:
        cfg = PolicyConfigV2().expand_placeholders(cwd=cwd)
        return cfg, MigrationReport()

    p = Path(path)
    raw = _read_yaml(p)

    return _build_config(raw, cwd=cwd, strict=strict, source=str(p))


def load_policies_from_dict(
    raw: dict[str, Any] | None,
    *,
    cwd: Path | None = None,
    strict: bool = True,
) -> tuple[PolicyConfigV2, MigrationReport]:
    """从内存 dict 直接加载（测试 / 程序化构造）。

    与 ``load_policies_yaml`` 共享后续 pipeline（migration + merge + validate）。
    """
    return _build_config(raw, cwd=cwd, strict=strict, source="<memory>")


def _build_config(
    raw: dict[str, Any] | None,
    *,
    cwd: Path | None,
    strict: bool,
    source: str,
) -> tuple[PolicyConfigV2, MigrationReport]:
    v2_dict, report = migrate_v1_to_v2(raw)

    if report.fields_migrated:
        logger.info(
            "[PolicyV2] migrated %d v1 fields from %s: %s",
            len(report.fields_migrated),
            source,
            ", ".join(report.fields_migrated),
        )
    if report.fields_dropped:
        logger.warning(
            "[PolicyV2] dropped %d obsolete v1 fields from %s: %s",
            len(report.fields_dropped),
            source,
            ", ".join(report.fields_dropped),
        )
    if report.conflicts:
        logger.warning(
            "[PolicyV2] %d v1↔v2 conflicts in %s (kept v2 values): %s",
            len(report.conflicts),
            source,
            ", ".join(report.conflicts),
        )

    merged = _deep_merge_defaults(v2_dict.get("security") or {})

    try:
        cfg = PolicyConfigV2.model_validate(merged)
    except ValidationError as exc:
        msg = f"POLICIES.yaml schema validation failed at {source}:\n{exc}"
        if strict:
            raise PolicyConfigError(msg, validation_error=exc) from exc
        logger.error("[PolicyV2] %s — falling back to defaults", msg)
        cfg = PolicyConfigV2()

    cfg = cfg.expand_placeholders(cwd=cwd)
    return cfg, report


def _read_yaml(path: Path) -> dict[str, Any] | None:
    """读 YAML 文件。文件不存在/解析失败 → None + WARN/ERROR log（不抛）。"""
    if not path.exists():
        logger.warning(
            "[PolicyV2] config file not found: %s — using defaults",
            path,
        )
        return None
    try:
        import yaml  # local import: yaml 是 optional dependency
    except ImportError:
        logger.error("[PolicyV2] PyYAML not installed; cannot load %s", path)
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        logger.error("[PolicyV2] YAML parse error in %s: %s", path, exc)
        return None
    except OSError as exc:
        logger.error("[PolicyV2] I/O error reading %s: %s", path, exc)
        return None

    if data is None:
        return None
    if not isinstance(data, dict):
        logger.error(
            "[PolicyV2] %s top-level must be a mapping, got %s",
            path,
            type(data).__name__,
        )
        return None
    return data


def _deep_merge_defaults(user: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge user config over PolicyConfigV2() defaults.

    保证用户只配局部时（例如只设 confirmation.mode）其他字段使用默认。
    Pydantic 的 ``model_validate`` 不自动 fill nested defaults——必须手动 deep-merge。

    list 字段**整体替换**（不 merge），符合用户直觉（用户配 ``blocked_commands``
    是想精准覆盖，不是 union）。
    """
    defaults = PolicyConfigV2().model_dump()
    return _merge_dicts(defaults, user)


def _merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, val in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(val, dict):
            out[key] = _merge_dicts(out[key], val)
        else:
            out[key] = val
    return out


__all__ = [
    "PolicyConfigError",
    "load_policies_from_dict",
    "load_policies_yaml",
]
