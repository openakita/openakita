"""Multi-endpoint failover view over :class:`openakita.llm.client.LLMClient`.

Extracted from the legacy ``openakita.core.brain.Brain`` god-class
(continuation plan section 5, P-RC-4). The legacy Brain carried nine
thin wrappers around its embedded ``LLMClient`` so the API server and
CLI could drive failover from outside. Stapling that surface to Brain
made it inaccessible to the v2 supervisor (which never owns a Brain)
and noisy to unit-test (had to construct the whole Brain first).

This module is the borrow: construct it once with an ``LLMClient`` and
call the view methods directly. The agent rewrite composes this view
alongside the circuit-breaker, multimodal, and streaming helpers; the
legacy Brain delegates to the same view so behaviour stays identical
through the cutover.
"""

from __future__ import annotations

from typing import Any


class EndpointFailoverView:
    """Failover/endpoint-management surface over an LLMClient.

    The view is a plain class (not a dataclass) so static typing stays
    simple and ``isinstance`` checks work without subclassing. The
    client is borrowed; the view holds no mutable fields beyond the
    reference, so two consumers may share one client through two
    views without state corruption.
    """

    __slots__ = ("_client",)

    def __init__(self, llm_client: Any) -> None:
        self._client = llm_client

    # ---- Read-only state -------------------------------------------------

    def current_endpoint_info(self) -> dict[str, Any]:
        """Return ``{name, model, healthy}`` for the first healthy provider.

        Falls back to the first configured endpoint when no provider
        is healthy, then to ``{"name": "none", ...}`` when the client
        has no endpoints. Byte-faithful to the legacy
        ``Brain.get_current_endpoint_info`` contract.
        """
        for name, provider in self._client.providers.items():
            if getattr(provider, "is_healthy", False):
                return {"name": name, "model": getattr(provider, "model", ""), "healthy": True}
        endpoints = self._client.endpoints
        if endpoints:
            return {
                "name": getattr(endpoints[0], "name", ""),
                "model": getattr(endpoints[0], "model", ""),
                "healthy": False,
            }
        return {"name": "none", "model": "none", "healthy": False}

    async def health_check(self) -> dict[str, bool]:
        """Async health probe across every endpoint."""
        return await self._client.health_check()

    def current_model_info(self, conversation_id: str | None = None) -> dict[str, Any]:
        """Dict-shape rendering of the current ``ModelInfo`` (or error)."""
        model = self._client.get_current_model(conversation_id=conversation_id)
        if model is None:
            return {"error": "no available model"}
        return {
            "name": getattr(model, "name", ""),
            "model": getattr(model, "model", ""),
            "provider": getattr(model, "provider", ""),
            "is_healthy": getattr(model, "is_healthy", False),
            "is_override": getattr(model, "is_override", False),
            "capabilities": list(getattr(model, "capabilities", []) or []),
            "note": getattr(model, "note", ""),
        }

    def list_models(self) -> list[dict[str, Any]]:
        """Dict-shape rendering of every available ``ModelInfo``."""
        return [
            {
                "name": getattr(m, "name", ""),
                "model": getattr(m, "model", ""),
                "provider": getattr(m, "provider", ""),
                "priority": getattr(m, "priority", 0),
                "is_healthy": getattr(m, "is_healthy", False),
                "is_current": getattr(m, "is_current", False),
                "is_override": getattr(m, "is_override", False),
                "capabilities": list(getattr(m, "capabilities", []) or []),
                "note": getattr(m, "note", ""),
            }
            for m in self._client.list_available_models()
        ]

    def override_status(self) -> dict | None:
        """Active manual override descriptor, or ``None``."""
        return self._client.get_override_status()

    def next_fallback_model(self, conversation_id: str | None = None) -> str:
        """Name of the next-priority configured endpoint (empty when none)."""
        return self._client.get_next_endpoint(conversation_id) or ""

    # ---- State mutations -------------------------------------------------

    def switch_model(
        self,
        endpoint_name: str,
        hours: float = 12.0,
        reason: str = "",
        *,
        conversation_id: str | None = None,
        policy: str = "prefer",
    ) -> tuple[bool, str]:
        """Stage a temporary override pointing at ``endpoint_name``."""
        return self._client.switch_model(
            endpoint_name, hours, reason, conversation_id=conversation_id, policy=policy
        )

    def restore_default(self, conversation_id: str | None = None) -> tuple[bool, str]:
        """Drop the manual override and revert to default priority."""
        return self._client.restore_default(conversation_id=conversation_id)

    def update_priority(self, priority_order: list[str]) -> tuple[bool, str]:
        """Rewrite the persisted endpoint priority order."""
        return self._client.update_priority(priority_order)


__all__ = ["EndpointFailoverView"]
