"""Machine-authenticated endpoints used by OpenAkita Account."""

from __future__ import annotations

import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from openakita.account.status_store import AccountStatusStore, StatusPropagationError

router = APIRouter(prefix="/api/internal/openakita", tags=["openakita-internal"])


@router.post("/users/status")
async def update_openakita_user_status(request: Request) -> JSONResponse:
    body = await request.body()
    if len(body) > 64 * 1024:
        return JSONResponse(status_code=400, content={"error": "invalid_event_body"})
    store: AccountStatusStore = request.app.state.account_status_store
    try:
        result = await store.process(
            secret=os.environ.get("OPENAKITA_STATUS_PROPAGATION_SECRET", ""),
            window_seconds=int(
                os.environ.get("OPENAKITA_STATUS_PROPAGATION_WINDOW_SECONDS", "300")
            ),
            timestamp=request.headers.get("X-OpenAkita-Timestamp", ""),
            event_id=request.headers.get("X-OpenAkita-Event-ID", ""),
            idempotency_key=request.headers.get("Idempotency-Key", ""),
            signature=request.headers.get("X-OpenAkita-Signature", ""),
            body=body,
        )
    except (ValueError, StatusPropagationError) as exc:
        if isinstance(exc, StatusPropagationError):
            return JSONResponse(status_code=exc.status_code, content={"error": exc.error})
        return JSONResponse(status_code=500, content={"error": "invalid_server_config"})
    return JSONResponse(
        status_code=200,
        content={"status": "ok", "duplicate": result.duplicate},
    )
