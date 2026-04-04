"""
会话管理器

职责:
- 根据 (channel, chat_id, user_id) 获取或创建会话
- 管理会话生命周期
- 隔离不同会话的上下文
- 会话持久化
"""

import asyncio
import contextlib
import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from openakita.utils.atomic_io import atomic_json_write

from .session import Session, SessionConfig, SessionState
from .user import UserManager

logger = logging.getLogger(__name__)


class SessionManager:
    """
    会话管理器

    管理所有活跃会话，提供:
    - 会话的创建和获取
    - 会话过期清理
    - 会话持久化
    """

    def __init__(
        self,
        storage_path: Path | None = None,
        default_config: SessionConfig | None = None,
        cleanup_interval_seconds: int = 300,  # 5 分钟清理一次
    ):
        """
        Args:
            storage_path: 会话存储目录
            default_config: 默认会话配置
            cleanup_interval_seconds: 清理间隔（秒）
        """
        self.storage_path = Path(storage_path) if storage_path else Path("data/sessions")
        self.storage_path.mkdir(parents=True, exist_ok=True)

        self.default_config = default_config or SessionConfig()
        self.cleanup_interval = cleanup_interval_seconds

        # 活跃会话缓存 {session_key: Session}
        self._sessions: dict[str, Session] = {}
        self._sessions_lock = threading.RLock()

        # 通道注册表：记录每个 IM 通道最后已知的 chat_id / user_id
        # 不受 session 过期清理影响，用于定时任务等场景回溯通道目标
        # 格式: {channel_name: {"chat_id": str, "user_id": str, "last_seen": str}}
        self._channel_registry: dict[str, dict[str, str]] = {}
        self._load_channel_registry()

        self._plugin_hooks = None

        # 用户管理器
        self.user_manager = UserManager(self.storage_path / "users")

        # 清理任务
        self._cleanup_task: asyncio.Task | None = None
        self._save_task: asyncio.Task | None = None
        self._running = False

        # 脏标志和防抖保存
        self._dirty = False
        self._save_delay_seconds = 5  # 防抖延迟：5 秒内的多次修改只保存一次

        # 可选：从外部存储（SQLite）加载 turns 的回调，用于崩溃恢复时回填
        # 签名: (safe_session_id: str) -> list[dict]  (每个 dict 含 role, content, timestamp)
        self._turn_loader = None

        # 会话是否已从磁盘加载完毕（API 层用此判断 ready 语义）
        self._sessions_loaded = False

        # 加载持久化的会话
        self._load_sessions()

    async def start(self) -> None:
        """启动会话管理器"""
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        self._save_task = asyncio.create_task(self._save_loop())
        logger.info("SessionManager started")

    def mark_dirty(self) -> None:
        """标记会话数据已修改，需要保存"""
        self._dirty = True

    def _dispatch_hook_fire_and_forget(self, hook_name: str, **kwargs) -> None:
        """Dispatch a plugin hook from sync context (best-effort, non-blocking)."""
        if self._plugin_hooks is None:
            return
        try:
            import asyncio

            loop = asyncio.get_running_loop()
            task = loop.create_task(self._plugin_hooks.dispatch(hook_name, **kwargs))
            task.add_done_callback(self._hook_task_done)
        except RuntimeError:
            pass
        except Exception as e:
            logger.debug(f"Hook '{hook_name}' dispatch error: {e}")

    @staticmethod
    def _hook_task_done(task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning("Plugin hook task failed: %s", exc)

    def flush(self) -> None:
        """立即保存所有待写入的会话（绕过防抖延迟）"""
        if self._dirty:
            self._dirty = False
            self._save_sessions()

    def set_turn_loader(self, loader) -> None:
        """设置 turn_loader 回调（延迟绑定，Agent 初始化完成后调用）"""
        self._turn_loader = loader

    def backfill_sessions_from_store(self) -> int:
        """用 turn_loader 回填所有 session 中可能缺失的消息（崩溃恢复）。

        Returns:
            回填的总 turn 数
        """
        import re

        if not self._turn_loader:
            return 0
        total_backfilled = 0
        for session in self._sessions.values():
            try:
                safe_id = session.session_key.replace(":", "__")
                safe_id = re.sub(r'[/\\+=%?*<>|"\x00-\x1f]', "_", safe_id)
                db_turns = self._turn_loader(safe_id)
                if not db_turns:
                    continue
                last_ts = ""
                if session.context.messages:
                    last_ts = session.context.messages[-1].get("timestamp", "")
                newer = [t for t in db_turns if t.get("timestamp", "") > last_ts] if last_ts else []
                if not newer and not session.context.messages and db_turns:
                    newer = db_turns
                newer = [t for t in newer if (t.get("content") or "").strip()]
                for t in newer:
                    session.context.add_message(
                        role=t["role"],
                        content=t.get("content", ""),
                    )
                if newer:
                    total_backfilled += len(newer)
                    logger.info(
                        f"Backfilled {len(newer)} turns from SQLite for {session.session_key}"
                    )
            except Exception as e:
                logger.warning(f"Turn backfill failed for {session.session_key}: {e}")
        if total_backfilled:
            self.mark_dirty()
        return total_backfilled

    async def stop(self) -> None:
        """停止会话管理器"""
        self._running = False

        # 取消清理任务
        if self._cleanup_task:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task

        # 取消保存任务
        if self._save_task:
            self._save_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._save_task

        # 最终保存所有会话
        self._save_sessions()
        logger.info("SessionManager stopped")

    def get_session(
        self,
        channel: str,
        chat_id: str,
        user_id: str,
        thread_id: str | None = None,
        create_if_missing: bool = True,
        config: SessionConfig | None = None,
        chat_type: str = "private",
        display_name: str = "",
        chat_name: str = "",
    ) -> Session | None:
        """
        获取或创建会话

        Args:
            channel: 来源通道
            chat_id: 聊天 ID
            user_id: 用户 ID
            thread_id: 话题/线程 ID（可选，用于话题级隔离）
            create_if_missing: 如果不存在是否创建
            config: 会话配置（创建时使用）
            chat_type: 聊天类型 ("group" | "private")
            display_name: 用户昵称
            chat_name: 聊天/群组名称

        Returns:
            Session 或 None
        """
        session_key = f"{channel}:{chat_id}:{user_id}"
        if thread_id:
            session_key += f":{thread_id}"

        with self._sessions_lock:
            # 检查缓存
            if session_key in self._sessions:
                session = self._sessions[session_key]
                session.touch()
                return session

            # 创建新会话
            if create_if_missing:
                session = self._create_session(
                    channel, chat_id, user_id, thread_id, config,
                    chat_type=chat_type, display_name=display_name,
                    chat_name=chat_name,
                )
                self._sessions[session_key] = session
                logger.info(f"Created new session: {session_key}")
                self._dispatch_hook_fire_and_forget(
                    "on_session_start", session=session, session_key=session_key
                )
                return session

        return None

    def get_session_by_id(self, session_id: str) -> Session | None:
        """通过 session_id 获取会话"""
        with self._sessions_lock:
            for session in self._sessions.values():
                if session.id == session_id:
                    return session
        return None

    def _create_session(
        self,
        channel: str,
        chat_id: str,
        user_id: str,
        thread_id: str | None = None,
        config: SessionConfig | None = None,
        chat_type: str = "private",
        display_name: str = "",
        chat_name: str = "",
    ) -> Session:
        """创建新会话"""
        # 合并配置
        session_config = (
            config.merge_with_defaults(self.default_config) if config else self.default_config
        )

        session = Session.create(
            channel=channel,
            chat_id=chat_id,
            user_id=user_id,
            thread_id=thread_id,
            config=session_config,
            chat_type=chat_type,
            display_name=display_name,
            chat_name=chat_name,
        )

        # 设置记忆范围
        session.context.memory_scope = f"session_{session.id}"

        # 更新通道注册表（持久记录 channel→chat_id 映射，不受 session 过期影响）
        self._update_channel_registry(channel, chat_id, user_id)

        return session

    def close_session(self, session_key: str) -> bool:
        """关闭会话"""
        closed_session = None
        with self._sessions_lock:
            if session_key in self._sessions:
                closed_session = self._sessions[session_key]
                closed_session.close()
                del self._sessions[session_key]
                self.mark_dirty()
                logger.info(f"Closed session: {session_key}")
        if closed_session is not None:
            self._dispatch_hook_fire_and_forget(
                "on_session_end", session=closed_session, session_key=session_key,
                reason="close",
            )
            return True
        return False

    def list_sessions(
        self,
        channel: str | None = None,
        user_id: str | None = None,
        state: SessionState | None = None,
    ) -> list[Session]:
        """
        列出会话

        Args:
            channel: 过滤通道
            user_id: 过滤用户
            state: 过滤状态
        """
        with self._sessions_lock:
            sessions = list(self._sessions.values())

        if channel:
            sessions = [s for s in sessions if s.channel == channel]
        if user_id:
            sessions = [s for s in sessions if s.user_id == user_id]
        if state:
            sessions = [s for s in sessions if s.state == state]

        return sessions

    def get_session_count(self) -> dict[str, int]:
        """获取会话统计"""
        with self._sessions_lock:
            all_sessions = list(self._sessions.values())

        stats = {
            "total": len(all_sessions),
            "active": 0,
            "idle": 0,
            "by_channel": {},
        }

        for session in all_sessions:
            if session.state == SessionState.ACTIVE:
                stats["active"] += 1
            elif session.state == SessionState.IDLE:
                stats["idle"] += 1

            channel = session.channel
            stats["by_channel"][channel] = stats["by_channel"].get(channel, 0) + 1

        return stats

    async def cleanup_expired(self) -> int:
        """清理过期会话"""
        with self._sessions_lock:
            expired_keys = [
                key for key, session in self._sessions.items()
                if session.is_expired()
            ]

            for key in expired_keys:
                session = self._sessions[key]
                session.mark_expired()
                del self._sessions[key]
                logger.debug(f"Cleaned up expired session: {key}")

        if expired_keys:
            logger.info(f"Cleaned up {len(expired_keys)} expired sessions")

        return len(expired_keys)

    def purge_channel(self, channel_name: str) -> int:
        """清理指定通道的所有会话和注册表数据。

        当 IM bot 被删除时调用，确保 UI 不再显示已删除通道的聊天窗口。

        Returns:
            被清理的会话数量
        """
        with self._sessions_lock:
            keys_to_remove = [
                key for key, session in self._sessions.items()
                if session.channel == channel_name
            ]
            for key in keys_to_remove:
                del self._sessions[key]

        if channel_name in self._channel_registry:
            del self._channel_registry[channel_name]
            self._save_channel_registry()

        if keys_to_remove:
            self.mark_dirty()
            self._save_sessions()
            logger.info(
                f"Purged {len(keys_to_remove)} sessions and registry for channel: "
                f"{channel_name}"
            )

        return len(keys_to_remove)

    async def _cleanup_loop(self) -> None:
        """定期清理循环（每 24 小时清理 30 天未活跃的僵尸 session）"""
        while self._running:
            try:
                await asyncio.sleep(3600 * 24)
                await self.cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")

    async def _save_loop(self) -> None:
        """
        防抖保存循环

        检测到 dirty 标志后，等待一小段时间再保存，
        这样短时间内的多次修改只会触发一次保存。
        """
        while self._running:
            try:
                await asyncio.sleep(self._save_delay_seconds)

                if self._dirty:
                    self._dirty = False
                    if not self._save_sessions():
                        self._dirty = True

            except asyncio.CancelledError:
                # 退出前最后保存一次
                if self._dirty:
                    self._save_sessions()
                break
            except Exception as e:
                logger.error(f"Error in save loop: {e}")

    def _load_sessions(self) -> None:
        """从文件加载会话，主文件失败时自动回退到 .bak 备份。"""
        sessions_file = self.storage_path / "sessions.json"
        backup_file = self.storage_path / "sessions.json.bak"

        data = self._try_load_sessions_file(sessions_file)
        if data is None and backup_file.exists():
            logger.warning("Main sessions.json failed or missing, trying .bak backup")
            data = self._try_load_sessions_file(backup_file)

        if data is None:
            self._sessions_loaded = True
            return

        skipped_expired = 0
        skipped_error = 0
        for item in data:
            try:
                if not isinstance(item, dict):
                    skipped_error += 1
                    continue
                session = Session.from_dict(item)
                if not session.is_expired() and session.state != SessionState.CLOSED:
                    msg_count = len(session.context.messages)
                    self._clean_large_content_in_messages(session.context.messages)
                    self._sessions[session.session_key] = session
                    if msg_count > 0:
                        logger.debug(
                            f"Loaded session {session.session_key}: "
                            f"{msg_count} messages preserved "
                            f"(last_active: {session.last_active})"
                        )
                else:
                    skipped_expired += 1

                session_ts = session.last_active.isoformat()
                existing = self._channel_registry.get(session.channel)
                _existing_ts = ""
                if isinstance(existing, dict):
                    _existing_ts = existing.get("last_seen", "")
                elif isinstance(existing, list) and existing:
                    top = existing[0]
                    _existing_ts = top.get("last_seen", "") if isinstance(top, dict) else ""
                if not existing or session_ts >= _existing_ts:
                    self._channel_registry[session.channel] = {
                        "chat_id": session.chat_id,
                        "user_id": session.user_id,
                        "last_seen": session_ts,
                    }
            except Exception as e:
                skipped_error += 1
                logger.warning(f"Failed to load session: {e}")

        if self._channel_registry:
            self._save_channel_registry()

        parts = [f"Loaded {len(self._sessions)} sessions from storage"]
        if skipped_expired:
            parts.append(f"skipped {skipped_expired} expired")
        if skipped_error:
            parts.append(f"skipped {skipped_error} errors")
        logger.info(f"{parts[0]}" + (f" ({', '.join(parts[1:])})" if len(parts) > 1 else ""))

        self._sessions_loaded = True

    @staticmethod
    def _try_load_sessions_file(path: Path) -> list[dict] | None:
        """尝试读取并解析 sessions JSON 文件，失败返回 None。"""
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
            logger.warning(f"Unexpected sessions format in {path.name}: {type(data).__name__}")
            return None
        except Exception as e:
            logger.error(f"Failed to parse {path.name}: {e}")
            return None

    _MEDIA_BLOCK_TYPES = frozenset({
        "image", "video", "video_url", "audio", "input_audio",
    })

    def _clean_large_content_in_messages(self, messages: list[dict]) -> None:
        """
        清理消息中的大型数据（base64 图片/视频、大段 tool_result 等）

        session 恢复时调用，防止历史 base64 数据导致上下文爆炸。
        """
        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list):
                continue

            cleaned: list[dict] = []
            for block in content:
                if not isinstance(block, dict):
                    cleaned.append(block)
                    continue

                block_type = block.get("type", "")

                # 图片/视频/音频块 → 替换为文字占位符
                if block_type in self._MEDIA_BLOCK_TYPES:
                    cleaned.append({
                        "type": "text",
                        "text": "[历史媒体内容已清理]",
                    })
                    continue

                # image_url 内嵌 data URI → 替换
                if block_type == "image_url":
                    url = (block.get("image_url") or {}).get("url", "")
                    if url.startswith("data:"):
                        cleaned.append({
                            "type": "text",
                            "text": "[历史图片已清理]",
                        })
                        continue

                # tool_result 中的大型内容
                if block_type == "tool_result":
                    result_content = block.get("content", "")
                    if isinstance(result_content, str) and len(result_content) > 10000:
                        if (
                            "base64" in result_content.lower()
                            or result_content.startswith("data:image")
                        ):
                            block = dict(block)
                            block["content"] = "[图片数据已清理，请重新截图]"
                        else:
                            from openakita.core.tool_executor import smart_truncate
                            block = dict(block)
                            block["content"], _ = smart_truncate(
                                result_content, 4000,
                                label="session_restore",
                                save_full=True,
                            )

                cleaned.append(block)

            msg["content"] = cleaned

    # ==================== 通道注册表 ====================

    def _load_channel_registry(self) -> None:
        """从文件加载通道注册表"""
        registry_file = self.storage_path / "channel_registry.json"
        if not registry_file.exists():
            return
        try:
            with open(registry_file, encoding="utf-8") as f:
                self._channel_registry = json.load(f)
            logger.debug(
                "Loaded channel registry: %s",
                ", ".join(self._channel_registry.keys()) or "(empty)",
            )
        except Exception as e:
            logger.warning(f"Failed to load channel registry: {e}")

    def _save_channel_registry(self) -> None:
        """保存通道注册表到文件（原子写入）"""
        registry_file = self.storage_path / "channel_registry.json"
        try:
            atomic_json_write(registry_file, self._channel_registry)
        except Exception as e:
            logger.warning(f"Failed to save channel registry: {e}")

    def _update_channel_registry(
        self, channel: str, chat_id: str, user_id: str
    ) -> None:
        """
        更新通道注册表

        每当有新 session 创建时调用，持久记录 channel→chat_id 映射。
        兼容旧格式（单 dict）和新格式（list of dicts）。
        同一 channel 保留最近活跃的多个 chat_id（上限 20）。
        """
        now = datetime.now().isoformat()
        entry = self._channel_registry.get(channel)

        # 兼容旧格式：将单 dict 升级为 list
        if isinstance(entry, dict):
            entry = [entry]

        if not isinstance(entry, list):
            entry = []

        # 更新或追加
        found = False
        for item in entry:
            if item.get("chat_id") == chat_id:
                item["user_id"] = user_id
                item["last_seen"] = now
                found = True
                break
        if not found:
            entry.append({"chat_id": chat_id, "user_id": user_id, "last_seen": now})

        # 按 last_seen 排序，保留最近 20 条
        entry.sort(key=lambda x: x.get("last_seen", ""), reverse=True)
        self._channel_registry[channel] = entry[:20]
        self._save_channel_registry()

    def get_known_channel_target(
        self, channel: str
    ) -> tuple[str, str] | None:
        """
        从通道注册表查找通道的最后已知 chat_id

        用于定时任务等场景：即使当前没有活跃 session，
        也能通过历史记录找到推送目标。

        Returns:
            (channel_name, chat_id) 或 None
        """
        entry = self._channel_registry.get(channel)
        # 兼容旧格式（单 dict）
        if isinstance(entry, dict):
            if entry.get("chat_id"):
                return (channel, entry["chat_id"])
        # 新格式（list of dicts）：返回最近活跃的
        elif isinstance(entry, list) and entry:
            top = entry[0]
            if top.get("chat_id"):
                return (channel, top["chat_id"])
        return None

    def get_all_channel_targets(
        self, channel: str
    ) -> list[tuple[str, str]]:
        """返回通道的所有已知 chat_id（多群场景）。"""
        entry = self._channel_registry.get(channel)
        if isinstance(entry, dict):
            if entry.get("chat_id"):
                return [(channel, entry["chat_id"])]
            return []
        if isinstance(entry, list):
            return [(channel, e["chat_id"]) for e in entry if e.get("chat_id")]
        return []

    def _save_sessions(self) -> bool:
        """
        保存会话到文件（原子写入）

        使用临时文件 + 重命名的方式，确保写入过程中断不会损坏原文件。
        返回 True 表示保存成功，False 表示失败（调用方应重试）。
        """
        sessions_file = self.storage_path / "sessions.json"
        temp_file = self.storage_path / "sessions.json.tmp"
        backup_file = self.storage_path / "sessions.json.bak"

        try:
            data = [session.to_dict() for session in self._sessions.values()]

            # 1. 先写入临时文件
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            # 2. 验证临时文件可以正确解析
            with open(temp_file, encoding="utf-8") as f:
                json.load(f)  # 验证 JSON 格式正确

            # 3. 备份旧文件（如果存在）
            if sessions_file.exists():
                try:
                    sessions_file.replace(backup_file)
                except Exception as e:
                    logger.warning(f"Failed to backup sessions file: {e}")

            # 4. 原子重命名临时文件为正式文件 (replace works on Windows too)
            temp_file.replace(sessions_file)

            logger.debug(f"Saved {len(data)} sessions to storage (atomic)")
            return True

        except Exception as e:
            logger.error(f"Failed to save sessions: {e}", exc_info=True)
            # 清理临时文件
            if temp_file.exists():
                with contextlib.suppress(Exception):
                    temp_file.unlink()
            return False

    async def _save_sessions_async(self) -> None:
        """异步保存会话（在线程池中执行同步 I/O）"""
        await asyncio.to_thread(self._save_sessions)

    # ==================== 会话操作快捷方法 ====================

    def add_message(
        self,
        channel: str,
        chat_id: str,
        user_id: str,
        role: str,
        content: str,
        **metadata,
    ) -> Session:
        """添加消息到会话"""
        session = self.get_session(channel, chat_id, user_id)
        session.add_message(role, content, **metadata)
        self.mark_dirty()  # 标记需要保存
        return session

    def get_history(
        self,
        channel: str,
        chat_id: str,
        user_id: str,
        limit: int | None = None,
    ) -> list[dict]:
        """获取会话历史"""
        session = self.get_session(channel, chat_id, user_id, create_if_missing=False)
        if session:
            return session.context.get_messages(limit)
        return []

    def clear_history(
        self,
        channel: str,
        chat_id: str,
        user_id: str,
    ) -> bool:
        """清空会话历史"""
        session = self.get_session(channel, chat_id, user_id, create_if_missing=False)
        if session:
            session.context.clear_messages()
            self.mark_dirty()  # 标记需要保存
            return True
        return False

    def set_variable(
        self,
        channel: str,
        chat_id: str,
        user_id: str,
        key: str,
        value: Any,
    ) -> bool:
        """设置会话变量"""
        session = self.get_session(channel, chat_id, user_id, create_if_missing=False)
        if session:
            session.context.set_variable(key, value)
            self.mark_dirty()  # 标记需要保存
            return True
        return False

    def get_variable(
        self,
        channel: str,
        chat_id: str,
        user_id: str,
        key: str,
        default: Any = None,
    ) -> Any:
        """获取会话变量"""
        session = self.get_session(channel, chat_id, user_id, create_if_missing=False)
        if session:
            return session.context.get_variable(key, default)
        return default
