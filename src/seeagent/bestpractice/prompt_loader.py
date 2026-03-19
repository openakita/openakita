"""PromptTemplateLoader — 加载和渲染 BP 提示模板。"""

from __future__ import annotations

import logging
from pathlib import Path
from string import Template
from typing import Any

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "prompts"


class PromptTemplateLoader:
    """加载 bestpractice/prompts/ 目录下的模板文件。"""

    def __init__(self, template_dir: Path | None = None) -> None:
        self._dir = template_dir or _TEMPLATE_DIR
        self._cache: dict[str, Template] = {}

    def load(self, name: str) -> Template:
        """加载模板（带缓存）。name 不含 .md 后缀。"""
        if name in self._cache:
            return self._cache[name]

        path = self._dir / f"{name}.md"
        if not path.exists():
            logger.warning(f"[BP] Template not found: {path}")
            return Template("")

        text = path.read_text(encoding="utf-8")
        tmpl = Template(text)
        self._cache[name] = tmpl
        return tmpl

    def render(self, name: str, **kwargs: Any) -> str:
        """加载 + 变量注入。未提供的变量保持原样。"""
        tmpl = self.load(name)
        return tmpl.safe_substitute(**kwargs)

    def clear_cache(self) -> None:
        self._cache.clear()
