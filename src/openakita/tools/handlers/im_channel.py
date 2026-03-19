"""
IM 通道处理器

处理 IM 通道相关的系统技能：
- deliver_artifacts: 通过网关交付附件并返回回执（推荐）
- get_voice_file: 获取语音文件
- get_image_file: 获取图片文件
- get_chat_history: 获取聊天历史

通用性设计：
- 通过 gateway/adapter 发送消息，不依赖 Session 类的发送方法
- 各 adapter 实现统一接口，新增 IM 平台只需实现 ChannelAdapter 基类
- 对于平台不支持的功能（如某些平台不支持语音），返回友好提示
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
    IM 通道处理器

    通过 gateway 获取对应的 adapter 来发送消息，保持通用性。
    各 IM 平台的 adapter 需要实现 ChannelAdapter 基类的方法：
    - send_text(chat_id, text): 发送文本消息
    - send_file(chat_id, file_path, caption): 发送文件
    - send_image(chat_id, image_path, caption): 发送图片（可选）
    - send_voice(chat_id, voice_path, caption): 发送语音（可选）
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
        ws = getattr(self.agent, "workspace_dir", None) or getattr(self.agent, "_workspace_dir", None)
        return Path(ws).resolve() if ws else None

    @staticmethod
    def _is_relative_to(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        """处理工具调用"""
        from ...core.im_context import get_im_session

        # deliver_artifacts 支持跨通道发送（target_channel 参数）
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

        # get_chat_history 在 Desktop 模式下也可用（从 session 读取）
        if tool_name == "get_chat_history":
            if get_im_session():
                return await self._get_chat_history(params)
            return self._get_chat_history_desktop(params)

        if not get_im_session():
            return "❌ 当前不在 IM 会话中，无法使用此工具"

        if tool_name == "get_voice_file":
            return self._get_voice_file(params)
        elif tool_name == "get_image_file":
            return self._get_image_file(params)
        elif tool_name in ("get_chat_info", "get_user_info", "get_chat_members", "get_recent_messages"):
            return await self._handle_im_query_tool(tool_name, params)
        else:
            return f"❌ Unknown IM channel tool: {tool_name}"

    def _get_adapter_and_chat_id(
        self,
    ) -> tuple[Optional["ChannelAdapter"], str | None, str | None, str | None, str | None]:
        """
        获取当前 IM 会话的 adapter 和 chat_id

        Returns:
            (adapter, chat_id, channel_name, reply_to, channel_user_id)
            或 (None, None, None, None, None) 如果获取失败
        """
        from ...core.im_context import get_im_session

        session = get_im_session()
        if not session:
            return None, None, None, None, None

        # 从 session metadata 获取 gateway 和当前消息
        gateway = session.get_metadata("_gateway")
        current_message = session.get_metadata("_current_message")

        if not gateway or not current_message:
            logger.warning("Missing gateway or current_message in session metadata")
            return None, None, None, None, None

        # 获取对应的 adapter
        channel = current_message.channel
        # 避免访问私有属性：优先使用公开接口
        adapter = gateway.get_adapter(channel) if hasattr(gateway, "get_adapter") else None
        if adapter is None:
            adapter = getattr(gateway, "_adapters", {}).get(channel)

        if not adapter:
            logger.warning(f"Adapter not found for channel: {channel}")
            return None, None, channel, None, None

        # 提取 reply_to (channel_message_id) 和 channel_user_id（群聊精确路由）
        reply_to = getattr(current_message, "channel_message_id", None)
        channel_user_id = getattr(current_message, "channel_user_id", None)

        return adapter, current_message.chat_id, channel, reply_to, channel_user_id

    # ==================== 跨通道辅助方法 ====================

    def _get_gateway(self):
        """
        获取 MessageGateway 实例（不依赖 IM session 上下文）。

        查找顺序：
        1. agent._task_executor.gateway（全局 agent 通过 set_scheduler_gateway 设置）
        2. IM 上下文（IM 会话处理期间由 gateway.py 设置）
        3. 全局 main._message_gateway（Desktop 跨通道 fallback）
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
        解析 target_channel 名称为 (adapter, chat_id)。

        策略（逐级回退）:
        1. 检查 gateway 中是否有该通道的适配器且正在运行
        2. 从 session_manager 中找到该通道最近活跃的 session（优先匹配 prefer_chat_type）
        3. 从持久化文件 sessions.json 中查找（优先匹配 prefer_chat_type）
        4. 从通道注册表 channel_registry.json 查找历史记录

        Returns:
            (adapter, chat_id) 或 (None, None)
        """
        from datetime import datetime

        gateway = self._get_gateway()
        if not gateway:
            logger.warning("[CrossChannel] No gateway available")
            return None, None

        # 1. 解析候选适配器（支持前缀匹配 + 别名回退，如 "wework" → "wework_ws:bot-id"）
        adapters = getattr(gateway, "_adapters", {})
        if target_channel in adapters:
            candidates = [target_channel]
        else:
            prefixes = [target_channel + ":"]
            for alias in _CHANNEL_ALIASES.get(target_channel, []):
                prefixes.append(alias + ":")
            candidates = [
                k for k in adapters
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
            """(chat_type 不匹配排后面, 越新越靠前)"""
            return (s_chat_type != prefer_chat_type, -last_active_ts)

        adapter: "ChannelAdapter | None" = None
        chat_id: str | None = None

        # 2. 跨所有候选适配器收集内存 session，全局排序选最优
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

        # 3. 从持久化文件查找（跨所有候选适配器，优先匹配 prefer_chat_type）
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
                            s for s in raw
                            if s.get("channel") in cand_set and s.get("chat_id")
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

        # 4. 从通道注册表查找（尝试每个候选适配器）
        if not chat_id and session_manager and hasattr(session_manager, "get_known_channel_target"):
            for cand in candidates:
                known = session_manager.get_known_channel_target(cand)
                if known:
                    chat_id = known[1]
                    adapter = adapters.get(cand)
                    logger.info(
                        f"[CrossChannel] Resolved '{cand}' from channel registry: "
                        f"chat_id={chat_id}"
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
        跨通道发送附件：解析 target_channel 获取 adapter+chat_id，
        然后复用 _send_file/_send_image/_send_voice 方法发送。
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
                        f"无法解析通道 '{target_channel}'。"
                        "请确认该通道已配置、适配器正在运行，且至少有过一次会话。"
                    ),
                    "receipts": [],
                },
                ensure_ascii=False,
            )

        artifacts = params.get("artifacts") or []
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
                        adapter, chat_id, path, caption, target_channel,
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

        artifacts = params.get("artifacts") or []
        receipts = []

        workspace_root = self._get_workspace_root()
        home_dir = Path.home().resolve()

        for idx, art in enumerate(artifacts):
            art_type = (art or {}).get("type", "")
            path_str = (art or {}).get("path", "")
            caption = (art or {}).get("caption", "") or ""
            name = (art or {}).get("name", "") or ""

            if not path_str:
                receipts.append({
                    "index": idx,
                    "status": "error",
                    "error": "missing_path",
                })
                continue

            p = Path(path_str)
            if not p.exists() or not p.is_file():
                receipts.append({
                    "index": idx,
                    "status": "error",
                    "error": f"file_not_found: {path_str}",
                })
                continue

            resolved = p.resolve()

            # /api/files 的安全白名单只允许 workspace 和 home 目录下的文件。
            # 如果文件在白名单外（如 D:\research\），先复制到工作区再提供服务，
            # 否则前端请求 /api/files 会被 403 拦截。
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

            receipts.append({
                "index": idx,
                "status": "delivered",
                "type": art_type,
                "path": abs_path,
                "file_url": file_url,
                "caption": caption,
                "name": name or p.name,
                "size": size,
                "channel": "desktop",
            })

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
        统一交付入口：显式 manifest 交付附件，并返回回执 JSON。
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

        artifacts = params.get("artifacts") or []
        receipts = []

        # 会话内去重（仅运行时有效，不落盘）
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
                dedupe_key = f"path:{hashlib.sha1(path.encode('utf-8', errors='ignore')).hexdigest()[:12]}"
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
                        adapter, chat_id, path, caption, channel,
                        reply_to=reply_to, channel_user_id=channel_user_id,
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

        # 保存回 session metadata（下划线开头：不落盘，仅运行时）
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

        # 进度事件由网关统一发送（节流/合并）
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
                    session, f"📦 附件交付回执：{delivered}/{total} delivered"
                )
        except Exception as e:
            logger.warning(f"Failed to emit deliver progress: {e}")

        return result_json

    def _is_image_file(self, file_path: str) -> bool:
        """检测文件是否是图片"""
        image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
        return Path(file_path).suffix.lower() in image_extensions

    async def _send_text(
        self, adapter: "ChannelAdapter", chat_id: str, text: str, channel: str
    ) -> str:
        """发送文本消息"""
        message_id = await adapter.send_text(chat_id, text)
        logger.info(f"[IM] Sent text to {channel}:{chat_id}")
        return f"✅ 已发送消息 (message_id={message_id})"

    async def _send_file(
        self, adapter: "ChannelAdapter", chat_id: str, file_path: str, caption: str, channel: str
    ) -> str:
        """发送文件"""
        if not Path(file_path).exists():
            return f"❌ 文件不存在: {file_path}"

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
            return f"✅ 已发送文件: {file_path} (message_id={message_id})"
        except NotImplementedError as e:
            reason = str(e)
            return f"❌ {reason}" if reason else f"❌ 当前平台 ({channel}) 不支持发送文件"

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
        """发送图片"""
        # 检查文件是否存在
        if not Path(image_path).exists():
            return f"❌ 图片不存在: {image_path}"

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
                chat_id, image_path, caption,
                **send_kwargs,
            )
            logger.info(f"[IM] Sent image to {channel}:{chat_id}: {image_path}")
            return f"✅ 已发送图片: {image_path} (message_id={message_id})"
        except NotImplementedError as e:
            _img_reason = str(e)
        except Exception as e:
            logger.warning(f"[IM] send_image failed for {channel}: {e}")
            _is_timeout = "timed out" in str(e).lower() or "timeout" in type(e).__name__.lower()
            if _is_timeout:
                return f"⚠️ 图片发送超时（可能已发送成功）: {image_path}"
            _img_reason = ""

        # 降级：以文件形式发送图片（仅非超时错误才走此路径）
        try:
            message_id = await adapter.send_file(chat_id, image_path, caption)
            logger.info(f"[IM] Sent image as file to {channel}:{chat_id}: {image_path}")
            return f"✅ 已发送图片(作为文件): {image_path} (message_id={message_id})"
        except NotImplementedError:
            pass

        if _img_reason:
            return f"❌ {_img_reason}"
        return f"❌ 当前平台 ({channel}) 不支持发送图片"

    async def _send_voice(
        self, adapter: "ChannelAdapter", chat_id: str, voice_path: str, caption: str, channel: str
    ) -> str:
        """发送语音"""
        # 检查文件是否存在
        if not Path(voice_path).exists():
            return f"❌ 语音文件不存在: {voice_path}"

        # 优先使用 send_voice，失败则降级到 send_file
        try:
            message_id = await adapter.send_voice(chat_id, voice_path, caption)
            logger.info(f"[IM] Sent voice to {channel}:{chat_id}: {voice_path}")
            return f"✅ 已发送语音: {voice_path} (message_id={message_id})"
        except NotImplementedError as e:
            _voice_reason = str(e)

        # 降级：以文件形式发送语音
        try:
            message_id = await adapter.send_file(chat_id, voice_path, caption)
            logger.info(f"[IM] Sent voice as file to {channel}:{chat_id}: {voice_path}")
            return f"✅ 已发送语音(作为文件): {voice_path} (message_id={message_id})"
        except NotImplementedError:
            pass

        if _voice_reason:
            return f"❌ {_voice_reason}"
        return f"❌ 当前平台 ({channel}) 不支持发送语音"

    def _get_voice_file(self, params: dict) -> str:
        """获取语音文件路径"""
        from ...core.im_context import get_im_session

        session = get_im_session()

        # 优先从 pending_voices 获取（转写失败时设置）
        pending_voices = session.get_metadata("pending_voices")
        if pending_voices and len(pending_voices) > 0:
            voice = pending_voices[0]
            local_path = voice.get("local_path")
            if local_path and Path(local_path).exists():
                return f"语音文件路径: {local_path}"

        # 兜底从 pending_audio 获取（转写成功时也会存储原始音频路径）
        pending_audio = session.get_metadata("pending_audio")
        if pending_audio and len(pending_audio) > 0:
            audio = pending_audio[0]
            local_path = audio.get("local_path")
            if local_path and Path(local_path).exists():
                transcription = audio.get("transcription")
                info = f"语音文件路径: {local_path}"
                if transcription:
                    info += f"\n已转写文字: {transcription}"
                return info

        return "❌ 当前消息没有语音文件"

    def _get_image_file(self, params: dict) -> str:
        """获取图片文件路径"""
        from ...core.im_context import get_im_session

        session = get_im_session()

        # 从 session metadata 获取图片信息
        pending_images = session.get_metadata("pending_images")
        if pending_images and len(pending_images) > 0:
            image = pending_images[0]
            local_path = image.get("local_path")
            if local_path and Path(local_path).exists():
                return f"图片文件路径: {local_path}"

        return "❌ 当前消息没有图片文件"

    def _fallback_history_from_sqlite(self, session, limit: int) -> str | None:
        """从 SQLite conversation_turns 兜底加载历史（进程崩溃恢复场景）"""
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
        _logger.info(f"[getChatHistory] Session context empty, falling back to SQLite (safe_id={safe_id})")
        db_turns = mm.store.get_recent_turns(safe_id, limit)
        if not db_turns:
            _logger.info(f"[getChatHistory] SQLite fallback: no turns found for {safe_id}")
            return None
        _logger.info(f"[getChatHistory] SQLite fallback: recovered {len(db_turns)} turns for {safe_id}")
        MSG_LIMIT = 2000
        output = f"最近 {len(db_turns)} 条消息（从持久化存储恢复）:\n\n"
        for t in db_turns:
            role = t.get("role", "?")
            content = t.get("content", "") or ""
            if isinstance(content, str):
                if len(content) > MSG_LIMIT:
                    output += f"[{role}] {content[:MSG_LIMIT]}... [已截断, 原文{len(content)}字]\n"
                else:
                    output += f"[{role}] {content}\n"
            else:
                output += f"[{role}] [复杂内容]\n"
        return output

    def _get_chat_history_desktop(self, params: dict) -> str:
        """Desktop 模式下从当前 session 读取聊天历史"""
        limit = params.get("limit", 20)
        session = getattr(self.agent, "_current_session", None)
        if not session:
            sid = getattr(self.agent, "_current_session_id", None)
            if sid:
                sm = getattr(self.agent, "_session_manager", None)
                if sm:
                    session = sm.get_session_by_id(sid)
        if not session:
            return "当前没有活跃的会话，无法获取聊天历史"

        messages = session.context.get_messages(limit=limit)
        if not messages or len(messages) <= 1:
            _reset_at = session.context.get_variable("_context_reset_at")
            if not _reset_at:
                fallback = self._fallback_history_from_sqlite(session, limit)
                if fallback:
                    return fallback
        if not messages:
            return "没有聊天历史"

        MSG_LIMIT = 2000
        output = f"最近 {len(messages)} 条消息:\n\n"
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, str):
                if len(content) > MSG_LIMIT:
                    output += f"[{role}] {content[:MSG_LIMIT]}... [已截断, 原文{len(content)}字]\n"
                else:
                    output += f"[{role}] {content}\n"
            else:
                output += f"[{role}] [复杂内容]\n"
        return output

    async def _get_chat_history(self, params: dict) -> str:
        """获取聊天历史"""
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
            return "没有聊天历史"

        MSG_LIMIT = 2000
        output = f"最近 {len(messages)} 条消息:\n\n"
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, str):
                if len(content) > MSG_LIMIT:
                    output += f"[{role}] {content[:MSG_LIMIT]}... [已截断, 原文{len(content)}字]\n"
                else:
                    output += f"[{role}] {content}\n"
            else:
                output += f"[{role}] [复杂内容]\n"

        return output

    async def _handle_im_query_tool(self, tool_name: str, params: dict) -> str:
        """处理 IM 查询类工具（get_chat_info / get_user_info / get_chat_members / get_recent_messages）"""
        adapter, chat_id, channel, _, _ = self._get_adapter_and_chat_id()
        if not adapter:
            return "❌ 当前不在 IM 会话中"

        from ...channels.base import ChannelAdapter

        try:
            if tool_name == "get_chat_info":
                if type(adapter).get_chat_info is ChannelAdapter.get_chat_info:
                    return f"⚠️ 当前平台 ({channel}) 暂不支持获取聊天信息"
                result = await adapter.get_chat_info(chat_id)
                if not result:
                    return "未能获取聊天信息（可能缺少相应权限）"
                return json.dumps(result, ensure_ascii=False, indent=2)

            elif tool_name == "get_user_info":
                if type(adapter).get_user_info is ChannelAdapter.get_user_info:
                    return f"⚠️ 当前平台 ({channel}) 暂不支持获取用户信息"
                user_id = params.get("user_id", "")
                if not user_id:
                    return "❌ 缺少参数 user_id"
                result = await adapter.get_user_info(user_id)
                if not result:
                    return "未能获取用户信息（可能缺少相应权限）"
                return json.dumps(result, ensure_ascii=False, indent=2)

            elif tool_name == "get_chat_members":
                if type(adapter).get_chat_members is ChannelAdapter.get_chat_members:
                    return f"⚠️ 当前平台 ({channel}) 暂不支持获取群成员列表"
                result = await adapter.get_chat_members(chat_id)
                if not result:
                    return "未能获取群成员列表（可能缺少相应权限）"
                return json.dumps(result, ensure_ascii=False, indent=2)

            elif tool_name == "get_recent_messages":
                if type(adapter).get_recent_messages is ChannelAdapter.get_recent_messages:
                    return f"⚠️ 当前平台 ({channel}) 暂不支持获取最近消息"
                limit = params.get("limit", 20)
                result = await adapter.get_recent_messages(chat_id, limit=limit)
                if not result:
                    return "未能获取最近消息（可能缺少相应权限）"
                return json.dumps(result, ensure_ascii=False, indent=2)

            else:
                return f"❌ Unknown query tool: {tool_name}"

        except Exception as e:
            logger.error(f"[IM] Error in {tool_name}: {e}", exc_info=True)
            return f"❌ 调用 {tool_name} 失败: {e}"


def create_handler(agent: "Agent"):
    """创建 IM 通道处理器"""
    handler = IMChannelHandler(agent)
    return handler.handle
