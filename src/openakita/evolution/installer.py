"""
自动安装器

自动安装缺失的依赖包 (pip/npm)。
"""

import logging
from dataclasses import dataclass

from ..skills.registry import SkillRegistry
from ..tools.shell import ShellTool
from .analyzer import CapabilityGap

logger = logging.getLogger(__name__)


@dataclass
class InstallResult:
    """安装结果"""

    success: bool
    capability: str
    method: str  # pip, npm, generated
    details: str
    error: str | None = None


class AutoInstaller:
    """
    自动安装器

    根据能力缺口自动安装所需的依赖包。
    """

    def __init__(
        self,
        skill_registry: SkillRegistry | None = None,
    ):
        self.registry = skill_registry if skill_registry is not None else SkillRegistry()
        self.shell = ShellTool()

    async def install_capability(self, gap: CapabilityGap) -> InstallResult:
        """
        安装缺失的能力

        Args:
            gap: 能力缺口

        Returns:
            InstallResult
        """
        logger.info(f"Attempting to install capability: {gap.name}")

        # 按优先级尝试不同的安装方法
        methods = [
            self._try_pip_install,
            self._try_npm_install,
        ]

        for method in methods:
            result = await method(gap)
            if result.success:
                return result

        # 都失败了，建议走“创建技能”的工作流
        return InstallResult(
            success=False,
            capability=gap.name,
            method="none",
            details="无法自动安装，建议创建一个自定义技能来补齐该能力",
            error="无法找到或安装此能力",
        )

    async def _try_pip_install(self, gap: CapabilityGap) -> InstallResult:
        """尝试通过 pip 安装"""
        # PyInstaller 兼容: 检查当前环境是否支持 pip install
        from openakita.runtime_env import can_pip_install
        if not can_pip_install():
            return InstallResult(
                success=False,
                capability=gap.name,
                method="pip",
                details="打包环境下不支持自动 pip install，请通过设置中心安装",
            )

        # 常见的 Python 包映射
        package_mapping = {
            "爬虫": "scrapy",
            "scraping": "beautifulsoup4",
            "http": "httpx",
            "数据处理": "pandas",
            "图像处理": "pillow",
            "pdf": "pypdf",
            "excel": "openpyxl",
            "机器学习": "scikit-learn",
            "深度学习": "torch",
            "数据库": "sqlalchemy",
            "redis": "redis",
            "mongodb": "pymongo",
        }

        package = None
        gap_lower = gap.name.lower()

        for key, pkg in package_mapping.items():
            if key in gap_lower:
                package = pkg
                break

        if not package:
            # 直接尝试用能力名作为包名
            package = gap.name.lower().replace(" ", "-")

        logger.info(f"Trying pip install: {package}")

        result = await self.shell.pip_install(package)

        if result.success:
            return InstallResult(
                success=True,
                capability=gap.name,
                method="pip",
                details=f"通过 pip 安装了 {package}",
            )
        else:
            return InstallResult(
                success=False,
                capability=gap.name,
                method="pip",
                details=f"pip install {package} 失败",
                error=result.stderr,
            )

    async def _try_npm_install(self, gap: CapabilityGap) -> InstallResult:
        """尝试通过 npm 安装"""
        # 检查是否需要 npm 包
        npm_keywords = ["前端", "frontend", "react", "vue", "node", "js", "javascript"]

        if not any(kw in gap.name.lower() for kw in npm_keywords):
            return InstallResult(
                success=False,
                capability=gap.name,
                method="npm",
                details="不需要 npm 包",
            )

        package = gap.name.lower().replace(" ", "-")

        logger.info(f"Trying npm install: {package}")

        result = await self.shell.npm_install(package)

        if result.success:
            return InstallResult(
                success=True,
                capability=gap.name,
                method="npm",
                details=f"通过 npm 安装了 {package}",
            )
        else:
            return InstallResult(
                success=False,
                capability=gap.name,
                method="npm",
                details=f"npm install {package} 失败",
                error=result.stderr,
            )

    async def install_all(self, gaps: list[CapabilityGap]) -> list[InstallResult]:
        """
        安装所有缺失的能力

        Args:
            gaps: 能力缺口列表

        Returns:
            安装结果列表
        """
        results = []

        # 按优先级排序
        sorted_gaps = sorted(gaps, key=lambda g: -g.priority)

        for gap in sorted_gaps:
            result = await self.install_capability(gap)
            results.append(result)

            if not result.success:
                logger.warning(f"Failed to install {gap.name}: {result.error}")

        return results
