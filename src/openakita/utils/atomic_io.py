"""
原子文件写入工具

提供 temp+rename 模式的原子写入，防止写入中途崩溃导致文件损坏。
支持 .bak 自动备份和读取回退。
"""

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def atomic_json_write(path: Path, data: Any, *, indent: int = 2) -> None:
    """原子写入 JSON 文件（temp + rename 模式）。

    先写入临时文件，验证 JSON 正确性后再重命名为目标文件。
    在 POSIX 系统上 rename 是原子操作；Windows 上通过 replace 保证覆盖。

    Args:
        path: 目标文件路径
        data: 可 JSON 序列化的数据
        indent: JSON 缩进级别
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")

    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)

        # Windows 不支持 rename 覆盖已存在文件，使用 replace
        tmp.replace(path)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


# ---------------------------------------------------------------------------
# Enhanced: atomic write with .bak backup + Windows PermissionError retry
# ---------------------------------------------------------------------------

def safe_write(
    path: Path, content: str, *, backup: bool = True, retries: int = 3, fsync: bool = False
) -> None:
    """Atomic text write with optional .bak backup and Windows retry.

    Flow: backup existing → write to .tmp → (fsync) → rename .tmp → target.
    On Windows, PermissionError on rename is retried up to *retries* times
    before falling back to a direct (non-atomic) write.
    """
    import os

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if backup and path.exists():
        bak = path.with_suffix(path.suffix + ".bak")
        try:
            shutil.copy2(path, bak)
        except OSError as e:
            logger.warning("Failed to create backup %s: %s", bak, e)

    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
        if fsync:
            f.flush()
            os.fsync(f.fileno())

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            tmp.replace(path)
            return
        except PermissionError as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(0.2 * (attempt + 1))

    logger.warning(
        "Atomic rename failed after %d retries (%s), falling back to direct write",
        retries, last_err,
    )
    path.write_text(content, encoding="utf-8")
    tmp.unlink(missing_ok=True)


def safe_json_write(
    path: Path, data: Any, *, indent: int = 2, backup: bool = True, fsync: bool = False
) -> None:
    """Atomic JSON write with .bak backup (convenience wrapper around safe_write)."""
    content = json.dumps(data, ensure_ascii=False, indent=indent) + "\n"
    safe_write(path, content, backup=backup, fsync=fsync)


def append_jsonl(path: Path, obj: dict, *, fsync: bool = False) -> None:
    """Append a single JSON object as one line to a JSONL file (append-only)."""
    import os

    line = json.dumps(obj, ensure_ascii=False, default=str) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        if fsync:
            f.flush()
            os.fsync(f.fileno())


def read_json_safe(path: Path) -> dict | None:
    """Read JSON from *path*, falling back to .bak if primary is missing or corrupt.

    When the backup is used successfully, it is restored to the primary path
    WITHOUT overwriting the existing .bak (avoids the trap of backing up a
    corrupt file over a good backup).

    Returns:
        Parsed dict, or None if neither file is readable.
    """
    path = Path(path)
    bak = path.with_suffix(path.suffix + ".bak")

    for p in (path, bak):
        if not p.exists():
            continue
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read %s: %s", p, e)
            continue

        if p == bak:
            logger.warning("Restored config from backup %s", p)
            try:
                tmp = path.with_suffix(path.suffix + ".tmp")
                tmp.write_text(bak.read_text(encoding="utf-8"), encoding="utf-8")
                tmp.replace(path)
            except OSError as e:
                logger.warning("Failed to restore primary from backup: %s", e)
        return data

    return None
