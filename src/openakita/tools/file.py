"""
File 工具 - 文件操作
"""

import logging
import re
import shutil
from pathlib import Path

import aiofiles
import aiofiles.os

logger = logging.getLogger(__name__)

DEFAULT_IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist",
    "build", ".next", ".nuxt", "coverage", ".tox", ".eggs",
    ".cache", ".parcel-cache", "egg-info",
}


class FileTool:
    """文件操作工具"""

    def __init__(self, base_path: str | None = None):
        self.base_path = Path(base_path) if base_path else Path.cwd()

    def _resolve_path(self, path: str) -> Path:
        """解析路径（支持相对路径和绝对路径）"""
        p = Path(path)
        if p.is_absolute():
            return p
        return self.base_path / p

    # 二进制文件扩展名
    BINARY_EXTENSIONS = {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".ico",
        ".webp",
        ".svg",
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".zip",
        ".rar",
        ".7z",
        ".tar",
        ".gz",
        ".bz2",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".mp3",
        ".mp4",
        ".avi",
        ".mkv",
        ".wav",
        ".flac",
        ".ttf",
        ".otf",
        ".woff",
        ".woff2",
        ".pyc",
        ".pyo",
        ".class",
    }

    async def read(self, path: str, encoding: str = "utf-8") -> str:
        """
        读取文件内容

        Args:
            path: 文件路径
            encoding: 编码

        Returns:
            文件内容（二进制文件返回提示信息）
        """
        file_path = self._resolve_path(path)
        logger.debug(f"Reading file: {file_path}")

        if file_path.is_dir():
            raise IsADirectoryError(f"'{file_path}' 是一个目录而非文件")

        suffix = file_path.suffix.lower()
        if suffix in self.BINARY_EXTENSIONS:
            # 获取文件大小
            stat = await aiofiles.os.stat(file_path)
            size_kb = stat.st_size / 1024
            return f"[二进制文件: {file_path.name}, 类型: {suffix}, 大小: {size_kb:.1f}KB - 无法作为文本读取]"

        try:
            async with aiofiles.open(file_path, encoding=encoding) as f:
                return await f.read()
        except UnicodeDecodeError:
            # 尝试检测编码或返回二进制提示
            stat = await aiofiles.os.stat(file_path)
            size_kb = stat.st_size / 1024
            return f"[无法解码的文件: {file_path.name}, 大小: {size_kb:.1f}KB - 可能是二进制文件或使用了非 {encoding} 编码]"

    async def write(
        self,
        path: str,
        content: str,
        encoding: str = "utf-8",
        create_dirs: bool = True,
    ) -> None:
        """
        写入文件

        Args:
            path: 文件路径
            content: 内容
            encoding: 编码
            create_dirs: 是否自动创建目录
        """
        file_path = self._resolve_path(path)

        if create_dirs:
            file_path.parent.mkdir(parents=True, exist_ok=True)

        logger.debug(f"Writing file: {file_path}")

        async with aiofiles.open(file_path, mode="w", encoding=encoding) as f:
            await f.write(content)

    async def append(
        self,
        path: str,
        content: str,
        encoding: str = "utf-8",
    ) -> None:
        """
        追加内容到文件

        Args:
            path: 文件路径
            content: 内容
            encoding: 编码
        """
        file_path = self._resolve_path(path)
        logger.debug(f"Appending to file: {file_path}")

        async with aiofiles.open(file_path, mode="a", encoding=encoding) as f:
            await f.write(content)

    async def _read_preserving_newlines(self, path: str) -> str:
        """读取文件内容，保留原始换行符（不做 CRLF→LF 转换）。

        普通 ``read()`` 使用 text mode 会将 ``\\r\\n`` 转为 ``\\n``，
        导致写回时丢失原有换行风格。本方法使用 ``newline=''``
        保留原始字节级换行符。
        """
        file_path = self._resolve_path(path)
        suffix = file_path.suffix.lower()
        if suffix in self.BINARY_EXTENSIONS:
            raise ValueError(f"Cannot edit binary file: {file_path.name}")
        try:
            async with aiofiles.open(
                file_path, encoding="utf-8", newline=""
            ) as f:
                return await f.read()
        except UnicodeDecodeError as e:
            raise ValueError(
                f"Cannot decode file (non-UTF-8): {file_path.name}"
            ) from e

    async def _write_preserving_newlines(self, path: str, content: str) -> None:
        """写入文件内容，保留原始换行符（不做 LF→CRLF 转换）。"""
        file_path = self._resolve_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(
            file_path, mode="w", encoding="utf-8", newline=""
        ) as f:
            await f.write(content)

    async def edit(
        self,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> dict:
        """精确字符串替换式编辑（兼容 CRLF/LF）。

        使用 ``newline=''`` 读写，保留文件原始换行风格。LLM 产生的
        old_string 换行符始终是 ``\\n``，但 Windows 文件可能使用
        ``\\r\\n``。本方法先尝试原始匹配，失败后自动将 old_string 中的
        ``\\n`` 适配为 ``\\r\\n`` 重试，写回时保留文件原有换行风格。

        Returns:
            dict with keys: replaced (int), path (str)
        Raises:
            FileNotFoundError, ValueError
        """
        file_path = self._resolve_path(path)
        if not file_path.is_file():
            raise FileNotFoundError(f"File not found: {file_path}")

        raw = await self._read_preserving_newlines(path)

        # Phase 1: 直接匹配（文件本身就是 LF，或 old_string 已包含 CRLF）
        count = raw.count(old_string)

        if count == 0:
            # Phase 2: LLM 给的 \n，文件是 \r\n → 适配后重试
            if "\r\n" in raw and "\n" in old_string:
                adapted_old = old_string.replace("\n", "\r\n")
                count = raw.count(adapted_old)
                if count == 0:
                    raise ValueError(
                        "old_string not found in file (tried both LF and CRLF matching)"
                    )
                if count > 1 and not replace_all:
                    raise ValueError(
                        f"old_string found {count} times in file, "
                        "set replace_all=true or provide more surrounding context"
                    )
                adapted_new = new_string.replace("\n", "\r\n")
                limit = -1 if replace_all else 1
                result = raw.replace(adapted_old, adapted_new, limit)
            else:
                raise ValueError("old_string not found in file")
        else:
            if count > 1 and not replace_all:
                raise ValueError(
                    f"old_string found {count} times in file, "
                    "set replace_all=true or provide more surrounding context"
                )
            limit = -1 if replace_all else 1
            result = raw.replace(old_string, new_string, limit)

        replaced = count if replace_all else 1
        await self._write_preserving_newlines(path, result)
        return {"replaced": replaced, "path": str(file_path)}

    async def grep(
        self,
        pattern: str,
        path: str = ".",
        *,
        include: str | None = None,
        context_lines: int = 0,
        max_results: int = 50,
        case_insensitive: bool = False,
    ) -> list[dict]:
        """纯 Python 内容搜索（跨平台，无需外部工具）。

        Returns:
            list of dicts: {file, line, text, context_before, context_after}
        """
        flags = re.IGNORECASE if case_insensitive else 0
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            raise ValueError(f"Invalid regex pattern: {e}") from e

        dir_path = self._resolve_path(path)
        if dir_path.is_file():
            if not include:
                include = dir_path.name
            dir_path = dir_path.parent
        if not dir_path.is_dir():
            raise FileNotFoundError(f"Directory not found: {dir_path}")

        file_glob = include or "*"
        results: list[dict] = []

        for file_path in dir_path.rglob(file_glob):
            if len(results) >= max_results:
                break

            if not file_path.is_file():
                continue

            # 跳过忽略目录
            parts = file_path.relative_to(dir_path).parts
            if any(p in DEFAULT_IGNORE_DIRS for p in parts):
                continue
            # 跳过 .xxx 隐藏目录（除 .github 等常用目录）
            if any(
                p.startswith(".") and p not in (".github", ".vscode", ".cursor")
                for p in parts[:-1]
            ):
                continue

            # 跳过二进制文件
            if file_path.suffix.lower() in self.BINARY_EXTENSIONS:
                continue

            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except (OSError, PermissionError):
                continue

            lines = text.splitlines()
            rel = str(file_path.relative_to(dir_path))

            for i, line in enumerate(lines):
                if len(results) >= max_results:
                    break
                if regex.search(line):
                    entry: dict = {
                        "file": rel,
                        "line": i + 1,
                        "text": line,
                    }
                    if context_lines > 0:
                        start = max(0, i - context_lines)
                        end = min(len(lines), i + context_lines + 1)
                        entry["context_before"] = lines[start:i]
                        entry["context_after"] = lines[i + 1:end]
                    results.append(entry)

        return results

    async def delete(self, path: str) -> bool:
        """删除单个文件或空目录。非空目录一律拒绝。"""
        file_path = self._resolve_path(path)
        logger.debug(f"Deleting: {file_path}")

        try:
            if file_path.is_file() or file_path.is_symlink():
                await aiofiles.os.remove(file_path)
            elif file_path.is_dir():
                children = list(file_path.iterdir())
                if children:
                    logger.warning(
                        f"Refused to delete non-empty directory {file_path}"
                    )
                    return False
                file_path.rmdir()
            return True
        except Exception as e:
            logger.error(f"Failed to delete {file_path}: {e}")
            return False

    async def exists(self, path: str) -> bool:
        """检查路径是否存在"""
        file_path = self._resolve_path(path)
        return file_path.exists()

    async def is_file(self, path: str) -> bool:
        """检查是否是文件"""
        file_path = self._resolve_path(path)
        return file_path.is_file()

    async def is_dir(self, path: str) -> bool:
        """检查是否是目录"""
        file_path = self._resolve_path(path)
        return file_path.is_dir()

    async def list_dir(
        self,
        path: str = ".",
        pattern: str = "*",
        recursive: bool = False,
    ) -> list[str]:
        """
        列出目录内容

        Args:
            path: 目录路径
            pattern: 文件名模式
            recursive: 是否递归

        Returns:
            文件路径列表
        """
        dir_path = self._resolve_path(path)

        if recursive:
            return [str(p.relative_to(dir_path)) for p in dir_path.rglob(pattern)]
        else:
            return [str(p.relative_to(dir_path)) for p in dir_path.glob(pattern)]

    async def search(
        self,
        pattern: str,
        path: str = ".",
        content_pattern: str | None = None,
    ) -> list[str]:
        """
        搜索文件

        Args:
            pattern: 文件名模式
            path: 搜索路径
            content_pattern: 内容匹配模式（可选）

        Returns:
            匹配的文件路径列表
        """
        import re

        dir_path = self._resolve_path(path)
        matches = []

        for file_path in dir_path.rglob(pattern):
            if file_path.is_file():
                if content_pattern:
                    try:
                        content = file_path.read_text(encoding="utf-8")
                        if re.search(content_pattern, content):
                            matches.append(str(file_path.relative_to(dir_path)))
                    except Exception:
                        pass
                else:
                    matches.append(str(file_path.relative_to(dir_path)))

        return matches

    async def copy(self, src: str, dst: str) -> bool:
        """
        复制文件或目录

        Args:
            src: 源路径
            dst: 目标路径

        Returns:
            是否成功
        """
        src_path = self._resolve_path(src)
        dst_path = self._resolve_path(dst)

        try:
            if src_path.is_file():
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, dst_path)
            else:
                shutil.copytree(src_path, dst_path)
            return True
        except Exception as e:
            logger.error(f"Failed to copy {src_path} to {dst_path}: {e}")
            return False

    async def move(self, src: str, dst: str) -> bool:
        """
        移动文件或目录

        Args:
            src: 源路径
            dst: 目标路径

        Returns:
            是否成功
        """
        src_path = self._resolve_path(src)
        dst_path = self._resolve_path(dst)

        try:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(src_path, dst_path)
            return True
        except Exception as e:
            logger.error(f"Failed to move {src_path} to {dst_path}: {e}")
            return False

    async def mkdir(self, path: str, parents: bool = True) -> bool:
        """
        创建目录

        Args:
            path: 目录路径
            parents: 是否创建父目录

        Returns:
            是否成功
        """
        dir_path = self._resolve_path(path)

        try:
            dir_path.mkdir(parents=parents, exist_ok=True)
            return True
        except Exception as e:
            logger.error(f"Failed to create directory {dir_path}: {e}")
            return False
