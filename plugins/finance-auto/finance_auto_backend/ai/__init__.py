"""``finance_auto_backend.ai`` — M2 AI sub-system.

Owned by the M2 AI backend worker (territory: ``backend/ai/**``).  Layout
follows v0.2 Part 2 §3 / §4 / §5 / §9:

* ``desensitizer.py`` — three-tier sensitivity scrubber + PII config (§3).
* ``pii_config.py``   — YAML loader + dataclass mapping (§3.2).
* ``consent.py``      — consent checker hook + WebSocket emit (§4).
* ``router.py``       — local-first LLM router (§5).
* ``audit.py``        — llm_call_audit insert + payload hash helper (§7).
* ``models.py``       — pydantic types for the AI tables (§8).
* ``scenarios/``      — six scenario implementations (S1–S6 per §6).
* ``ws.py``           — FastAPI WebSocket endpoint for consent dialog channel.
* ``event_bus.py``    — InMemoryEventBus shim (parse_issue.created listener).

The package re-exports the most-commonly-used public symbols so the rest
of the plugin can ``from .ai import desensitize, check_consent`` etc.
"""

from __future__ import annotations

__all__: list[str] = []
