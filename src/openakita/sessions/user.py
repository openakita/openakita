"""
User management

Provides cross-platform user management:
- Unified user ID
- Multi-platform binding
- User preferences
- Permission management
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
    User object

    Represents a unified cross-platform user
    """

    id: str  # Unified user ID

    # Platform bindings {channel: channel_user_id}
    bindings: dict[str, str] = field(default_factory=dict)

    # Preferences
    preferences: dict[str, Any] = field(default_factory=dict)

    # Permissions
    permissions: list[str] = field(default_factory=lambda: ["user"])

    # Metadata
    display_name: str | None = None
    avatar_url: str | None = None

    # Statistics
    created_at: datetime = field(default_factory=datetime.now)
    last_seen: datetime = field(default_factory=datetime.now)
    total_messages: int = 0

    @classmethod
    def create(cls, channel: str, channel_user_id: str) -> "User":
        """Create a new user"""
        user_id = f"user_{uuid.uuid4().hex[:12]}"
        return cls(
            id=user_id,
            bindings={channel: channel_user_id},
        )

    def bind_channel(self, channel: str, channel_user_id: str) -> None:
        """Bind to a new channel"""
        self.bindings[channel] = channel_user_id
        logger.info(f"User {self.id} bound to {channel}:{channel_user_id}")

    def unbind_channel(self, channel: str) -> bool:
        """Unbind from a channel"""
        if channel in self.bindings:
            del self.bindings[channel]
            logger.info(f"User {self.id} unbound from {channel}")
            return True
        return False

    def get_channel_user_id(self, channel: str) -> str | None:
        """Get the channel user ID"""
        return self.bindings.get(channel)

    def is_bound_to(self, channel: str) -> bool:
        """Check if bound to a channel"""
        return channel in self.bindings

    def touch(self) -> None:
        """Update last active time"""
        self.last_seen = datetime.now()

    def increment_messages(self) -> None:
        """Increment message count"""
        self.total_messages += 1
        self.touch()

    # Preference management
    def set_preference(self, key: str, value: Any) -> None:
        """Set a preference"""
        self.preferences[key] = value

    def get_preference(self, key: str, default: Any = None) -> Any:
        """Get a preference"""
        return self.preferences.get(key, default)

    # Permission management
    def has_permission(self, permission: str) -> bool:
        """Check permission"""
        return permission in self.permissions or "admin" in self.permissions

    def add_permission(self, permission: str) -> None:
        """Add a permission"""
        if permission not in self.permissions:
            self.permissions.append(permission)

    def remove_permission(self, permission: str) -> bool:
        """Remove a permission"""
        if permission in self.permissions:
            self.permissions.remove(permission)
            return True
        return False

    def is_admin(self) -> bool:
        """Check if user is an admin"""
        return "admin" in self.permissions

    def to_dict(self) -> dict:
        """Serialize to dict"""
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
        """Deserialize from dict"""
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
    User manager

    Manages cross-platform users:
    - Get or create users by (channel, channel_user_id)
    - Bind and unbind users
    - Persist user data
    """

    def __init__(self, storage_path: Path | None = None):
        """
        Args:
            storage_path: Directory for storing user data
        """
        self.storage_path = Path(storage_path) if storage_path else Path("data/users")
        self.storage_path.mkdir(parents=True, exist_ok=True)

        # User cache {user_id: User}
        self._users: dict[str, User] = {}

        # Channel binding index {channel:channel_user_id: user_id}
        self._binding_index: dict[str, str] = {}

        # Load user data

    def get_or_create(self, channel: str, channel_user_id: str) -> User:
        """
        Get or create a user

        If (channel, channel_user_id) is already bound to a user, return that user.
        Otherwise create a new user and bind it.
        """
        binding_key = f"{channel}:{channel_user_id}"

        # Check if already bound
            user_id = self._binding_index[binding_key]
            user = self._users.get(user_id)
            if user:
                user.touch()
                return user

        # Create new user
        self._users[user.id] = user
        self._binding_index[binding_key] = user.id

        logger.info(f"Created new user: {user.id} from {channel}:{channel_user_id}")
        self._save_users()

        return user

    def get_user(self, user_id: str) -> User | None:
        """Get user by user ID"""
        return self._users.get(user_id)

    def get_user_by_binding(self, channel: str, channel_user_id: str) -> User | None:
        """Get user by channel binding"""
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
        Bind a user to a new channel

        Returns:
            Whether the binding was successful
        """
        user = self._users.get(user_id)
        if not user:
            return False

        binding_key = f"{channel}:{channel_user_id}"

        # Check if already bound to another user
        if binding_key in self._binding_index:
            existing_user_id = self._binding_index[binding_key]
            if existing_user_id != user_id:
                logger.warning(
                    f"Channel {channel}:{channel_user_id} already bound to {existing_user_id}"
                )
                return False

        # Bind
        user.bind_channel(channel, channel_user_id)
        self._binding_index[binding_key] = user_id
        self._save_users()

        return True

    def unbind_channel(self, user_id: str, channel: str) -> bool:
        """Unbind a user's channel"""
        user = self._users.get(user_id)
        if not user:
            return False

        channel_user_id = user.get_channel_user_id(channel)
        if not channel_user_id:
            return False

        binding_key = f"{channel}:{channel_user_id}"

        # Unbind
        user.unbind_channel(channel)
        if binding_key in self._binding_index:
            del self._binding_index[binding_key]

        self._save_users()
        return True

    def merge_users(self, primary_user_id: str, secondary_user_id: str) -> bool:
        """
        Merge users

        Merge secondary's bindings into primary, then delete secondary.
        """
        primary = self._users.get(primary_user_id)
        secondary = self._users.get(secondary_user_id)

        if not primary or not secondary:
            return False

        # Merge bindings
            if channel not in primary.bindings:
                primary.bind_channel(channel, channel_user_id)
                binding_key = f"{channel}:{channel_user_id}"
                self._binding_index[binding_key] = primary_user_id

        # Merge preferences (primary takes priority)
        for key, value in secondary.preferences.items():
            if key not in primary.preferences:
                primary.preferences[key] = value

        # Merge statistics
        primary.total_messages += secondary.total_messages

        # Delete secondary
        del self._users[secondary_user_id]

        logger.info(f"Merged user {secondary_user_id} into {primary_user_id}")
        self._save_users()

        return True

    def update_preferences(self, user_id: str, preferences: dict) -> bool:
        """Update user preferences"""
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
        """List users"""
        users = list(self._users.values())

        if channel:
            users = [u for u in users if u.is_bound_to(channel)]
        if has_permission:
            users = [u for u in users if u.has_permission(has_permission)]

        return users

    def get_stats(self) -> dict:
        """Get statistics"""
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
        """Load user data"""
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

                    # Rebuild binding index
                    for channel, channel_user_id in user.bindings.items():
                        binding_key = f"{channel}:{channel_user_id}"
                        self._binding_index[binding_key] = user.id

                except Exception as e:
                    logger.warning(f"Failed to load user: {e}")

            logger.info(f"Loaded {len(self._users)} users from storage")

        except Exception as e:
            logger.error(f"Failed to load users: {e}")

    def _save_users(self) -> None:
        """Save user data"""
        users_file = self.storage_path / "users.json"

        try:
            data = [user.to_dict() for user in self._users.values()]

            with open(users_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            logger.debug(f"Saved {len(data)} users to storage")

        except Exception as e:
            logger.error(f"Failed to save users: {e}")
