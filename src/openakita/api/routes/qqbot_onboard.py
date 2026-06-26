"""QQ Bot OpenClaw onboarding API — web mode support for QR code bot creation."""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/qqbot/onboard", tags=["qqbot-onboard"])


class PollRequest(BaseModel):
    session_id: str


@router.post("/start")
async def onboard_start():
    """Create login session. Returns session_id and qr_url."""
    try:
        from openakita.setup.qqbot_onboard import QQBotOnboard

        ob = QQBotOnboard()
        try:
            result = await ob.create_session()
            return JSONResponse(content=result)
        finally:
            await ob.close()
    except Exception as e:
        logger.error(f"QQBot onboard start failed: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/poll")
async def onboard_poll(body: PollRequest):
    """Poll QR login status once."""
    try:
        from openakita.setup.qqbot_onboard import QQBotOnboard

        ob = QQBotOnboard()
        try:
            result = await ob.poll(body.session_id)
            return JSONResponse(content=result)
        finally:
            await ob.close()
    except Exception as e:
        logger.error(f"QQBot onboard poll failed: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/create")
async def onboard_create():
    """Create a QQ bot. Returns app_id, app_secret, bot_name."""
    try:
        from openakita.setup.qqbot_onboard import QQBotOnboard

        ob = QQBotOnboard()
        try:
            result = await ob.create_bot()
            return JSONResponse(content=result)
        finally:
            await ob.close()
    except Exception as e:
        logger.error(f"QQBot onboard create failed: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/poll-and-create")
async def onboard_poll_and_create(body: PollRequest):
    """Atomic poll + create in one httpx client so cookies carry over."""
    try:
        from openakita.setup.qqbot_onboard import QQBotOnboard

        ob = QQBotOnboard()
        try:
            result = await ob.poll_and_create(body.session_id)
            return JSONResponse(content=result)
        finally:
            await ob.close()
    except Exception as e:
        logger.error(f"QQBot onboard poll-and-create failed: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})
