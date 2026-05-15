"""
DM Pairing（配对授权）

通过一次性配对码验证 IM 用户身份，防止未授权用户发送消息给 Agent。

流程:
1. 管理员在 CLI 或已授权通道发起 /pair 命令，生成 8 位配对码（有效期 1 小时）
2. 新用户在 IM 中发送配对码
3. 验证通过后该 chat_id 被永久授权

安全措施:
- 配对码 1 小时过期
- 每个 IP/chat_id 5 次失败后锁定 15 分钟
- 使用 secrets 生成安全随机码
"""

import logging
import secrets
import string
import time
from dataclasses import dataclass
from pathlib import Path

import json as _json

from ..utils.atomic_io import safe_json_write

logger = logging.getLogger(__name__)

CODE_LENGTH = 8
CODE_TTL_SECONDS = 3600
MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 900


@dataclass
class PairingCode:
    code: str
    created_at: float
    created_by: str = ""
    used: bool = False


@dataclass
class FailureRecord:
    count: int = 0
    last_attempt: float = 0.0
    locked_until: float = 0.0


class DMPairingManager:
    """Manages DM pairing codes and authorized chat IDs."""

    def __init__(self, data_dir: Path):
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._auth_file = self._data_dir / "dm_authorized.json"
        self._active_codes: dict[str, PairingCode] = {}
        self._failures: dict[str, FailureRecord] = {}
        self._authorized: set[str] = set()
        self._load_authorized()

    def _load_authorized(self) -> None:
        if self._auth_file.exists():
            try:
                data = _json.loads(self._auth_file.read_text(encoding="utf-8"))
                self._authorized = set(data.get("authorized", []))
                return
            except Exception as e:
                logger.warning(f"Failed to load DM authorized list: {e}")
        self._authorized = set()

    def _save_authorized(self) -> None:
        safe_json_write(self._auth_file, {"authorized": sorted(self._authorized)})

    def _make_key(self, channel: str, chat_id: str) -> str:
        return f"{channel}:{chat_id}"

    def is_authorized(self, channel: str, chat_id: str) -> bool:
        return self._make_key(channel, chat_id) in self._authorized

    def authorize(self, channel: str, chat_id: str) -> None:
        key = self._make_key(channel, chat_id)
        self._authorized.add(key)
        self._save_authorized()
        logger.info(f"DM Pairing: authorized {key}")

    def revoke(self, channel: str, chat_id: str) -> bool:
        key = self._make_key(channel, chat_id)
        if key in self._authorized:
            self._authorized.discard(key)
            self._save_authorized()
            logger.info(f"DM Pairing: revoked {key}")
            return True
        return False

    def generate_code(self, created_by: str = "") -> str:
        self._cleanup_expired()

        alphabet = string.ascii_uppercase + string.digits
        code = "".join(secrets.choice(alphabet) for _ in range(CODE_LENGTH))

        self._active_codes[code] = PairingCode(
            code=code,
            created_at=time.time(),
            created_by=created_by,
        )
        logger.info(f"DM Pairing: generated code {code} (by {created_by})")
        return code

    def verify_code(self, code: str, channel: str, chat_id: str) -> tuple[bool, str]:
        """
        Verify a pairing code.

        Returns:
            (success, message)
        """
        key = self._make_key(channel, chat_id)

        failure = self._failures.get(key)
        if failure and failure.locked_until > time.time():
            remaining = int(failure.locked_until - time.time())
            return False, f"Too many failed attempts. Try again in {remaining}s."

        self._cleanup_expired()
        code = code.strip().upper()

        pc = self._active_codes.get(code)
        if not pc:
            self._record_failure(key)
            return False, "Invalid or expired pairing code."

        if pc.used:
            self._record_failure(key)
            return False, "This code has already been used."

        pc.used = True
        del self._active_codes[code]
        self.authorize(channel, chat_id)

        if key in self._failures:
            del self._failures[key]

        return True, "Pairing successful! You are now authorized."

    def _record_failure(self, key: str) -> None:
        now = time.time()
        rec = self._failures.get(key)
        if not rec:
            rec = FailureRecord()
            self._failures[key] = rec
        rec.count += 1
        rec.last_attempt = now
        if rec.count >= MAX_ATTEMPTS:
            rec.locked_until = now + LOCKOUT_SECONDS
            logger.warning(f"DM Pairing: locked {key} for {LOCKOUT_SECONDS}s after {rec.count} failures")

    def _cleanup_expired(self) -> None:
        now = time.time()
        expired = [
            code for code, pc in self._active_codes.items()
            if now - pc.created_at > CODE_TTL_SECONDS
        ]
        for code in expired:
            del self._active_codes[code]

    def list_authorized(self) -> list[str]:
        return sorted(self._authorized)
