"""whatsapp-channel: WhatsApp IM adapter plugin for OpenAkita.

Supports two modes:
  - cloud_api: WhatsApp Cloud API (Meta official, token-based)
  - web: WhatsApp Web via Baileys Node.js sidecar (QR code pairing)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

from openakita.channels.base import ChannelAdapter
from openakita.channels.types import (
    MediaFile,
    MediaStatus,
    MessageContent,
    MessageType,
    OutgoingMessage,
    UnifiedMessage,
)
from openakita.plugins.api import PluginAPI, PluginBase

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com"


class WhatsAppAdapter(ChannelAdapter):
    """Dual-mode WhatsApp adapter: Cloud API or Baileys sidecar."""

    channel_name = "whatsapp"

    capabilities = {
        **ChannelAdapter.capabilities,
        "streaming": True,
        "send_image": True,
        "send_file": True,
        "send_voice": True,
        "delete_message": True,
        "edit_message": False,
        "markdown": False,
    }

    def __init__(
        self,
        creds: dict,
        *,
        channel_name: str,
        bot_id: str,
        agent_profile_id: str,
    ) -> None:
        super().__init__(
            channel_name=channel_name,
            bot_id=bot_id,
            agent_profile_id=agent_profile_id,
        )
        self._creds = creds
        self._mode = creds.get("mode", "cloud_api")

        # Cloud API
        self._phone_number_id = creds.get("phone_number_id", "")
        self._access_token = creds.get("access_token", "")
        self._verify_token = creds.get("verify_token", "openakita-verify")
        self._api_version = creds.get("api_version", "v21.0")
        self._webhook_port = int(creds.get("webhook_port", 9881))
        self._webhook_path = creds.get("webhook_path", "/whatsapp/webhook")

        # Baileys sidecar (web mode)
        self._node_path = creds.get("node_path", "node")
        self._bridge_port = int(creds.get("bridge_port", 9882))
        self._bridge_proc: subprocess.Popen | None = None
        self._bridge_data_dir = creds.get("bridge_data_dir", "")

        # HTTP client
        self._http: httpx.AsyncClient | None = None

        # Streaming state
        self._streaming_buffers: dict[str, str] = {}

        # Webhook server
        self._webhook_app = None
        self._webhook_server = None

    # --- Lifecycle ---

    async def start(self) -> None:
        self._http = httpx.AsyncClient(timeout=30)
        if self._mode == "web":
            await self._start_baileys_bridge()
        else:
            await self._start_cloud_webhook()
        self._running = True
        logger.info("WhatsAppAdapter started (mode=%s)", self._mode)

    async def stop(self) -> None:
        self._running = False
        if self._bridge_proc is not None:
            try:
                self._bridge_proc.terminate()
                self._bridge_proc.wait(timeout=5)
            except Exception:
                try:
                    self._bridge_proc.kill()
                except Exception:
                    pass
            self._bridge_proc = None
        if self._webhook_server is not None:
            try:
                self._webhook_server.close()
                await self._webhook_server.wait_closed()
            except Exception:
                pass
            self._webhook_server = None
        if self._http:
            await self._http.aclose()
            self._http = None
        logger.info("WhatsAppAdapter stopped")

    # --- Cloud API: Webhook server ---

    async def _start_cloud_webhook(self) -> None:
        """Start a minimal HTTP server to receive Cloud API webhooks."""
        from aiohttp import web

        app = web.Application()
        app.router.add_get(self._webhook_path, self._webhook_verify)
        app.router.add_post(self._webhook_path, self._webhook_receive)
        self._webhook_app = app

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self._webhook_port)
        await site.start()
        self._webhook_server = runner
        logger.info(
            "WhatsApp Cloud API webhook listening on port %d", self._webhook_port
        )

    async def _webhook_verify(self, request) -> Any:
        """Handle Meta webhook verification (GET)."""
        from aiohttp import web

        mode = request.query.get("hub.mode", "")
        token = request.query.get("hub.verify_token", "")
        challenge = request.query.get("hub.challenge", "")
        if mode == "subscribe" and token == self._verify_token:
            return web.Response(text=challenge)
        return web.Response(status=403, text="Verification failed")

    async def _webhook_receive(self, request) -> Any:
        """Handle inbound Cloud API webhook (POST)."""
        from aiohttp import web

        try:
            body = await request.json()
        except Exception:
            return web.Response(status=400)

        asyncio.create_task(self._process_cloud_payload(body))
        return web.Response(text="OK")

    async def _process_cloud_payload(self, payload: dict) -> None:
        """Parse Cloud API webhook payload into UnifiedMessage."""
        try:
            for entry in payload.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    messages = value.get("messages", [])
                    contacts = value.get("contacts", [])
                    contact_map = {
                        c["wa_id"]: c.get("profile", {}).get("name", "")
                        for c in contacts
                    }
                    for msg in messages:
                        await self._parse_cloud_message(msg, contact_map)
        except Exception as e:
            logger.error("Error processing WhatsApp payload: %s", e, exc_info=True)

    async def _parse_cloud_message(
        self, msg: dict, contact_map: dict
    ) -> None:
        """Convert a single Cloud API message to UnifiedMessage and emit."""
        msg_type = msg.get("type", "text")
        from_id = msg.get("from", "")
        msg_id = msg.get("id", "")
        timestamp = msg.get("timestamp", "")

        text = ""
        images = []
        files = []
        voices = []
        videos = []

        if msg_type == "text":
            text = msg.get("text", {}).get("body", "")
        elif msg_type == "image":
            img = msg.get("image", {})
            text = img.get("caption", "")
            images.append(MediaFile(
                id=img.get("id", ""),
                filename="image.jpg",
                mime_type=img.get("mime_type", "image/jpeg"),
                file_id=img.get("id"),
                status=MediaStatus.PENDING,
            ))
        elif msg_type == "document":
            doc = msg.get("document", {})
            text = doc.get("caption", "")
            files.append(MediaFile(
                id=doc.get("id", ""),
                filename=doc.get("filename", "document"),
                mime_type=doc.get("mime_type", "application/octet-stream"),
                file_id=doc.get("id"),
                status=MediaStatus.PENDING,
            ))
        elif msg_type == "audio":
            audio = msg.get("audio", {})
            voices.append(MediaFile(
                id=audio.get("id", ""),
                filename="audio.ogg",
                mime_type=audio.get("mime_type", "audio/ogg"),
                file_id=audio.get("id"),
                status=MediaStatus.PENDING,
            ))
        elif msg_type == "video":
            vid = msg.get("video", {})
            text = vid.get("caption", "")
            videos.append(MediaFile(
                id=vid.get("id", ""),
                filename="video.mp4",
                mime_type=vid.get("mime_type", "video/mp4"),
                file_id=vid.get("id"),
                status=MediaStatus.PENDING,
            ))

        is_group = msg.get("context", {}).get("group_id") is not None
        group_id = msg.get("context", {}).get("group_id", "")
        chat_id = group_id if is_group else from_id

        context = msg.get("context", {})
        mentioned_ids = []
        if msg_type == "text":
            mentioned_ids = msg.get("text", {}).get("mentioned_ids", [])

        content = MessageContent(
            text=text,
            images=images,
            files=files,
            voices=voices,
            videos=videos,
        )

        is_mentioned = bool(mentioned_ids) or (
            context.get("mentioned") is True
        )

        unified = UnifiedMessage(
            channel=self.channel_name,
            channel_message_id=msg_id,
            user_id=from_id,
            channel_user_id=from_id,
            chat_id=chat_id,
            chat_type="group" if is_group else "private",
            message_type=MessageType.TEXT if msg_type == "text" else MessageType.MIXED,
            content=content,
            reply_to=context.get("id"),
            is_mentioned=is_mentioned,
            is_direct_message=not is_group,
            metadata={
                "contact_name": contact_map.get(from_id, ""),
                "wa_timestamp": timestamp,
            },
        )

        if self._message_callback:
            await self._message_callback(unified)

    # --- Baileys sidecar ---

    async def _start_baileys_bridge(self) -> None:
        """Start the Baileys Node.js bridge as a subprocess."""
        bridge_dir = Path(__file__).parent / "bridge"
        if not (bridge_dir / "index.js").exists():
            logger.error("Baileys bridge not found at %s", bridge_dir)
            return

        data_dir = self._bridge_data_dir or str(
            Path(tempfile.gettempdir()) / "openakita-wa-bridge"
        )
        os.makedirs(data_dir, exist_ok=True)

        env = {
            **os.environ,
            "BRIDGE_PORT": str(self._bridge_port),
            "BRIDGE_DATA_DIR": data_dir,
            "CALLBACK_URL": f"http://127.0.0.1:{self._webhook_port}{self._webhook_path}",
        }

        try:
            self._bridge_proc = subprocess.Popen(
                [self._node_path, str(bridge_dir / "index.js")],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=str(bridge_dir),
            )
            logger.info(
                "Baileys bridge started (pid=%d, port=%d)",
                self._bridge_proc.pid, self._bridge_port,
            )
            await asyncio.sleep(2)
        except FileNotFoundError:
            logger.error("Node.js not found at '%s'", self._node_path)
        except Exception as e:
            logger.error("Failed to start Baileys bridge: %s", e)

    async def get_qr_code(self) -> str | None:
        """Fetch QR code data from the Baileys bridge for pairing."""
        if self._mode != "web" or self._http is None:
            return None
        try:
            resp = await self._http.get(
                f"http://127.0.0.1:{self._bridge_port}/qr"
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("qr")
        except Exception as e:
            logger.debug("QR fetch failed: %s", e)
        return None

    async def get_connection_status(self) -> dict:
        """Get connection status from the Baileys bridge."""
        if self._mode != "web" or self._http is None:
            return {"status": "n/a", "mode": self._mode}
        try:
            resp = await self._http.get(
                f"http://127.0.0.1:{self._bridge_port}/status"
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return {"status": "disconnected"}

    # --- Send messages ---

    async def send_message(self, message: OutgoingMessage) -> str:
        if self._mode == "web":
            return await self._send_via_bridge(message)
        return await self._send_via_cloud_api(message)

    async def _send_via_cloud_api(self, message: OutgoingMessage) -> str:
        """Send a message via Cloud API."""
        if not self._http:
            return ""
        url = (
            f"{GRAPH_API_BASE}/{self._api_version}"
            f"/{self._phone_number_id}/messages"
        )
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

        text = message.content.text if message.content else ""
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": message.chat_id,
        }

        if message.content and message.content.images:
            img = message.content.images[0]
            if img.url:
                payload["type"] = "image"
                payload["image"] = {"link": img.url}
                if text:
                    payload["image"]["caption"] = text
            else:
                payload["type"] = "text"
                payload["text"] = {"body": text or "(image)"}
        elif message.content and message.content.files:
            f = message.content.files[0]
            if f.url:
                payload["type"] = "document"
                payload["document"] = {"link": f.url, "filename": f.filename}
                if text:
                    payload["document"]["caption"] = text
            else:
                payload["type"] = "text"
                payload["text"] = {"body": text or "(file)"}
        elif message.content and message.content.voices:
            v = message.content.voices[0]
            if v.url:
                payload["type"] = "audio"
                payload["audio"] = {"link": v.url}
            else:
                payload["type"] = "text"
                payload["text"] = {"body": text or "(voice)"}
        else:
            payload["type"] = "text"
            payload["text"] = {"body": text}

        if message.reply_to:
            payload["context"] = {"message_id": message.reply_to}

        try:
            resp = await self._http.post(url, json=payload, headers=headers)
            data = resp.json()
            msg_id = ""
            msgs = data.get("messages", [])
            if msgs:
                msg_id = msgs[0].get("id", "")
            return msg_id
        except Exception as e:
            logger.error("Cloud API send failed: %s", e)
            return ""

    async def _send_via_bridge(self, message: OutgoingMessage) -> str:
        """Send a message via the Baileys bridge HTTP API."""
        if not self._http:
            return ""
        text = message.content.text if message.content else ""
        payload = {
            "chat_id": message.chat_id,
            "text": text,
        }
        if message.reply_to:
            payload["reply_to"] = message.reply_to

        if message.content and message.content.images:
            img = message.content.images[0]
            if img.url:
                payload["media_url"] = img.url
                payload["media_type"] = "image"

        try:
            resp = await self._http.post(
                f"http://127.0.0.1:{self._bridge_port}/send",
                json=payload,
            )
            data = resp.json()
            return data.get("message_id", "")
        except Exception as e:
            logger.error("Baileys bridge send failed: %s", e)
            return ""

    # --- Media ---

    async def download_media(self, media: MediaFile) -> Path:
        """Download media from Cloud API or bridge."""
        if not self._http:
            return Path(tempfile.gettempdir()) / f"wa-{media.id}.bin"

        if self._mode == "cloud_api" and media.file_id:
            try:
                url_resp = await self._http.get(
                    f"{GRAPH_API_BASE}/{self._api_version}/{media.file_id}",
                    headers={"Authorization": f"Bearer {self._access_token}"},
                )
                media_url = url_resp.json().get("url", "")
                if media_url:
                    dl_resp = await self._http.get(
                        media_url,
                        headers={"Authorization": f"Bearer {self._access_token}"},
                    )
                    tmp = Path(tempfile.gettempdir()) / f"wa-{media.id}-{media.filename}"
                    tmp.write_bytes(dl_resp.content)
                    return tmp
            except Exception as e:
                logger.error("Media download failed: %s", e)

        return Path(tempfile.gettempdir()) / f"wa-{media.id}.bin"

    async def upload_media(self, path: Path, mime_type: str) -> MediaFile:
        if not self._http or self._mode != "cloud_api":
            return MediaFile.create(path.name, mime_type)

        url = (
            f"{GRAPH_API_BASE}/{self._api_version}"
            f"/{self._phone_number_id}/media"
        )
        try:
            with open(path, "rb") as f:
                resp = await self._http.post(
                    url,
                    headers={"Authorization": f"Bearer {self._access_token}"},
                    data={"messaging_product": "whatsapp"},
                    files={"file": (path.name, f, mime_type)},
                )
            data = resp.json()
            return MediaFile(
                id=data.get("id", ""),
                filename=path.name,
                mime_type=mime_type,
                file_id=data.get("id"),
                status=MediaStatus.READY,
            )
        except Exception as e:
            logger.error("Media upload failed: %s", e)
            return MediaFile.create(path.name, mime_type)

    async def delete_message(self, chat_id: str, message_id: str) -> bool:
        """Delete a message (Cloud API only — not supported in Web mode)."""
        if self._mode != "cloud_api" or not self._http:
            return False
        # Cloud API doesn't support message deletion for most message types.
        return False

    # --- Streaming ---

    def is_streaming_enabled(self, is_group: bool = False) -> bool:
        return True

    async def stream_token(
        self,
        chat_id: str,
        token: str,
        *,
        thread_id: str | None = None,
        is_group: bool = False,
    ) -> None:
        """Accumulate streaming tokens (WhatsApp doesn't support real-time edits)."""
        sk = self._make_session_key(chat_id, thread_id)
        self._streaming_buffers[sk] = self._streaming_buffers.get(sk, "") + token

    async def finalize_stream(
        self,
        chat_id: str,
        final_text: str,
        *,
        thread_id: str | None = None,
    ) -> bool:
        """Finalize stream by sending accumulated text as a single message."""
        sk = self._make_session_key(chat_id, thread_id)
        self._streaming_buffers.pop(sk, None)

        if final_text.strip():
            msg = OutgoingMessage.text(chat_id, final_text, thread_id=thread_id)
            await self.send_message(msg)
            return True
        return False

    @staticmethod
    def _make_session_key(chat_id: str, thread_id: str | None = None) -> str:
        return f"{chat_id}:{thread_id}" if thread_id else chat_id

    # --- Typing ---

    async def send_typing(self, chat_id: str, thread_id: str | None = None) -> None:
        """Send typing indicator (Cloud API only)."""
        if self._mode != "cloud_api" or not self._http:
            return
        url = (
            f"{GRAPH_API_BASE}/{self._api_version}"
            f"/{self._phone_number_id}/messages"
        )
        try:
            await self._http.post(
                url,
                json={
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": chat_id,
                    "type": "reaction",
                    "status": "typing",
                },
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": "application/json",
                },
            )
        except Exception:
            pass

    async def clear_typing(self, chat_id: str, thread_id: str | None = None) -> None:
        pass

    # --- Onboard (QR) ---

    async def onboard_start(self) -> dict:
        """Start the onboarding flow. For web mode, return QR data."""
        if self._mode != "web":
            return {"type": "credentials", "status": "ready"}

        qr = await self.get_qr_code()
        if qr:
            return {
                "type": "qr",
                "status": "scanning",
                "qr_data": qr,
                "expires_in": 120,
            }
        return {"type": "qr", "status": "waiting", "qr_data": ""}

    async def onboard_poll(self) -> dict:
        """Poll connection status during onboarding."""
        if self._mode != "web":
            return {"status": "success"}

        status = await self.get_connection_status()
        conn = status.get("status", "disconnected")
        if conn == "connected":
            return {"status": "success"}
        if conn == "qr_expired":
            qr = await self.get_qr_code()
            return {
                "status": "expired",
                "qr_data": qr or "",
            }

        qr = await self.get_qr_code()
        return {
            "status": "waiting",
            "qr_data": qr or "",
        }


# --- Adapter factory ---

def _whatsapp_factory(
    creds: dict,
    *,
    channel_name: str,
    bot_id: str,
    agent_profile_id: str,
) -> WhatsAppAdapter:
    return WhatsAppAdapter(
        creds,
        channel_name=channel_name,
        bot_id=bot_id,
        agent_profile_id=agent_profile_id,
    )


# --- Plugin ---

class Plugin(PluginBase):
    def __init__(self) -> None:
        self._api: PluginAPI | None = None

    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        api.register_channel("whatsapp", _whatsapp_factory)

        # Register onboard API routes if the plugin has route permission
        try:
            self._register_onboard_routes(api)
        except Exception as e:
            api.log(f"Onboard routes not registered: {e}", "debug")

        api.log("whatsapp-channel v1.0.0 loaded (dual-mode: cloud_api + web)")

    def _register_onboard_routes(self, api: PluginAPI) -> None:
        """Register onboard endpoints for QR-based pairing."""
        from fastapi import APIRouter, Request
        from fastapi.responses import JSONResponse

        router = APIRouter(prefix="/whatsapp-channel")

        @router.post("/onboard/start")
        async def onboard_start(request: Request):
            adapter = self._find_adapter()
            if adapter is None:
                return JSONResponse(
                    {"error": "WhatsApp adapter not active"},
                    status_code=404,
                )
            result = await adapter.onboard_start()
            return JSONResponse(result)

        @router.post("/onboard/poll")
        async def onboard_poll(request: Request):
            adapter = self._find_adapter()
            if adapter is None:
                return JSONResponse(
                    {"error": "WhatsApp adapter not active"},
                    status_code=404,
                )
            result = await adapter.onboard_poll()
            return JSONResponse(result)

        api.register_api_routes(router)

    def _find_adapter(self) -> WhatsAppAdapter | None:
        """Find the running WhatsApp adapter instance from the gateway."""
        if self._api is None:
            return None
        gateway = self._api._host_refs.get("gateway") if hasattr(self._api, "_host_refs") else None
        if gateway is None:
            return None
        for adapter in getattr(gateway, "_adapters", {}).values():
            if isinstance(adapter, WhatsAppAdapter):
                return adapter
        return None

    def on_unload(self) -> None:
        self._api = None

