"""Workspace path helpers.

v2 大幅简化了 v1 的 zone 概念（详见 docs §7 迁移规则）：

- ``zones.workspace`` 保留 — 给 ApprovalClassifier "在不在 workspace" 判定（refine 跨盘升级）
- ``zones.protected`` / ``zones.forbidden`` → 合并进 ``safety_immune.paths``（启动时 union，C6 实施）
- ``zones.default_zone`` → 废弃，v2 不依赖 zone

本模块只暴露 workspace 判定 helper；其他"zone"语义不在 v2 中存在。
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path


def _coerce_workspace_roots(workspace_roots: Path | str | Iterable[Path | str]) -> tuple[Path, ...]:
    if isinstance(workspace_roots, (str, Path)):
        roots = (workspace_roots,)
    else:
        roots = tuple(workspace_roots)
    return tuple(Path(root) for root in roots if str(root))


def is_inside_workspace(
    raw_path: str,
    workspace_roots: Path | str | Iterable[Path | str],
) -> bool:
    """判断 raw_path 是否在任一 workspace root 内（resolved，处理符号链接）。

    跨平台说明：
    - Windows 路径大小写不敏感，比较用 lower() 兜底
    - resolve(strict=False) 不要求路径存在（write_file 写新文件场景）
    - 解析失败（权限/无效字符）→ 保守返回 False（视为外部，触发严格分类）

    符号链接：workspace root 或 target 是符号链接 → resolve 后比较 real path。
    若 workspace root 内的符号链接指向外部 → 判外（safety-by-default）。
    """
    try:
        target = Path(raw_path).expanduser().resolve(strict=False)
    except (OSError, ValueError, RuntimeError):
        return False

    for root in _coerce_workspace_roots(workspace_roots):
        try:
            ws = root.expanduser().resolve(strict=False)
        except (OSError, ValueError, RuntimeError):
            continue
        try:
            target.relative_to(ws)
            return True
        except ValueError:
            # Windows 大小写不一致 fallback
            try:
                target_str = str(target).lower()
                ws_str = str(ws).lower()
                if target_str == ws_str or target_str.startswith(ws_str + os.sep.lower()):
                    return True
            except (OSError, ValueError):
                continue
    return False


def candidate_path_fields(params: dict) -> list[str]:
    """从工具参数里提取所有可能的路径字段。

    覆盖 OpenAkita 内置工具的常见字段名：path / src / dst / source / target / file_path。
    返回非空字符串列表，按字段顺序。供 ApprovalClassifier refine + safety_immune 检查共用。
    """
    out: list[str] = []
    for key in ("path", "src", "dst", "source", "target", "file_path"):
        value = params.get(key)
        if isinstance(value, str) and value:
            out.append(value)
    return out


def all_paths_inside_workspace(
    params: dict,
    workspace_roots: Path | str | Iterable[Path | str],
) -> bool:
    """如果 params 里**任一**路径字段在 workspace 外，返回 False。

    用于 MUTATING 类工具的 refine：任一 path 越界即升级 MUTATING_GLOBAL（保守）。
    无 path 字段时视为"无法判断"，返回 True（不升级，让矩阵默认决策走）。
    """
    candidates = candidate_path_fields(params)
    if not candidates:
        return True
    return all(is_inside_workspace(p, workspace_roots) for p in candidates)
