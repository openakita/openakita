"""
File 工具 - 文件操作
"""

import logging
import shutil
from pathlib import Path

import aiofiles
import aiofiles.os

logger = logging.getLogger(__name__)


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

        # Office 文档：尝试提取文本内容
        suffix = file_path.suffix.lower()
        if suffix in (".docx", ".xlsx", ".pptx", ".pdf"):
            try:
                from openakita.channels.media.handler import MediaHandler
                _handler = MediaHandler()
                if suffix == ".docx":
                    text = await _handler._extract_docx(file_path)
                elif suffix == ".xlsx":
                    text = await _handler._extract_xlsx(file_path)
                elif suffix == ".pptx":
                    text = await _handler._extract_pptx(file_path)
                else:
                    text = await _handler._extract_pdf(file_path)
                if text and text.strip():
                    return text
            except ImportError as _imp_err:
                return (
                    f"[{suffix.upper().lstrip('.')} 文件: {file_path.name} — "
                    f"缺少依赖库，无法提取内容。{_imp_err}]"
                )
            except Exception as _err:
                logger.warning(f"Office text extraction failed for {file_path}: {_err}")

        if suffix in self.BINARY_EXTENSIONS:
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

    async def delete(self, path: str) -> bool:
        """
        删除文件或目录

        Args:
            path: 路径

        Returns:
            是否成功
        """
        file_path = self._resolve_path(path)
        logger.debug(f"Deleting: {file_path}")

        try:
            if file_path.is_file():
                await aiofiles.os.remove(file_path)
            elif file_path.is_dir():
                shutil.rmtree(file_path)
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
