"""WeCom QR code onboarding API — web mode support for QR-based bot configuration."""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/wecom/onboard", tags=["wecom-onboard"])


class StartRequest(BaseModel):
    pass


class PollRequest(BaseModel):
    scode: str


@router.post("/start")
async def onboard_start(body: StartRequest):
    """Generate QR code for WeCom bot configuration. Returns auth_url and scode."""
    try:
        from openakita.setup.wecom_onboard import WecomOnboard

        ob = WecomOnboard()
        data = await ob.generate()
        result = {
            "auth_url": data.get("auth_url", ""),
            "scode": data.get("scode", ""),
        }
        return JSONResponse(content=result)
    except Exception as e:
        logger.error(f"WeCom onboard start failed: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/poll")
async def onboard_poll(body: PollRequest):
    """Poll QR scan result once. Returns bot_id + secret on success."""
    try:
        from openakita.setup.wecom_onboard import WecomOnboard

        ob = WecomOnboard()
        result = await ob.poll(body.scode)
        return JSONResponse(content=result)
    except Exception as e:
        logger.error(f"WeCom onboard poll failed: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})
