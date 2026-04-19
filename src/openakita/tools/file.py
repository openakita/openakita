"""
File tool - File operations
"""

import logging
import re
import shutil
from pathlib import Path

import aiofiles
import aiofiles.os

logger = logging.getLogger(__name__)

DEFAULT_IGNORE_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".next",
    ".nuxt",
    "coverage",
    ".tox",
    ".eggs",
    ".cache",
    ".parcel-cache",
    "egg-info",
}


class FileTool:
    """File operation tool"""

    def __init__(self, base_path: str | None = None):
        self.base_path = Path(base_path) if base_path else Path.cwd()

    def _resolve_path(self, path: str) -> Path:
        """Resolve path (supports relative and absolute paths)"""
        p = Path(path)
        if p.is_absolute():
            return p
        return self.base_path / p

    # Binary file extensions
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
        Read file content

        Args:
            path: File path
            encoding: Encoding

        Returns:
            File content (returns message for binary files)
        """
        file_path = self._resolve_path(path)
        logger.debug(f"Reading file: {file_path}")

        # Check if it is a binary file
        suffix = file_path.suffix.lower()
        if suffix in self.BINARY_EXTENSIONS:
            # Get file size
            stat = await aiofiles.os.stat(file_path)
            size_kb = stat.st_size / 1024
            return f"[Binary file: {file_path.name}, type: {suffix}, size: {size_kb:.1f}KB - cannot be read as text]"

        try:
            async with aiofiles.open(file_path, encoding=encoding) as f:
                return await f.read()
        except UnicodeDecodeError:
            # Try detecting encoding or return binary message
            stat = await aiofiles.os.stat(file_path)
            size_kb = stat.st_size / 1024
            return f"[File cannot be decoded: {file_path.name}, size: {size_kb:.1f}KB - may be a binary file or uses non-{encoding} encoding]"

    async def write(
        self,
        path: str,
        content: str,
        encoding: str = "utf-8",
        create_dirs: bool = True,
    ) -> None:
        """
        Write file

        Args:
            path: File path
            content: Content
            encoding: Encoding
            create_dirs: Whether to auto-create directories
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
        Append content to file

        Args:
            path: File path
            content: Content
            encoding: Encoding
        """
        file_path = self._resolve_path(path)
        logger.debug(f"Appending to file: {file_path}")

        async with aiofiles.open(file_path, mode="a", encoding=encoding) as f:
            await f.write(content)

    async def _read_preserving_newlines(self, path: str) -> str:
        """Read file content, preserve original line breaks (no CRLF→LF conversion).

        Normal ``read()`` uses text mode which converts ``\\r\\n`` to ``\\n``,
        losing the original line ending style when writing back. This method uses ``newline=''``
        to preserve original byte-level line breaks.
        """
        file_path = self._resolve_path(path)
        suffix = file_path.suffix.lower()
        if suffix in self.BINARY_EXTENSIONS:
            raise ValueError(f"Cannot edit binary file: {file_path.name}")
        try:
            async with aiofiles.open(file_path, encoding="utf-8", newline="") as f:
                return await f.read()
        except UnicodeDecodeError as e:
            raise ValueError(f"Cannot decode file (non-UTF-8): {file_path.name}") from e

    async def _write_preserving_newlines(self, path: str, content: str) -> None:
        """Write file content, preserve original line breaks (no LF→CRLF conversion)."""
        file_path = self._resolve_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(file_path, mode="w", encoding="utf-8", newline="") as f:
            await f.write(content)

    async def edit(
        self,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> dict:
        """Exact string replacement editing (CRLF/LF compatible).

        Use ``newline=''`` for reading/writing, preserve original file line style.
        LLM-generated old_string always has ``\\n`` line breaks, but Windows files may use
        ``\\r\\n``. This method first tries exact matching, and on failure automatically adapts
        ``\\n`` in old_string to ``\\r\\n`` and retries. Writing preserves original line style.

        Returns:
            dict with keys: replaced (int), path (str)
        Raises:
            FileNotFoundError, ValueError
        """
        file_path = self._resolve_path(path)
        if not file_path.is_file():
            raise FileNotFoundError(f"File not found: {file_path}")

        raw = await self._read_preserving_newlines(path)

        # Phase 1: Direct match (file is LF, or old_string already contains CRLF)
        count = raw.count(old_string)

        if count == 0:
            # Phase 2: LLM-provided \n, file is \r\n → adapt and retry
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
        """Pure Python content search (cross-platform, no external tools needed).

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

            # Skip ignored directories
            parts = file_path.relative_to(dir_path).parts
            if any(p in DEFAULT_IGNORE_DIRS for p in parts):
                continue
            # Skip .xxx hidden directories (except common ones like .github)
            if any(
                p.startswith(".") and p not in (".github", ".vscode", ".cursor") for p in parts[:-1]
            ):
                continue

            # Skip binary files
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
                        entry["context_after"] = lines[i + 1 : end]
                    results.append(entry)

        return results

    async def delete(self, path: str) -> bool:
        """Delete a single file or empty directory. Non-empty directories are rejected."""
        file_path = self._resolve_path(path)
        logger.debug(f"Deleting: {file_path}")

        try:
            if file_path.is_file() or file_path.is_symlink():
                await aiofiles.os.remove(file_path)
            elif file_path.is_dir():
                children = list(file_path.iterdir())
                if children:
                    logger.warning(f"Refused to delete non-empty directory {file_path}")
                    return False
                file_path.rmdir()
            return True
        except Exception as e:
            logger.error(f"Failed to delete {file_path}: {e}")
            return False

    async def exists(self, path: str) -> bool:
        """Check if path exists"""
        file_path = self._resolve_path(path)
        return file_path.exists()

    async def is_file(self, path: str) -> bool:
        """Check if it is a file"""
        file_path = self._resolve_path(path)
        return file_path.is_file()

    async def is_dir(self, path: str) -> bool:
        """Check if it is a directory"""
        file_path = self._resolve_path(path)
        return file_path.is_dir()

    async def list_dir(
        self,
        path: str = ".",
        pattern: str = "*",
        recursive: bool = False,
    ) -> list[str]:
        """
        List directory contents

        Args:
            path: Directory path
            pattern: File name pattern
            recursive: Whether to recurse

        Returns:
            List of file paths
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
        Search files

        Args:
            pattern: File name pattern
            path: Search path
            content_pattern: Content match pattern (optional)

        Returns:
            List of matching file paths
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
        Copy file or directory

        Args:
            src: Source path
            dst: Destination path

        Returns:
            Whether successful
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
        Move file or directory

        Args:
            src: Source path
            dst: Destination path

        Returns:
            Whether successful
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
        Create directory

        Args:
            path: Directory path
            parents: Whether to create parent directories

        Returns:
            Whether successful
        """
        dir_path = self._resolve_path(path)

        try:
            dir_path.mkdir(parents=parents, exist_ok=True)
            return True
        except Exception as e:
            logger.error(f"Failed to create directory {dir_path}: {e}")
            return False
