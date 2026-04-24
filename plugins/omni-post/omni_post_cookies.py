"""omni-post cookie pool — Fernet-encrypted storage + lazy health probe.

Design rules
------------

- **Never** store cookies in clear text. Every ``accounts.cookie_cipher``
  column is a :class:`cryptography.fernet.Fernet` ciphertext of the raw
  Netscape / JSON blob the user pasted.
- The Fernet key is derived ONCE at plugin load from a per-install salt
  saved under ``data/omni-post/identity.salt``. The salt itself is
  random 32B, not checked into git. If the file is missing we generate
  it on the fly — which means "identity" here is *local* and per
  install, not a global secret.
- Health probes are **lazy** (fixes MultiPost-Extension issue #207, the
  "eager polling" anti-pattern). Callers only run a probe when:
    (a) the Accounts tab is opened,
    (b) the user hits the "Refresh" button,
    (c) a publish pre-check runs right before the pipeline spawns the
        real adapter step.
  Nothing in this module should start background timers or threads.
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("openakita.plugins.omni-post")


_SALT_FILE = "identity.salt"
_KEY_VERSION = 1  # bump if we ever change the derivation; reject old ciphertext


class CookieEncryptError(RuntimeError):
    """Raised on decryption failure so the caller can mark cookie_expired."""


class CookiePool:
    """Fernet-backed wrapper used by the task manager for cookie I/O.

    The pool is stateless apart from the Fernet key; callers should
    ``seal`` before inserting into SQLite and ``open`` before injecting
    into the Playwright browser context.
    """

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = Path(data_dir)
        self._fernet = Fernet(self._load_key())

    def _load_key(self) -> bytes:
        """Return (or lazily create) the Fernet key for this install.

        Persisted as a base64-encoded 32B key in ``identity.salt`` under
        the plugin's data dir. The file mode is best-effort tightened to
        0o600 on POSIX; on Windows the ACL already prevents other users
        from reading the plugin data dir by default.
        """

        self._data_dir.mkdir(parents=True, exist_ok=True)
        path = self._data_dir / _SALT_FILE
        if path.exists():
            try:
                data = path.read_bytes().strip()
                if data:
                    return data
            except OSError as e:
                logger.warning("cookie_pool: cannot read existing salt file, regenerating: %s", e)

        key = Fernet.generate_key()
        path.write_bytes(key)
        try:
            path.chmod(0o600)
        except OSError:
            # Windows doesn't honour chmod bits; OK — directory ACL handles it.
            pass
        logger.info("cookie_pool: generated new Fernet key")
        return key

    # ── Encrypt / decrypt ───────────────────────────────────────────

    def seal(self, plaintext: str) -> bytes:
        """Return the Fernet ciphertext bytes for the raw cookie blob.

        The caller is expected to pass either a Netscape cookies.txt
        string or a JSON array of cookie dicts — both formats are
        supported by :class:`omni_post_engine_pw.CookieLoader`.
        """

        if not plaintext:
            raise ValueError("cookie plaintext must be non-empty")
        return self._fernet.encrypt(plaintext.encode("utf-8"))

    def open(self, ciphertext: bytes) -> str:
        """Return the decrypted cookie blob or raise :class:`CookieEncryptError`."""

        try:
            return self._fernet.decrypt(bytes(ciphertext)).decode("utf-8")
        except (InvalidToken, ValueError, TypeError) as e:
            raise CookieEncryptError(
                "cookie decryption failed — the Fernet key likely changed; "
                "ask the user to re-import this account"
            ) from e

    # ── Health probe (lazy) ─────────────────────────────────────────

    async def probe_lazy(
        self,
        account: dict,
        *,
        probe_fn,  # callable: (cookie_plaintext) -> "ok" | "cookie_expired" | "unknown"
    ) -> str:
        """Run a one-off probe on the account's decrypted cookie.

        Callers supply ``probe_fn`` (normally implemented in the engine
        layer). Never invoked on a timer; only on-demand. Returns one of
        ``ok`` / ``cookie_expired`` / ``unknown``.
        """

        try:
            plaintext = self.open(account["cookie_cipher"])
        except CookieEncryptError:
            return "cookie_expired"
        try:
            verdict = await probe_fn(plaintext)
        except Exception as e:  # noqa: BLE001 - probe failures must not crash caller
            logger.warning(
                "cookie_pool: probe for %s errored: %s",
                account.get("id"),
                e,
            )
            return "unknown"
        return verdict or "unknown"


def new_random_hex(n: int = 16) -> str:
    """Small helper for adapter tests that need a stable-but-random string."""

    return secrets.token_hex(n)


__all__ = [
    "CookieEncryptError",
    "CookiePool",
    "new_random_hex",
]
