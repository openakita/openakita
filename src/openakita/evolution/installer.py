"""
Auto-installer

Automatically installs missing dependency packages (pip/npm).
"""

import logging
from dataclasses import dataclass

from ..skills.registry import SkillRegistry
from ..tools.shell import ShellTool
from .analyzer import CapabilityGap

logger = logging.getLogger(__name__)


@dataclass
class InstallResult:
    """Installation result."""

    success: bool
    capability: str
    method: str  # pip, npm, generated
    details: str
    error: str | None = None


class AutoInstaller:
    """
    Auto-installer

    Automatically installs required dependency packages based on capability gaps.
    """

    def __init__(
        self,
        skill_registry: SkillRegistry | None = None,
    ):
        self.registry = skill_registry if skill_registry is not None else SkillRegistry()
        self.shell = ShellTool()

    async def install_capability(self, gap: CapabilityGap) -> InstallResult:
        """
        Install a missing capability.

        Args:
            gap: Capability gap.

        Returns:
            InstallResult
        """
        logger.info(f"Attempting to install capability: {gap.name}")

        # Try different installation methods by priority
        methods = [
            self._try_pip_install,
            self._try_npm_install,
        ]

        for method in methods:
            result = await method(gap)
            if result.success:
                return result

        # All methods failed; suggest creating a custom skill
        return InstallResult(
            success=False,
            capability=gap.name,
            method="none",
            details="Could not auto-install. Consider creating a custom skill to cover this capability.",
            error="Unable to find or install this capability",
        )

    async def _try_pip_install(self, gap: CapabilityGap) -> InstallResult:
        """Try installing via pip."""
        # PyInstaller compat: check if current environment supports pip install
        from openakita.runtime_env import can_pip_install

        if not can_pip_install():
            return InstallResult(
                success=False,
                capability=gap.name,
                method="pip",
                details="Auto pip install is not supported in packaged environments. Please install via the Settings Center.",
            )

        # Common Python package mapping
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
            # Try using the capability name directly as a package name
            package = gap.name.lower().replace(" ", "-")

        logger.info(f"Trying pip install: {package}")

        result = await self.shell.pip_install(package)

        if result.success:
            return InstallResult(
                success=True,
                capability=gap.name,
                method="pip",
                details=f"Installed {package} via pip",
            )
        else:
            return InstallResult(
                success=False,
                capability=gap.name,
                method="pip",
                details=f"pip install {package} failed",
                error=result.stderr,
            )

    async def _try_npm_install(self, gap: CapabilityGap) -> InstallResult:
        """Try installing via npm."""
        # Check if an npm package is needed
        npm_keywords = ["前端", "frontend", "react", "vue", "node", "js", "javascript"]

        if not any(kw in gap.name.lower() for kw in npm_keywords):
            return InstallResult(
                success=False,
                capability=gap.name,
                method="npm",
                details="No npm package needed",
            )

        package = gap.name.lower().replace(" ", "-")

        logger.info(f"Trying npm install: {package}")

        result = await self.shell.npm_install(package)

        if result.success:
            return InstallResult(
                success=True,
                capability=gap.name,
                method="npm",
                details=f"Installed {package} via npm",
            )
        else:
            return InstallResult(
                success=False,
                capability=gap.name,
                method="npm",
                details=f"npm install {package} failed",
                error=result.stderr,
            )

    async def install_all(self, gaps: list[CapabilityGap]) -> list[InstallResult]:
        """
        Install all missing capabilities.

        Args:
            gaps: List of capability gaps.

        Returns:
            List of installation results.
        """
        results = []

        # Sort by priority
        sorted_gaps = sorted(gaps, key=lambda g: -g.priority)

        for gap in sorted_gaps:
            result = await self.install_capability(gap)
            results.append(result)

            if not result.success:
                logger.warning(f"Failed to install {gap.name}: {result.error}")

        return results
