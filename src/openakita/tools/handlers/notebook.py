"""
Notebook Handler

Jupyter Notebook (.ipynb) editing:
- edit_notebook: edit or create cells
"""

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)

LANGUAGE_TO_KERNEL = {
    "python": "python3",
    "javascript": "javascript",
    "typescript": "typescript",
    "r": "ir",
    "sql": "sql",
    "shell": "bash",
}


class NotebookHandler:
    TOOLS = ["edit_notebook"]

    def __init__(self, agent: "Agent"):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name == "edit_notebook":
            return await self._edit_notebook(params)
        return f"❌ Unknown notebook tool: {tool_name}"

    async def _edit_notebook(self, params: dict) -> str:
        path = params.get("path", "")
        cell_idx = params.get("cell_idx")
        is_new_cell = params.get("is_new_cell", False)
        cell_language = params.get("cell_language", "python")
        old_string = params.get("old_string", "")
        new_string = params.get("new_string", "")

        if not path:
            return "❌ edit_notebook missing required parameter 'path'."
        if cell_idx is None:
            return "❌ edit_notebook missing required parameter 'cell_idx'."
        if new_string is None:
            return "❌ edit_notebook missing required parameter 'new_string'."

        nb_path = Path(path)
        if nb_path.suffix != ".ipynb":
            return f"❌ File is not a Jupyter Notebook (.ipynb): {path}"

        if is_new_cell:
            return await self._create_cell(nb_path, cell_idx, cell_language, new_string)
        else:
            if not old_string:
                return "❌ old_string must not be empty when editing an existing cell."
            return await self._edit_cell(nb_path, cell_idx, old_string, new_string)

    async def _load_notebook(self, path: Path) -> dict | None:
        if not path.exists():
            return None
        try:
            content = path.read_text(encoding="utf-8")
            return json.loads(content)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to load notebook {path}: {e}")
            return None

    async def _save_notebook(self, path: Path, nb: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(nb, ensure_ascii=False, indent=1)
        path.write_text(content, encoding="utf-8")

    async def _create_cell(self, path: Path, cell_idx: int, language: str, content: str) -> str:
        nb = await self._load_notebook(path)

        if nb is None:
            nb = self._create_empty_notebook(language)

        cells = nb.get("cells", [])
        cell_idx = max(0, min(cell_idx, len(cells)))

        cell_type = "markdown" if language == "markdown" else "code"
        if language == "raw":
            cell_type = "raw"

        source_lines = self._text_to_notebook_source(content)

        new_cell = {
            "cell_type": cell_type,
            "metadata": {},
            "source": source_lines,
        }
        if cell_type == "code":
            new_cell["execution_count"] = None
            new_cell["outputs"] = []

        cells.insert(cell_idx, new_cell)
        nb["cells"] = cells

        await self._save_notebook(path, nb)
        return f"Notebook cell created at index {cell_idx} in {path}"

    async def _edit_cell(self, path: Path, cell_idx: int, old_string: str, new_string: str) -> str:
        nb = await self._load_notebook(path)
        if nb is None:
            return f"❌ Notebook does not exist: {path}"

        cells = nb.get("cells", [])
        if cell_idx < 0 or cell_idx >= len(cells):
            return (
                f"❌ cell_idx={cell_idx} out of range (total {len(cells)} cells, "
                f"index range 0-{len(cells) - 1})"
            )

        cell = cells[cell_idx]
        source_lines = cell.get("source", [])
        source = "".join(source_lines)

        if old_string not in source:
            return (
                f"❌ old_string not found in cell {cell_idx}. "
                "Please check that the text matches exactly (including whitespace and newlines)."
            )

        count = source.count(old_string)
        if count > 1:
            return (
                f"❌ old_string matched {count} times in cell {cell_idx}. "
                "Please include more context to make old_string unique."
            )

        new_source = source.replace(old_string, new_string, 1)
        cell["source"] = self._text_to_notebook_source(new_source)

        await self._save_notebook(path, nb)
        return f"Notebook cell {cell_idx} edited in {path}"

    @staticmethod
    def _text_to_notebook_source(text: str) -> list[str]:
        """Convert text to notebook source format (each line ends with \\n except the last)."""
        if not text:
            return [""]
        raw_lines = text.split("\n")
        return [line + "\n" for line in raw_lines[:-1]] + [raw_lines[-1]]

    @staticmethod
    def _create_empty_notebook(language: str = "python") -> dict:
        kernel = LANGUAGE_TO_KERNEL.get(language, "python3")
        return {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {
                "kernelspec": {
                    "display_name": language.capitalize(),
                    "language": language,
                    "name": kernel,
                },
                "language_info": {
                    "name": language,
                },
            },
            "cells": [],
        }


def create_handler(agent: "Agent"):
    handler = NotebookHandler(agent)
    return handler.handle
