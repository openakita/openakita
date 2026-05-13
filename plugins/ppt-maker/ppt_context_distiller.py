"""Deterministic context distillation for ppt-maker generation v2."""

from __future__ import annotations

from typing import Any

from ppt_generation_models import ContextPack, SourceDigest


class ContextDistiller:
    """Build a compact, auditable context pack from existing pipeline inputs."""

    def build(
        self,
        *,
        project_id: str,
        outline: dict[str, Any] | None = None,
        table_insights: dict[str, Any] | None = None,
        chart_specs: list[dict[str, Any]] | None = None,
        brand_tokens: dict[str, Any] | None = None,
        source_context: str = "",
    ) -> ContextPack:
        key_facts: list[str] = []
        caveats: list[str] = []
        digests: list[SourceDigest] = []

        if source_context.strip():
            text = source_context.strip()
            digests.append(
                SourceDigest(
                    title="Source context",
                    summary=text[:800],
                    facts=self._sentences(text, limit=8),
                )
            )
            key_facts.extend(self._sentences(text, limit=6))

        if table_insights:
            findings = [str(item) for item in table_insights.get("key_findings", []) if item]
            key_facts.extend(findings[:8])
            caveats.extend(str(item) for item in table_insights.get("risks_and_caveats", []) if item)

        if outline:
            for slide in outline.get("slides", [])[:12]:
                body = str(slide.get("body") or slide.get("purpose") or "").strip()
                if body:
                    key_facts.append(body[:240])

        return ContextPack(
            project_id=project_id,
            source_digests=digests,
            key_facts=list(dict.fromkeys(key_facts))[:16],
            table_insights=table_insights,
            chart_specs=chart_specs or [],
            brand_tokens=brand_tokens,
            caveats=list(dict.fromkeys(caveats))[:10],
        )

    @staticmethod
    def _sentences(text: str, *, limit: int) -> list[str]:
        normalized = " ".join(text.replace("\r", "\n").split())
        if not normalized:
            return []
        parts = []
        for delimiter in ("。", ".", "；", ";", "\n"):
            if delimiter in normalized:
                parts = [item.strip() for item in normalized.split(delimiter) if item.strip()]
                break
        if not parts:
            parts = [normalized]
        return [item[:240] for item in parts[:limit]]

