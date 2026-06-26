"""WeChat iLink Bot onboarding API — QR code login for personal WeChat bot."""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/wechat/onboard", tags=["wechat-onboard"])


class PollRequest(BaseModel):
    qrcode: str


@router.post("/start")
async def onboard_start():
    """Fetch login QR code. Returns qrcode (identifier) and qrcode_url."""
    try:
        from openakita.setup.wechat_onboard import WeChatOnboard

        ob = WeChatOnboard()
        try:
            result = await ob.fetch_qrcode()
            return JSONResponse(content=result)
        finally:
            await ob.close()
    except Exception as e:
        logger.error(f"WeChat onboard start failed: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/poll")
async def onboard_poll(body: PollRequest):
    """Poll QR login status once (long-poll)."""
    try:
        from openakita.setup.wechat_onboard import WeChatOnboard

        ob = WeChatOnboard()
        try:
            result = await ob.poll_status(body.qrcode)
            return JSONResponse(content=result)
        finally:
            await ob.close()
    except Exception as e:
        logger.error(f"WeChat onboard poll failed: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})
