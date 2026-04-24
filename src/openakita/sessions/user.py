"""
用户管理

提供跨平台用户管理:
- 统一用户 ID
- 多平台绑定
- 用户偏好
- 权限管理
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class User:
    """
    用户对象

    代表一个跨平台的统一用户
    """

    id: str  # 统一用户 ID

    # 各平台绑定 {channel: channel_user_id}
    bindings: dict[str, str] = field(default_factory=dict)

    # 偏好设置
    preferences: dict[str, Any] = field(default_factory=dict)

    # 权限
    permissions: list[str] = field(default_factory=lambda: ["user"])

    # 元数据
    display_name: str | None = None
    avatar_url: str | None = None

    # 统计
    created_at: datetime = field(default_factory=datetime.now)
    last_seen: datetime = field(default_factory=datetime.now)
    total_messages: int = 0

    @classmethod
    def create(cls, channel: str, channel_user_id: str) -> "User":
        """创建新用户"""
        user_id = f"user_{uuid.uuid4().hex[:12]}"
        return cls(
            id=user_id,
            bindings={channel: channel_user_id},
        )

    def bind_channel(self, channel: str, channel_user_id: str) -> None:
        """绑定新通道"""
        self.bindings[channel] = channel_user_id
        logger.info(f"User {self.id} bound to {channel}:{channel_user_id}")

    def unbind_channel(self, channel: str) -> bool:
        """解绑通道"""
        if channel in self.bindings:
            del self.bindings[channel]
            logger.info(f"User {self.id} unbound from {channel}")
            return True
        return False

    def get_channel_user_id(self, channel: str) -> str | None:
        """获取通道用户 ID"""
        return self.bindings.get(channel)

    def is_bound_to(self, channel: str) -> bool:
        """检查是否绑定到通道"""
        return channel in self.bindings

    def touch(self) -> None:
        """更新活跃时间"""
        self.last_seen = datetime.now()

    def increment_messages(self) -> None:
        """增加消息计数"""
        self.total_messages += 1
        self.touch()

    # 偏好管理
    def set_preference(self, key: str, value: Any) -> None:
        """设置偏好"""
        self.preferences[key] = value

    def get_preference(self, key: str, default: Any = None) -> Any:
        """获取偏好"""
        return self.preferences.get(key, default)

    # 权限管理
    def has_permission(self, permission: str) -> bool:
        """检查权限"""
        return permission in self.permissions or "admin" in self.permissions

    def add_permission(self, permission: str) -> None:
        """添加权限"""
        if permission not in self.permissions:
            self.permissions.append(permission)

    def remove_permission(self, permission: str) -> bool:
        """移除权限"""
        if permission in self.permissions:
            self.permissions.remove(permission)
            return True
        return False

    def is_admin(self) -> bool:
        """是否管理员"""
        return "admin" in self.permissions

    def to_dict(self) -> dict:
        """序列化"""
        return {
            "id": self.id,
            "bindings": self.bindings,
            "preferences": self.preferences,
            "permissions": self.permissions,
            "display_name": self.display_name,
            "avatar_url": self.avatar_url,
            "created_at": self.created_at.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "total_messages": self.total_messages,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "User":
        """反序列化"""
        return cls(
            id=data["id"],
            bindings=data.get("bindings", {}),
            preferences=data.get("preferences", {}),
            permissions=data.get("permissions", ["user"]),
            display_name=data.get("display_name"),
            avatar_url=data.get("avatar_url"),
            created_at=datetime.fromisoformat(data["created_at"]),
            last_seen=datetime.fromisoformat(data["last_seen"]),
            total_messages=data.get("total_messages", 0),
        )


class UserManager:
    """
    用户管理器

    管理跨平台用户:
    - 根据 (channel, channel_user_id) 获取或创建用户
    - 用户绑定和解绑
    - 用户数据持久化
    """

    def __init__(self, storage_path: Path | None = None):
        """
        Args:
            storage_path: 用户数据存储目录
        """
        self.storage_path = Path(storage_path) if storage_path else Path("data/users")
        self.storage_path.mkdir(parents=True, exist_ok=True)

        # 用户缓存 {user_id: User}
        self._users: dict[str, User] = {}

        # 通道绑定索引 {channel:channel_user_id: user_id}
        self._binding_index: dict[str, str] = {}

        # 加载用户数据
        self._load_users()

    def get_or_create(self, channel: str, channel_user_id: str) -> User:
        """
        获取或创建用户

        如果 (channel, channel_user_id) 已绑定到用户，返回该用户
        否则创建新用户并绑定
        """
        binding_key = f"{channel}:{channel_user_id}"

        # 检查是否已绑定
        if binding_key in self._binding_index:
            user_id = self._binding_index[binding_key]
            user = self._users.get(user_id)
            if user:
                user.touch()
                return user

        # 创建新用户
        user = User.create(channel, channel_user_id)
        self._users[user.id] = user
        self._binding_index[binding_key] = user.id

        logger.info(f"Created new user: {user.id} from {channel}:{channel_user_id}")
        self._save_users()

        return user

    def get_user(self, user_id: str) -> User | None:
        """通过用户 ID 获取用户"""
        return self._users.get(user_id)

    def get_user_by_binding(self, channel: str, channel_user_id: str) -> User | None:
        """通过通道绑定获取用户"""
        binding_key = f"{channel}:{channel_user_id}"
        user_id = self._binding_index.get(binding_key)
        if user_id:
            return self._users.get(user_id)
        return None

    def bind_channel(
        self,
        user_id: str,
        channel: str,
        channel_user_id: str,
    ) -> bool:
        """
        绑定用户到新通道

        Returns:
            是否成功
        """
        user = self._users.get(user_id)
        if not user:
            return False

        binding_key = f"{channel}:{channel_user_id}"

        # 检查是否已被其他用户绑定
        if binding_key in self._binding_index:
            existing_user_id = self._binding_index[binding_key]
            if existing_user_id != user_id:
                logger.warning(
                    f"Channel {channel}:{channel_user_id} already bound to {existing_user_id}"
                )
                return False

        # 绑定
        user.bind_channel(channel, channel_user_id)
        self._binding_index[binding_key] = user_id
        self._save_users()

        return True

    def unbind_channel(self, user_id: str, channel: str) -> bool:
        """解绑用户的通道"""
        user = self._users.get(user_id)
        if not user:
            return False

        channel_user_id = user.get_channel_user_id(channel)
        if not channel_user_id:
            return False

        binding_key = f"{channel}:{channel_user_id}"

        # 解绑
        user.unbind_channel(channel)
        if binding_key in self._binding_index:
            del self._binding_index[binding_key]

        self._save_users()
        return True

    def merge_users(self, primary_user_id: str, secondary_user_id: str) -> bool:
        """
        合并用户

        将 secondary 的绑定合并到 primary，然后删除 secondary
        """
        primary = self._users.get(primary_user_id)
        secondary = self._users.get(secondary_user_id)

        if not primary or not secondary:
            return False

        # 合并绑定
        for channel, channel_user_id in secondary.bindings.items():
            if channel not in primary.bindings:
                primary.bind_channel(channel, channel_user_id)
                binding_key = f"{channel}:{channel_user_id}"
                self._binding_index[binding_key] = primary_user_id

        # 合并偏好（primary 优先）
        for key, value in secondary.preferences.items():
            if key not in primary.preferences:
                primary.preferences[key] = value

        # 合并统计
        primary.total_messages += secondary.total_messages

        # 删除 secondary
        del self._users[secondary_user_id]

        logger.info(f"Merged user {secondary_user_id} into {primary_user_id}")
        self._save_users()

        return True

    def update_preferences(self, user_id: str, preferences: dict) -> bool:
        """更新用户偏好"""
        user = self._users.get(user_id)
        if not user:
            return False

        for key, value in preferences.items():
            user.set_preference(key, value)

        self._save_users()
        return True

    def list_users(
        self,
        channel: str | None = None,
        has_permission: str | None = None,
    ) -> list[User]:
        """列出用户"""
        users = list(self._users.values())

        if channel:
            users = [u for u in users if u.is_bound_to(channel)]
        if has_permission:
            users = [u for u in users if u.has_permission(has_permission)]

        return users

    def get_stats(self) -> dict:
        """获取统计"""
        stats = {
            "total_users": len(self._users),
            "total_bindings": len(self._binding_index),
            "by_channel": {},
            "admins": 0,
        }

        for user in self._users.values():
            if user.is_admin():
                stats["admins"] += 1
            for channel in user.bindings:
                stats["by_channel"][channel] = stats["by_channel"].get(channel, 0) + 1

        return stats

    def _load_users(self) -> None:
        """加载用户数据"""
        users_file = self.storage_path / "users.json"

        if not users_file.exists():
            return

        try:
            with open(users_file, encoding="utf-8") as f:
                data = json.load(f)

            for item in data:
                try:
                    user = User.from_dict(item)
                    self._users[user.id] = user

                    # 重建绑定索引
                    for channel, channel_user_id in user.bindings.items():
                        binding_key = f"{channel}:{channel_user_id}"
                        self._binding_index[binding_key] = user.id

                except Exception as e:
                    logger.warning(f"Failed to load user: {e}")

            logger.info(f"Loaded {len(self._users)} users from storage")

        except Exception as e:
            logger.error(f"Failed to load users: {e}")

    def _save_users(self) -> None:
        """保存用户数据"""
        users_file = self.storage_path / "users.json"

        try:
            data = [user.to_dict() for user in self._users.values()]

            with open(users_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            logger.debug(f"Saved {len(data)} users to storage")

        except Exception as e:
            logger.error(f"Failed to save users: {e}")

