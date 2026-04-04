"""Entity disambiguation — rule-based normalization + alias table + optional LLM batch."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openakita.core.brain import Brain

    from .store import RelationalMemoryStore

logger = logging.getLogger(__name__)

# Common Chinese-English term pairs for rule-based normalization
_ZH_EN_MAP: dict[str, str] = {
    "记忆": "memory",
    "记忆模块": "memory_module",
    "记忆系统": "memory_system",
    "数据库": "database",
    "配置": "config",
    "用户": "user",
    "代理": "agent",
    "智能体": "agent",
    "工具": "tool",
    "对话": "conversation",
    "会话": "session",
    "提示词": "prompt",
    "模型": "model",
    "大模型": "llm",
    "文件": "file",
    "目录": "directory",
    "项目": "project",
    "测试": "test",
    "部署": "deploy",
    "前端": "frontend",
    "后端": "backend",
}


class EntityResolver:
    """Resolves entity names to canonical forms using rules and alias table."""

    def __init__(self, store: RelationalMemoryStore, brain: Brain | None = None) -> None:
        self.store = store
        self.brain = brain

    def normalize(self, name: str) -> str:
        """Rule-based normalization: lowercase, strip, Chinese→English common terms."""
        name = name.strip().lower()
        name = re.sub(r"\s+", "_", name)
        name = re.sub(r"['\"]", "", name)

        if name in _ZH_EN_MAP:
            name = _ZH_EN_MAP[name]

        return name

    def resolve(self, name: str) -> str:
        """Resolve a single entity name through normalize → alias lookup."""
        normalized = self.normalize(name)
        return self.store.resolve_entity(normalized)

    def resolve_many(self, names: list[str]) -> dict[str, str]:
        """Resolve multiple entity names. Returns {original: canonical}."""
        result: dict[str, str] = {}
        for name in names:
            result[name] = self.resolve(name)
        return result

    async def resolve_batch_with_llm(self, entities: list[str]) -> dict[str, str]:
        """Use LLM to determine which entity names refer to the same concept.

        Results are persisted in the alias table for future use.
        """
        if not self.brain or len(entities) < 2:
            return self.resolve_many(entities)

        normalized = [self.normalize(e) for e in entities]
        unique = list(set(normalized))
        if len(unique) < 2:
            return dict.fromkeys(entities, unique[0])

        prompt = (
            "Given these entity names extracted from conversations, "
            "group them by whether they refer to the same concept.\n\n"
            f"Entities: {unique}\n\n"
            "Output JSON: {\"groups\": [[\"canonical\", \"alias1\", \"alias2\"], ...]}\n"
            "Each group's first element is the canonical name. "
            "Single-member groups need not be listed."
        )

        try:
            resp = await self.brain.compiler_think(
                prompt=prompt,
                system="You are an entity resolution expert. Output valid JSON only.",
                max_tokens=1024,
            )
            response_text = resp.content if hasattr(resp, "content") else str(resp)
            import json
            import re as _re
            json_str = response_text.strip()
            _match = _re.search(r"```(?:json)?\s*([\s\S]*?)```", json_str)
            if _match:
                json_str = _match.group(1).strip()
            data = json.loads(json_str)
            groups = data.get("groups", [])

            mapping: dict[str, str] = {}
            for group in groups:
                if not isinstance(group, list) or len(group) < 2:
                    continue
                canonical = group[0]
                for alias in group[1:]:
                    mapping[alias] = canonical
                    self.store.add_alias(alias, canonical, confidence=0.7, source="llm")

            result: dict[str, str] = {}
            for orig, norm in zip(entities, normalized, strict=False):
                result[orig] = mapping.get(norm, norm)
            return result

        except Exception as e:
            logger.warning(f"[EntityResolver] LLM batch resolution failed: {e}")
            return self.resolve_many(entities)
