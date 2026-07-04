"""Helpers for writing rows to ``llm_call_audit`` (schema v8).

Used by every scenario after a successful or failed LLM call.  Kept as
free functions so callers don't need to allocate a class — passes the
``FinanceAutoService`` (which carries the DB handle) directly.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .desensitizer import SensitivityLevel, payload_sha256
from .models import LLMOutcome
from .router import LLMResponse

if TYPE_CHECKING:
    from ..routes import FinanceAutoService

logger = logging.getLogger(__name__)

DEBUG_DIR = Path("data") / "llm_debug"
DEBUG_FILE_PREFIX = "finance-"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _serialise(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


async def record_llm_call(
    service: FinanceAutoService,
    *,
    scenario_id: str,
    sensitivity_level: SensitivityLevel,
    outcome: LLMOutcome,
    desensitized_payload: Any,
    response: LLMResponse | None = None,
    consent_id: int | None = None,
    org_id: str | None = None,
    user_id: str = "local",
    duration_ms: int = 0,
    error_message: str | None = None,
    desensitized_payload_path: str | Path | None = None,
) -> int:
    """Insert one ``llm_call_audit`` row and return its primary key.

    The desensitised payload is hashed (sha-256) regardless of whether
    the call succeeded — that's the dedup key the AI history page uses
    to cluster repeat calls.  ``desensitized_payload_path`` should
    point to a file under ``data/llm_debug/finance-*.json``; if absent
    we don't try to write the file (audit row will still be valid).
    """
    payload_text = _serialise(desensitized_payload)
    payload_hash = payload_sha256(desensitized_payload)
    size_bytes = len(payload_text.encode("utf-8"))

    provider = ""
    model_name = ""
    is_local = False
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    if response is not None:
        provider = response.provider or ""
        model_name = response.model_id or ""
        is_local = bool(response.is_local)
        prompt_tokens = int(response.tokens_prompt or 0) or None
        completion_tokens = int(response.tokens_completion or 0) or None
        if duration_ms == 0:
            duration_ms = int(response.duration_ms or 0)

    await service.db.conn.execute(
        "INSERT INTO llm_call_audit("
        "timestamp, user_id, org_id, scenario_id, sensitivity_level, "
        "model_provider, model_name, is_local_endpoint, payload_hash, "
        "payload_size_bytes, prompt_tokens, completion_tokens, consent_id, "
        "outcome, error_message, desensitized_payload_path, duration_ms"
        ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            _utcnow_iso(),
            user_id,
            org_id,
            scenario_id,
            sensitivity_level,
            provider,
            model_name,
            int(is_local),
            payload_hash,
            size_bytes,
            prompt_tokens,
            completion_tokens,
            consent_id,
            outcome,
            error_message,
            (str(desensitized_payload_path) if desensitized_payload_path else None),
            duration_ms or None,
        ),
    )
    await service.db.conn.commit()
    async with service.db.conn.execute(
        "SELECT id FROM llm_call_audit ORDER BY id DESC LIMIT 1"
    ) as cur:
        row = await cur.fetchone()
    return int(row["id"]) if row else 0


def maybe_persist_debug_snapshot(
    *,
    scenario_id: str,
    desensitized_payload: Any,
    response: LLMResponse | None,
    base_dir: Path | None = None,
) -> Path | None:
    """Optionally write a JSON snapshot under ``data/llm_debug/finance-*.json``.

    The host-level ``llm_debug_enabled`` toggle (config.py:331) controls
    whether the host writes generic snapshots; we always write a
    plugin-prefixed file so the AI history page can render the actual
    payload that went out.  Returns the path or ``None`` if writing
    failed (never raises).
    """
    target_dir = base_dir or DEBUG_DIR
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        return None
    fname = f"{DEBUG_FILE_PREFIX}{scenario_id}_{_utcnow_iso().replace(':','-')}.json"
    target = target_dir / fname
    record = {
        "scenario_id": scenario_id,
        "payload": desensitized_payload,
        "response_text": (response.text if response else None),
        "provider": (response.provider if response else None),
        "model": (response.model_id if response else None),
        "is_local": (response.is_local if response else None),
    }
    try:
        target.write_text(
            _serialise(record), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001
        return None
    return target


__all__ = [
    "DEBUG_DIR",
    "DEBUG_FILE_PREFIX",
    "maybe_persist_debug_snapshot",
    "record_llm_call",
]
