"""
IM channel handler

Handles system skills related to IM channels:
- deliver_artifacts: deliver attachments via the gateway and return receipts (recommended)
- get_voice_file: retrieve voice file
- get_image_file: retrieve image file
- get_chat_history: retrieve chat history

Generic design:
- Sends messages via gateway/adapter, without depending on the Session class's send methods
- Each adapter implements a unified interface; adding a new IM platform only requires implementing the ChannelAdapter base class
- For features not supported by a platform (e.g. some platforms don't support voice), returns a friendly message
"""

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ...channels.base import ChannelAdapter
    from ...core.agent import Agent

logger = logging.getLogger(__name__)

_CHANNEL_ALIASES: dict[str, list[str]] = {
    "wework": ["wework_ws"],
    "wework_ws": ["wework"],
}


class IMChannelHandler:
    """
    IM channel handler

    Uses the gateway to obtain the appropriate adapter for sending messages, remaining generic.
    Each IM platform's adapter must implement the following ChannelAdapter base class methods:
    - send_text(chat_id, text): send text message
    - send_file(chat_id, file_path, caption): send file
    - send_image(chat_id, image_path, caption): send image (optional)
    - send_voice(chat_id, voice_path, caption): send voice (optional)
    """

    TOOLS = [
        "deliver_artifacts",
        "get_voice_file",
        "get_image_file",
        "get_chat_history",
        "get_chat_info",
        "get_user_info",
        "get_chat_members",
        "get_recent_messages",
    ]

    def __init__(self, agent: "Agent"):
        self.agent = agent

    def _get_workspace_root(self) -> Path | None:
        ws = getattr(self.agent, "workspace_dir", None) or getattr(
            self.agent, "_workspace_dir", None
        )
        return Path(ws).resolve() if ws else None

    @staticmethod
    def _is_relative_to(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    @staticmethod
    def _normalize_artifacts(raw: Any) -> list[dict]:
        """Normalize ``artifacts`` param: handle str (JSON), list of dicts, etc.

        Some LLM models pass artifacts as a JSON string instead of a list.
        This helper ensures we always get ``list[dict]``.
        """
        if isinstance(raw, str):
            raw = raw.strip()
            if raw:
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        return [item for item in parsed if isinstance(item, dict)]
                    if isinstance(parsed, dict):
                        return [parsed]
                except (json.JSONDecodeError, TypeError):
                    pass
            logger.warning(
                "[deliver_artifacts] artifacts is a string but not valid JSON, ignoring: %s",
                raw[:200],
            )
            return []
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
        if isinstance(raw, dict):
            return [raw]
        return []

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        """Handle a tool call"""
        from ...core.im_context import get_im_session

        # deliver_artifacts supports cross-channel sending (via the target_channel parameter)
        if tool_name == "deliver_artifacts":
            target_channel = (params.get("target_channel") or "").strip()
            if target_channel:
                prefer_chat_type = (params.get("prefer_chat_type") or "private").strip()
                return await self._deliver_artifacts_cross_channel(
                    params, target_channel, prefer_chat_type=prefer_chat_type
                )
            if not get_im_session():
                return await self._deliver_artifacts_desktop(params)
            return await self._deliver_artifacts(params)

        # get_chat_history is also available in Desktop mode (reads from session)
        if tool_name == "get_chat_history":
            if get_im_session():
                return await self._get_chat_history(params)
            return self._get_chat_history_desktop(params)

        if not get_im_session():
            return "❌ Not currently in an IM session; this tool cannot be used"

        if tool_name == "get_voice_file":
            return self._get_voice_file(params)
        elif tool_name == "get_image_file":
            return self._get_image_file(params)
        elif tool_name in (
            "get_chat_info",
            "get_user_info",
            "get_chat_members",
            "get_recent_messages",
        ):
            return await self._handle_im_query_tool(tool_name, params)
        else:
            return f"❌ Unknown IM channel tool: {tool_name}"

    def _get_adapter_and_chat_id(
        self,
    ) -> tuple[Optional["ChannelAdapter"], str | None, str | None, str | None, str | None]:
        """
        Get the adapter and chat_id for the current IM session.

        Returns:
            (adapter, chat_id, channel_name, reply_to, channel_user_id)
            or (None, None, None, None, None) on failure
        """
        from ...core.im_context import get_im_session

        session = get_im_session()
        if not session:
            return None, None, None, None, None

        # Get gateway and current message from session metadata
        gateway = session.get_metadata("_gateway")
        current_message = session.get_metadata("_current_message")

        if not gateway or not current_message:
            logger.warning("Missing gateway or current_message in session metadata")
            return None, None, None, None, None

        # Get the corresponding adapter
        channel = current_message.channel
        # Avoid accessing private attributes: prefer the public interface
        adapter = gateway.get_adapter(channel) if hasattr(gateway, "get_adapter") else None
        if adapter is None:
            adapter = getattr(gateway, "_adapters", {}).get(channel)

        if not adapter:
            logger.warning(f"Adapter not found for channel: {channel}")
            return None, None, channel, None, None

        # Extract reply_to (channel_message_id) and channel_user_id (precise routing in group chats)
        reply_to = getattr(current_message, "channel_message_id", None)
        channel_user_id = getattr(current_message, "channel_user_id", None)

        return adapter, current_message.chat_id, channel, reply_to, channel_user_id

    # ==================== Cross-channel helper methods ====================

    def _get_gateway(self):
        """
        Get the MessageGateway instance (without depending on IM session context).

        Lookup order:
        1. agent._task_executor.gateway (set for the global agent via set_scheduler_gateway)
        2. IM context (set by gateway.py during IM session handling)
        3. Global main._message_gateway (Desktop cross-channel fallback)
        """
        executor = getattr(self.agent, "_task_executor", None)
        if executor and getattr(executor, "gateway", None):
            return executor.gateway

        from ...core.im_context import get_im_gateway

        gw = get_im_gateway()
        if gw:
            return gw

        try:
            from openakita import main as _main_mod

            return getattr(_main_mod, "_message_gateway", None)
        except Exception:
            return None

    def _resolve_target_channel(
        self, target_channel: str, *, prefer_chat_type: str = "private"
    ) -> tuple[Optional["ChannelAdapter"], str | None]:
        """
        Resolve a target_channel name to (adapter, chat_id).

        Strategy (tiered fallback):
        1. Check whether the gateway has an adapter for this channel and that it is running
        2. Find the most recently active session for this channel in session_manager (preferring prefer_chat_type)
        3. Look up the persisted sessions.json file (preferring prefer_chat_type)
        4. Look up history in the channel registry channel_registry.json

        Returns:
            (adapter, chat_id) or (None, None)
        """
        from datetime import datetime

        gateway = self._get_gateway()
        if not gateway:
            logger.warning("[CrossChannel] No gateway available")
            return None, None

        # 1. Resolve candidate adapters (supports prefix matching + alias fallback, e.g. "wework" -> "wework_ws:bot-id")
        adapters = getattr(gateway, "_adapters", {})
        if target_channel in adapters:
            candidates = [target_channel]
        else:
            prefixes = [target_channel + ":"]
            for alias in _CHANNEL_ALIASES.get(target_channel, []):
                prefixes.append(alias + ":")
            candidates = [
                k
                for k in adapters
                if any(k.startswith(p) for p in prefixes)
                and getattr(adapters[k], "is_running", False)
            ]
        if not candidates:
            logger.warning(
                f"[CrossChannel] Channel '{target_channel}' not found in adapters: "
                f"{list(adapters.keys())}"
            )
            return None, None

        def _chat_type_sort_key(s_chat_type: str, last_active_ts: float) -> tuple:
            """(chat_type mismatches sort last, newer sorts first)"""
            return (s_chat_type != prefer_chat_type, -last_active_ts)

        adapter: ChannelAdapter | None = None
        chat_id: str | None = None

        # 2. Collect in-memory sessions across all candidate adapters, then globally sort and pick the best
        session_manager = getattr(gateway, "session_manager", None)
        if session_manager:
            all_sessions: list[tuple[str, Any, Any]] = []
            for cand in candidates:
                for s in session_manager.list_sessions(channel=cand):
                    all_sessions.append((cand, adapters[cand], s))
            if all_sessions:
                all_sessions.sort(
                    key=lambda x: _chat_type_sort_key(
                        x[2].metadata.get("chat_type", ""),
                        getattr(x[2], "last_active", datetime.min).timestamp(),
                    ),
                )
                chosen_channel, adapter, chosen = all_sessions[0]
                chat_id = chosen.chat_id
                chosen_type = chosen.metadata.get("chat_type", "unknown")
                if chosen_type != prefer_chat_type:
                    logger.info(
                        f"[CrossChannel] No {prefer_chat_type} session across "
                        f"{len(candidates)} candidate(s), falling back to "
                        f"{chosen_type} on '{chosen_channel}' chat_id={chat_id}"
                    )
                else:
                    logger.info(
                        f"[CrossChannel] Selected {chosen_type} session on "
                        f"'{chosen_channel}': chat_id={chat_id}"
                    )

        # 3. Look up the persisted file (across all candidate adapters, preferring prefer_chat_type)
        if not chat_id and session_manager:
            import json as _json

            sessions_file = getattr(session_manager, "storage_path", None)
            if sessions_file:
                sessions_file = sessions_file / "sessions.json"
                if sessions_file.exists():
                    try:
                        with open(sessions_file, encoding="utf-8") as f:
                            raw = _json.load(f)
                        cand_set = set(candidates)
                        ch_sessions = [
                            s for s in raw if s.get("channel") in cand_set and s.get("chat_id")
                        ]
                        if ch_sessions:
                            ch_sessions.sort(
                                key=lambda s: _chat_type_sort_key(
                                    (s.get("metadata") or {}).get("chat_type", ""),
                                    0,
                                ),
                            )
                            best = ch_sessions[0]
                            chat_id = best["chat_id"]
                            adapter = adapters.get(best["channel"])
                    except Exception as e:
                        logger.error(f"[CrossChannel] Failed to read sessions file: {e}")

        # 4. Look up the channel registry (try each candidate adapter)
        if not chat_id and session_manager and hasattr(session_manager, "get_known_channel_target"):
            for cand in candidates:
                known = session_manager.get_known_channel_target(cand)
                if known:
                    chat_id = known[1]
                    adapter = adapters.get(cand)
                    logger.info(
                        f"[CrossChannel] Resolved '{cand}' from channel registry: chat_id={chat_id}"
                    )
                    break

        if not adapter or not chat_id:
            logger.warning(
                f"[CrossChannel] Channel '{target_channel}' has {len(candidates)} adapter(s) "
                f"but no chat_id found. Send at least one message through this channel first."
            )
            return None, None

        return adapter, chat_id

    async def _deliver_artifacts_cross_channel(
        self, params: dict, target_channel: str, *, prefer_chat_type: str = "private"
    ) -> str:
        """
        Cross-channel attachment delivery: resolve target_channel to adapter+chat_id,
        then reuse _send_file / _send_image / _send_voice to send.
        """
        import hashlib
        import json
        import re

        adapter, chat_id = self._resolve_target_channel(
            target_channel, prefer_chat_type=prefer_chat_type
        )
        if not adapter or not chat_id:
            return json.dumps(
                {
                    "ok": False,
                    "error": f"channel_resolve_failed:{target_channel}",
                    "error_code": "channel_resolve_failed",
                    "hint": (
                        f"Unable to resolve channel '{target_channel}'. "
                        "Please confirm the channel is configured, its adapter is running, "
                        "and that at least one session has occurred."
                    ),
                    "receipts": [],
                },
                ensure_ascii=False,
            )

        artifacts = self._normalize_artifacts(params.get("artifacts"))
        receipts = []

        for idx, art in enumerate(artifacts):
            art_type = (art or {}).get("type", "")
            path = (art or {}).get("path", "")
            caption = (art or {}).get("caption", "") or ""
            name = (art or {}).get("name", "") or ""

            size = None
            sha256 = None
            try:
                p = Path(path)
                if p.exists() and p.is_file():
                    size = p.stat().st_size
                    h = hashlib.sha256()
                    with p.open("rb") as f:
                        for chunk in iter(lambda: f.read(1024 * 1024), b""):
                            h.update(chunk)
                    sha256 = h.hexdigest()
            except Exception:
                pass

            receipt: dict[str, Any] = {
                "index": idx,
                "type": art_type,
                "path": path,
                "status": "failed",
                "error_code": "",
                "name": name,
                "size": size,
                "sha256": sha256,
                "channel": target_channel,
            }

            try:
                if not art_type or not path:
                    receipt["error"] = "missing_type_or_path"
                    receipt["error_code"] = "missing_type_or_path"
                elif art_type == "voice":
                    msg = await self._send_voice(adapter, chat_id, path, caption, target_channel)
                    receipt["status"] = "delivered" if msg.startswith("✅") else "failed"
                    receipt["message"] = msg
                    m = re.search(r"message_id=([^)]+)\)", msg)
                    if m:
                        receipt["message_id"] = m.group(1)
                    if receipt["status"] != "delivered":
                        receipt["error_code"] = "send_failed"
                elif art_type == "image":
                    msg = await self._send_image(
                        adapter,
                        chat_id,
                        path,
                        caption,
                        target_channel,
                    )
                    receipt["status"] = "delivered" if msg.startswith("✅") else "failed"
                    receipt["message"] = msg
                    m = re.search(r"message_id=([^)]+)\)", msg)
                    if m:
                        receipt["message_id"] = m.group(1)
                    if receipt["status"] != "delivered":
                        receipt["error_code"] = "send_failed"
                elif art_type == "file":
                    msg = await self._send_file(adapter, chat_id, path, caption, target_channel)
                    receipt["status"] = "delivered" if msg.startswith("✅") else "failed"
                    receipt["message"] = msg
                    m = re.search(r"message_id=([^)]+)\)", msg)
                    if m:
                        receipt["message_id"] = m.group(1)
                    if receipt["status"] != "delivered":
                        receipt["error_code"] = "send_failed"
                else:
                    receipt["error"] = f"unsupported_type:{art_type}"
                    receipt["error_code"] = "unsupported_type"
            except Exception as e:
                receipt["error"] = str(e)
                receipt["error_code"] = "exception"
                logger.error(f"[CrossChannel] Failed to send artifact to {target_channel}: {e}")

            receipts.append(receipt)

        ok = (
            all(r.get("status") in ("delivered", "skipped") for r in receipts)
            if receipts
            else False
        )
        logger.info(
            f"[CrossChannel] deliver_artifacts to {target_channel}: "
            f"{sum(1 for r in receipts if r.get('status') == 'delivered')}/{len(receipts)} delivered"
        )
        return json.dumps(
            {"ok": ok, "channel": target_channel, "receipts": receipts},
            ensure_ascii=False,
            indent=2,
        )

    async def _deliver_artifacts_desktop(self, params: dict) -> str:
        """
        Desktop mode: instead of sending via IM adapter, return file URLs
        so the desktop frontend can display them inline.
        """
        import json
        import shutil
        import urllib.parse

        artifacts = self._normalize_artifacts(params.get("artifacts"))
        receipts = []

        workspace_root = self._get_workspace_root()
        home_dir = Path.home().resolve()

        for idx, art in enumerate(artifacts):
            art_type = (art or {}).get("type", "")
            path_str = (art or {}).get("path", "")
            caption = (art or {}).get("caption", "") or ""
            name = (art or {}).get("name", "") or ""

            if not path_str:
                receipts.append(
                    {
                        "index": idx,
                        "status": "error",
                        "error": "missing_path",
                    }
                )
                continue

            p = Path(path_str)
            if not p.exists() or not p.is_file():
                receipts.append(
                    {
                        "index": idx,
                        "status": "error",
                        "error": f"file_not_found: {path_str}",
                    }
                )
                continue

            resolved = p.resolve()

            # The /api/files safety whitelist only allows files under the workspace and home directories.
            # If a file is outside the whitelist (e.g. D:\research\), copy it into the workspace first before serving,
            # otherwise the frontend's /api/files request will be blocked with 403.
            safe_roots = [workspace_root, home_dir] if workspace_root else [home_dir]
            if not any(self._is_relative_to(resolved, root) for root in safe_roots):
                try:
                    output_dir = (workspace_root or Path.cwd()) / "data" / "output"
                    output_dir.mkdir(parents=True, exist_ok=True)
                    dest = output_dir / resolved.name
                    if dest.exists() and dest.stat().st_size == resolved.stat().st_size:
                        pass  # same file already copied
                    else:
                        counter = 1
                        while dest.exists():
                            dest = output_dir / f"{resolved.stem}_{counter}{resolved.suffix}"
                            counter += 1
                        shutil.copy2(str(resolved), str(dest))
                    resolved = dest.resolve()
                    logger.info(f"[Desktop] Copied external file to workspace: {p} → {resolved}")
                except Exception as e:
                    logger.warning(f"[Desktop] Failed to copy external file {p}: {e}")

            abs_path = str(resolved)
            file_url = f"/api/files?path={urllib.parse.quote(abs_path, safe='')}"
            size = resolved.stat().st_size

            receipts.append(
                {
                    "index": idx,
                    "status": "delivered",
                    "type": art_type,
                    "path": abs_path,
                    "file_url": file_url,
                    "caption": caption,
                    "name": name or p.name,
                    "size": size,
                    "channel": "desktop",
                }
            )

        return json.dumps(
            {
                "ok": all(r.get("status") == "delivered" for r in receipts),
                "channel": "desktop",
                "receipts": receipts,
                "hint": "Desktop mode: files are served via /api/files/ endpoint. "
                "Frontend should display images inline using the file_url.",
            },
            ensure_ascii=False,
            indent=2,
        )

    async def _deliver_artifacts(self, params: dict) -> str:
        """
        Unified delivery entry point: deliver attachments via an explicit manifest and return a JSON receipt.
        """
        import hashlib
        import json
        import re

        adapter, chat_id, channel, reply_to, channel_user_id = self._get_adapter_and_chat_id()
        if not adapter:
            if channel:
                return json.dumps(
                    {
                        "ok": False,
                        "error": f"adapter_not_found:{channel}",
                        "error_code": "adapter_not_found",
                        "receipts": [],
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "ok": False,
                    "error": "missing_gateway_or_message_context",
                    "error_code": "missing_context",
                    "receipts": [],
                },
                ensure_ascii=False,
            )

        artifacts = self._normalize_artifacts(params.get("artifacts"))
        receipts = []

        # In-session deduplication (runtime-only, not persisted)
        session = getattr(self.agent, "_current_session", None)
        dedupe_set: set[str] = set()
        try:
            if session and hasattr(session, "get_metadata"):
                dedupe_set = set(session.get_metadata("_delivered_dedupe_keys") or [])
        except Exception:
            dedupe_set = set()

        for idx, art in enumerate(artifacts):
            art_type = (art or {}).get("type", "")
            path = (art or {}).get("path", "")
            caption = (art or {}).get("caption", "") or ""
            dedupe_key = (art or {}).get("dedupe_key", "") or ""
            mime = (art or {}).get("mime", "") or ""
            name = (art or {}).get("name", "") or ""

            size = None
            sha256 = None
            try:
                p = Path(path)
                if p.exists() and p.is_file():
                    size = p.stat().st_size
                    h = hashlib.sha256()
                    with p.open("rb") as f:
                        for chunk in iter(lambda: f.read(1024 * 1024), b""):
                            h.update(chunk)
                    sha256 = h.hexdigest()
            except Exception:
                pass

            if not dedupe_key and sha256:
                dedupe_key = f"content:{sha256}"
            elif not dedupe_key and path:
                dedupe_key = (
                    f"path:{hashlib.sha1(path.encode('utf-8', errors='ignore')).hexdigest()[:12]}"
                )
            receipt = {
                "index": idx,
                "type": art_type,
                "path": path,
                "status": "failed",
                "error_code": "",
                "name": name,
                "mime": mime,
                "size": size,
                "sha256": sha256,
                "dedupe_key": dedupe_key,
            }
            try:
                if not art_type or not path:
                    receipt["error"] = "missing_type_or_path"
                    receipt["error_code"] = "missing_type_or_path"
                elif dedupe_key and dedupe_key in dedupe_set:
                    receipt["status"] = "skipped"
                    receipt["error"] = "deduped"
                    receipt["error_code"] = "deduped"
                elif art_type == "voice":
                    msg = await self._send_voice(adapter, chat_id, path, caption, channel)
                    receipt["status"] = "delivered" if msg.startswith("✅") else "failed"
                    receipt["message"] = msg
                    m = re.search(r"message_id=([^)]+)\)", msg)
                    if m:
                        receipt["message_id"] = m.group(1)
                    if receipt["status"] != "delivered":
                        receipt["error_code"] = "send_failed"
                elif art_type == "image":
                    msg = await self._send_image(
                        adapter,
                        chat_id,
                        path,
                        caption,
                        channel,
                        reply_to=reply_to,
                        channel_user_id=channel_user_id,
                    )
                    receipt["status"] = "delivered" if msg.startswith("✅") else "failed"
                    receipt["message"] = msg
                    m = re.search(r"message_id=([^)]+)\)", msg)
                    if m:
                        receipt["message_id"] = m.group(1)
                    if receipt["status"] != "delivered":
                        receipt["error_code"] = "send_failed"
                elif art_type == "file":
                    msg = await self._send_file(adapter, chat_id, path, caption, channel)
                    receipt["status"] = "delivered" if msg.startswith("✅") else "failed"
                    receipt["message"] = msg
                    m = re.search(r"message_id=([^)]+)\)", msg)
                    if m:
                        receipt["message_id"] = m.group(1)
                    if receipt["status"] != "delivered":
                        receipt["error_code"] = "send_failed"
                else:
                    receipt["error"] = f"unsupported_type:{art_type}"
                    receipt["error_code"] = "unsupported_type"
            except Exception as e:
                receipt["error"] = str(e)
                receipt["error_code"] = "exception"
            receipts.append(receipt)

            if receipt.get("status") == "delivered" and dedupe_key:
                dedupe_set.add(dedupe_key)

        # Save back to session metadata (leading underscore: runtime-only, not persisted)
        try:
            if session and hasattr(session, "set_metadata"):
                session.set_metadata("_delivered_dedupe_keys", list(dedupe_set))
        except Exception:
            pass

        ok = (
            all(r.get("status") in ("delivered", "skipped") for r in receipts)
            if receipts
            else False
        )
        result_json = json.dumps({"ok": ok, "receipts": receipts}, ensure_ascii=False, indent=2)

        # Progress events are emitted uniformly by the gateway (throttled/coalesced)
        try:
            session = getattr(self.agent, "_current_session", None)
            gateway = (
                session.get_metadata("_gateway")
                if session and hasattr(session, "get_metadata")
                else None
            )
            if gateway and hasattr(gateway, "emit_progress_event"):
                delivered = sum(1 for r in receipts if r.get("status") == "delivered")
                total = len(receipts)
                await gateway.emit_progress_event(
                    session, f"📦 Attachment delivery receipt: {delivered}/{total} delivered"
                )
        except Exception as e:
            logger.warning(f"Failed to emit deliver progress: {e}")

        return result_json

    def _is_image_file(self, file_path: str) -> bool:
        """Check whether the file is an image"""
        image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
        return Path(file_path).suffix.lower() in image_extensions

    async def _send_text(
        self, adapter: "ChannelAdapter", chat_id: str, text: str, channel: str
    ) -> str:
        """Send a text message"""
        message_id = await adapter.send_text(chat_id, text)
        logger.info(f"[IM] Sent text to {channel}:{chat_id}")
        return f"✅ Message sent (message_id={message_id})"

    async def _send_file(
        self, adapter: "ChannelAdapter", chat_id: str, file_path: str, caption: str, channel: str
    ) -> str:
        """Send a file"""
        if not Path(file_path).exists():
            return f"❌ File not found: {file_path}"

        send_kwargs: dict = {}
        from ...core.im_context import get_im_session

        im_session = get_im_session()
        if im_session:
            current_msg = im_session.get_metadata("_current_message")
            if current_msg:
                req_id = getattr(current_msg, "metadata", {}).get("req_id")
                if req_id:
                    send_kwargs["metadata"] = {"req_id": req_id}
        try:
            message_id = await adapter.send_file(chat_id, file_path, caption, **send_kwargs)
            logger.info(f"[IM] Sent file to {channel}:{chat_id}: {file_path}")
            return f"✅ File sent: {file_path} (message_id={message_id})"
        except NotImplementedError as e:
            reason = str(e)
            return f"❌ {reason}" if reason else f"❌ Current platform ({channel}) does not support sending files"

    async def _send_image(
        self,
        adapter: "ChannelAdapter",
        chat_id: str,
        image_path: str,
        caption: str,
        channel: str,
        reply_to: str | None = None,
        channel_user_id: str | None = None,
    ) -> str:
        """Send an image"""
        # Check if the file exists
        if not Path(image_path).exists():
            return f"❌ Image not found: {image_path}"

        send_kwargs: dict = {"reply_to": reply_to}
        metadata: dict = {}
        if channel_user_id:
            metadata["channel_user_id"] = channel_user_id
        from ...core.im_context import get_im_session

        im_session = get_im_session()
        if im_session:
            current_msg = im_session.get_metadata("_current_message")
            if current_msg:
                req_id = getattr(current_msg, "metadata", {}).get("req_id")
                if req_id:
                    metadata["req_id"] = req_id
        if metadata:
            send_kwargs["metadata"] = metadata
        try:
            message_id = await adapter.send_image(
                chat_id,
                image_path,
                caption,
                **send_kwargs,
            )
            logger.info(f"[IM] Sent image to {channel}:{chat_id}: {image_path}")
            return f"✅ Image sent: {image_path} (message_id={message_id})"
        except NotImplementedError as e:
            _img_reason = str(e)
        except Exception as e:
            logger.warning(f"[IM] send_image failed for {channel}: {e}")
            _is_timeout = "timed out" in str(e).lower() or "timeout" in type(e).__name__.lower()
            if _is_timeout:
                return f"⚠️ Image send timed out (request submitted, may have succeeded): {image_path} (do not resend; tell the user to check back shortly)"
            _img_reason = ""

        # Fallback: send the image as a file (only taken for non-timeout errors)
        try:
            message_id = await adapter.send_file(chat_id, image_path, caption)
            logger.info(f"[IM] Sent image as file to {channel}:{chat_id}: {image_path}")
            return f"✅ Image sent (as file): {image_path} (message_id={message_id})"
        except NotImplementedError:
            pass
        except Exception as fallback_exc:
            logger.warning(f"[IM] send_file fallback also failed for {channel}: {fallback_exc}")

        if _img_reason:
            return f"❌ {_img_reason}"
        return f"❌ Current platform ({channel}) failed to send image; the reply window may have expired"

    async def _send_voice(
        self, adapter: "ChannelAdapter", chat_id: str, voice_path: str, caption: str, channel: str
    ) -> str:
        """Send a voice message"""
        # Check if the file exists
        if not Path(voice_path).exists():
            return f"❌ Voice file not found: {voice_path}"

        # Prefer send_voice; fall back to send_file on failure
        try:
            message_id = await adapter.send_voice(chat_id, voice_path, caption)
            logger.info(f"[IM] Sent voice to {channel}:{chat_id}: {voice_path}")
            return f"✅ Voice sent: {voice_path} (message_id={message_id})"
        except NotImplementedError as e:
            _voice_reason = str(e)

        # Fallback: send the voice as a file
        try:
            message_id = await adapter.send_file(chat_id, voice_path, caption)
            logger.info(f"[IM] Sent voice as file to {channel}:{chat_id}: {voice_path}")
            return f"✅ Voice sent (as file): {voice_path} (message_id={message_id})"
        except NotImplementedError:
            pass

        if _voice_reason:
            return f"❌ {_voice_reason}"
        return f"❌ Current platform ({channel}) does not support sending voice"

    def _get_voice_file(self, params: dict) -> str:
        """Get the voice file path"""
        from ...core.im_context import get_im_session

        session = get_im_session()

        # Prefer pending_voices (set when transcription fails)
        pending_voices = session.get_metadata("pending_voices")
        if pending_voices and len(pending_voices) > 0:
            voice = pending_voices[0]
            local_path = voice.get("local_path")
            if local_path and Path(local_path).exists():
                return f"Voice file path: {local_path}"

        # Fall back to pending_audio (also stores the raw audio path when transcription succeeds)
        pending_audio = session.get_metadata("pending_audio")
        if pending_audio and len(pending_audio) > 0:
            audio = pending_audio[0]
            local_path = audio.get("local_path")
            if local_path and Path(local_path).exists():
                transcription = audio.get("transcription")
                info = f"Voice file path: {local_path}"
                if transcription:
                    info += f"\nTranscription: {transcription}"
                return info

        return "❌ Current message has no voice file"

    def _get_image_file(self, params: dict) -> str:
        """Get the image file path"""
        from ...core.im_context import get_im_session

        session = get_im_session()

        # Get image info from session metadata
        pending_images = session.get_metadata("pending_images")
        if pending_images and len(pending_images) > 0:
            image = pending_images[0]
            local_path = image.get("local_path")
            if local_path and Path(local_path).exists():
                return f"Image file path: {local_path}"

        return "❌ Current message has no image file"

    def _fallback_history_from_sqlite(self, session, limit: int) -> str | None:
        """Fallback: load history from SQLite conversation_turns (for process-crash recovery)"""
        import logging
        import re

        _logger = logging.getLogger(__name__)

        mm = getattr(self.agent, "memory_manager", None)
        if not mm or not hasattr(mm, "store"):
            return None
        safe_id = ""
        if hasattr(session, "session_key"):
            safe_id = session.session_key.replace(":", "__")
        elif getattr(self.agent, "_current_conversation_id", None):
            safe_id = self.agent._current_conversation_id.replace(":", "__")
        if not safe_id:
            _logger.debug("[getChatHistory] fallback skipped: no safe_id resolved")
            return None
        safe_id = re.sub(r'[/\\+=%?*<>|"\x00-\x1f]', "_", safe_id)
        _logger.info(
            f"[getChatHistory] Session context empty, falling back to SQLite (safe_id={safe_id})"
        )
        db_turns = mm.store.get_recent_turns(safe_id, limit)
        if not db_turns:
            _logger.info(f"[getChatHistory] SQLite fallback: no turns found for {safe_id}")
            return None
        _logger.info(
            f"[getChatHistory] SQLite fallback: recovered {len(db_turns)} turns for {safe_id}"
        )
        MSG_LIMIT = 2000
        output = f"Last {len(db_turns)} messages (recovered from persistent storage):\n\n"
        for t in db_turns:
            role = t.get("role", "?")
            content = t.get("content", "") or ""
            if isinstance(content, str):
                if len(content) > MSG_LIMIT:
                    output += f"[{role}] {content[:MSG_LIMIT]}... [truncated, original length {len(content)} chars]\n"
                else:
                    output += f"[{role}] {content}\n"
            else:
                output += f"[{role}] [complex content]\n"
        return output

    def _get_chat_history_desktop(self, params: dict) -> str:
        """Read chat history from the current session in Desktop mode"""
        limit = params.get("limit", 20)
        session = getattr(self.agent, "_current_session", None)
        if not session:
            sid = getattr(self.agent, "_current_session_id", None)
            if sid:
                sm = getattr(self.agent, "_session_manager", None)
                if sm:
                    session = sm.get_session_by_id(sid)
        if not session:
            return "No active session; cannot retrieve chat history"

        messages = session.context.get_messages(limit=limit)
        if not messages or len(messages) <= 1:
            _reset_at = session.context.get_variable("_context_reset_at")
            if not _reset_at:
                fallback = self._fallback_history_from_sqlite(session, limit)
                if fallback:
                    return fallback
        if not messages:
            return "No chat history"

        MSG_LIMIT = 2000
        output = f"Last {len(messages)} messages:\n\n"
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, str):
                if len(content) > MSG_LIMIT:
                    output += f"[{role}] {content[:MSG_LIMIT]}... [truncated, original length {len(content)} chars]\n"
                else:
                    output += f"[{role}] {content}\n"
            else:
                output += f"[{role}] [complex content]\n"
        return output

    async def _get_chat_history(self, params: dict) -> str:
        """Get chat history"""
        from ...core.im_context import get_im_session

        session = get_im_session()
        limit = params.get("limit", 20)

        messages = session.context.get_messages(limit=limit)
        if not messages or len(messages) <= 1:
            _reset_at = session.context.get_variable("_context_reset_at")
            if not _reset_at:
                fallback = self._fallback_history_from_sqlite(session, limit)
                if fallback:
                    return fallback
        if not messages:
            return "No chat history"

        MSG_LIMIT = 2000
        output = f"Last {len(messages)} messages:\n\n"
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, str):
                if len(content) > MSG_LIMIT:
                    output += f"[{role}] {content[:MSG_LIMIT]}... [truncated, original length {len(content)} chars]\n"
                else:
                    output += f"[{role}] {content}\n"
            else:
                output += f"[{role}] [complex content]\n"

        return output

    async def _handle_im_query_tool(self, tool_name: str, params: dict) -> str:
        """Handle IM query tools (get_chat_info / get_user_info / get_chat_members / get_recent_messages)"""
        adapter, chat_id, channel, _, _ = self._get_adapter_and_chat_id()
        if not adapter:
            return "❌ Not currently in an IM session"

        from ...channels.base import ChannelAdapter

        try:
            if tool_name == "get_chat_info":
                if type(adapter).get_chat_info is ChannelAdapter.get_chat_info:
                    return f"⚠️ Current platform ({channel}) does not yet support retrieving chat info"
                result = await adapter.get_chat_info(chat_id)
                if not result:
                    return "Failed to retrieve chat info (may lack required permissions)"
                return json.dumps(result, ensure_ascii=False, indent=2)

            elif tool_name == "get_user_info":
                if type(adapter).get_user_info is ChannelAdapter.get_user_info:
                    return f"⚠️ Current platform ({channel}) does not yet support retrieving user info"
                user_id = params.get("user_id", "")
                if not user_id:
                    return "❌ Missing parameter user_id"
                result = await adapter.get_user_info(user_id)
                if not result:
                    return "Failed to retrieve user info (may lack required permissions)"
                return json.dumps(result, ensure_ascii=False, indent=2)

            elif tool_name == "get_chat_members":
                if type(adapter).get_chat_members is ChannelAdapter.get_chat_members:
                    return f"⚠️ Current platform ({channel}) does not yet support retrieving group member list"
                result = await adapter.get_chat_members(chat_id)
                if not result:
                    return "Failed to retrieve group member list (may lack required permissions)"
                return json.dumps(result, ensure_ascii=False, indent=2)

            elif tool_name == "get_recent_messages":
                if type(adapter).get_recent_messages is ChannelAdapter.get_recent_messages:
                    return f"⚠️ Current platform ({channel}) does not yet support retrieving recent messages"
                limit = params.get("limit", 20)
                result = await adapter.get_recent_messages(chat_id, limit=limit)
                if not result:
                    return "Failed to retrieve recent messages (may lack required permissions)"
                return json.dumps(result, ensure_ascii=False, indent=2)

            else:
                return f"❌ Unknown query tool: {tool_name}"

        except Exception as e:
            logger.error(f"[IM] Error in {tool_name}: {e}", exc_info=True)
            return f"❌ Call to {tool_name} failed: {e}"


def create_handler(agent: "Agent"):
    """Create an IM channel handler"""
    handler = IMChannelHandler(agent)
    return handler.handle
