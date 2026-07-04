"""KeyManager — field-level AES-256-GCM with OS keyring seed (M1 W2).

Replaces the M1 W1 stub with a working implementation that follows v0.3
Part Infra §2.

Key design points
-----------------

* **Seed source**: OS keyring (Windows Credential Manager / macOS Keychain /
  Linux Secret Service) via the ``keyring`` package.  If the keyring backend
  is unusable on this host (e.g. headless CI without secret service) the
  manager falls back to the ``OPENAKITA_FINANCE_AUTO_PASSPHRASE`` env var; if
  neither is available the manager stays *disabled* and callers fall back to
  cleartext columns (keeping the W1 behaviour intact).
* **KDF**: PBKDF2-HMAC-SHA256, 200 000 iterations (v0.3 Part Infra §2.3).
* **Field domains**: ``amounts`` (six numeric columns) and ``pii`` (account
  name + aux text + source filename + org name).  Each domain has its own key
  derived via HKDF-SHA256 from the master key with a domain-specific
  ``info`` byte string.  ``docrefs`` is reserved but not currently used.
* **Cipher**: AES-256-GCM with a 12-byte random nonce per encryption.  AAD =
  ``b"openakita-finance-v1"`` (matches the design constant).
* **Salt**: 32 random bytes generated on first ``enable_encryption`` call and
  stored in the ``key_meta`` table.  Salt rotation = full re-encryption of
  every row (handled by the migration script).

The KeyManager is **single-org** in M1 W2 — one master key serves all orgs in
the shared SQLite file.  v0.3 Part Infra §5.1 wants a per-org KeyManager
once we move to per-org encrypted DB files; that refactor is M2 work and is
flagged in the completion report's §7 deferral list.
"""

from __future__ import annotations

import ctypes
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Literal

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (mirror v0.3 Part Infra §2.3)
# ---------------------------------------------------------------------------

PBKDF2_ITERATIONS = 200_000
KEY_LEN = 32
NONCE_LEN = 12
AAD = b"openakita-finance-v1"
SEED_LEN = 32
SALT_LEN = 32

KEYRING_SERVICE = "openakita-finance-auto"
KEYRING_ACCOUNT_DEFAULT = "global"
ENV_PASSPHRASE = "OPENAKITA_FINANCE_AUTO_PASSPHRASE"

FieldDomain = Literal["amounts", "pii", "docrefs"]
FIELD_DOMAINS: tuple[FieldDomain, ...] = ("amounts", "pii", "docrefs")


class KeyManagerNotReady(RuntimeError):
    """Raised when a caller attempts to encrypt/decrypt before unlock."""


class KeyringUnavailable(RuntimeError):
    """Raised when neither the OS keyring nor the env-var fallback works."""


# ---------------------------------------------------------------------------
# In-memory unlocked state
# ---------------------------------------------------------------------------


@dataclass
class _UnlockedState:
    master_key: bytearray
    field_keys: dict[str, bytearray] = field(default_factory=dict)
    salt: bytes = b""
    last_used: float = field(default_factory=time.time)


def _wipe(buf: bytearray) -> None:
    """Best-effort overwrite of a key buffer.

    Python doesn't expose ``mlock``-style guarantees, so this only mitigates
    casual memory inspection; it's still a meaningful defence-in-depth step.
    """
    if not buf:
        return
    try:
        ctypes.memset(
            ctypes.addressof((ctypes.c_char * len(buf)).from_buffer(buf)),
            0,
            len(buf),
        )
    except Exception:
        for i in range(len(buf)):
            buf[i] = 0


# ---------------------------------------------------------------------------
# Seed loading helpers
# ---------------------------------------------------------------------------


def _load_seed_from_keyring(account: str = KEYRING_ACCOUNT_DEFAULT) -> bytes | None:
    """Return the seed bytes from the OS keyring or ``None`` if missing.

    Errors during keyring access (no backend, locked, permission denied) are
    swallowed and surfaced as ``None`` so callers can decide whether to fall
    back to env-var or stay disabled.
    """
    try:
        import keyring

        raw = keyring.get_password(KEYRING_SERVICE, account)
    except Exception as exc:  # noqa: BLE001 — keyring is optional infra
        logger.warning("finance-auto: keyring lookup failed: %s", exc)
        return None
    if not raw:
        return None
    try:
        return bytes.fromhex(raw)
    except ValueError:
        return raw.encode("utf-8")


def _store_seed_in_keyring(seed: bytes, account: str = KEYRING_ACCOUNT_DEFAULT) -> bool:
    """Persist the seed in the OS keyring; return True on success."""
    try:
        import keyring

        keyring.set_password(KEYRING_SERVICE, account, seed.hex())
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("finance-auto: keyring write failed: %s", exc)
        return False


def _seed_from_env() -> bytes | None:
    raw = os.environ.get(ENV_PASSPHRASE)
    if not raw:
        return None
    return raw.encode("utf-8")


def acquire_seed(
    *,
    account: str = KEYRING_ACCOUNT_DEFAULT,
    create_if_missing: bool = True,
) -> tuple[bytes, str]:
    """Return ``(seed_bytes, source)`` where source ∈ {``keyring``, ``env``,
    ``generated``}.

    Lookup order:

    1. OS keyring (preferred, persistent across runs);
    2. ``OPENAKITA_FINANCE_AUTO_PASSPHRASE`` env var (CI / headless fallback);
    3. If ``create_if_missing`` is true, generate a fresh 32 byte random seed
       and try to persist it in the keyring (so the next run finds it).

    Raises :class:`KeyringUnavailable` if all paths fail.
    """
    s = _load_seed_from_keyring(account)
    if s:
        return s, "keyring"

    s = _seed_from_env()
    if s:
        return s, "env"

    if not create_if_missing:
        raise KeyringUnavailable(
            "no seed in keyring or env, and create_if_missing=False"
        )

    seed = secrets.token_bytes(SEED_LEN)
    stored = _store_seed_in_keyring(seed, account)
    if not stored:
        logger.warning(
            "finance-auto: generated a fresh seed but could not persist it "
            "in the keyring; future runs will need OPENAKITA_FINANCE_AUTO_PASSPHRASE."
        )
        os.environ[ENV_PASSPHRASE] = seed.hex()
    return seed, "generated"


# ---------------------------------------------------------------------------
# KeyManager
# ---------------------------------------------------------------------------


class KeyManager:
    """Single-process, single-org key manager.

    Intended usage::

        km = KeyManager()
        seed, src = acquire_seed()
        salt = secrets.token_bytes(32)
        km.unlock(seed, salt)
        ct = km.encrypt("amounts", b"123.45")
        pt = km.decrypt("amounts", ct)
        km.lock()
    """

    def __init__(self, idle_timeout_sec: int = 600):
        self.idle_timeout = idle_timeout_sec
        self._state: _UnlockedState | None = None

    # --- lifecycle -----------------------------------------------------

    def unlock(self, seed: bytes, salt: bytes) -> None:
        """Derive the master key + per-domain field keys.

        Idempotent: calling unlock twice with the same seed/salt is fine; a
        second call replaces the cached keys (after wiping the old ones).
        """
        if len(salt) != SALT_LEN:
            raise ValueError(f"salt must be {SALT_LEN} bytes, got {len(salt)}")

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=KEY_LEN,
            salt=salt,
            iterations=PBKDF2_ITERATIONS,
        )
        master = bytearray(kdf.derive(seed))

        field_keys: dict[str, bytearray] = {}
        for domain in FIELD_DOMAINS:
            hkdf = HKDF(
                algorithm=hashes.SHA256(),
                length=KEY_LEN,
                salt=None,
                info=domain.encode("utf-8"),
            )
            field_keys[domain] = bytearray(hkdf.derive(bytes(master)))

        self.lock()
        self._state = _UnlockedState(
            master_key=master, field_keys=field_keys, salt=salt
        )
        logger.info(
            "finance-auto: KeyManager unlocked (PBKDF2 200k, AES-GCM, %d domains)",
            len(field_keys),
        )

    def lock(self) -> None:
        """Wipe key buffers and drop the unlocked state."""
        if not self._state:
            return
        _wipe(self._state.master_key)
        for k in self._state.field_keys.values():
            _wipe(k)
        self._state = None

    # --- queries -------------------------------------------------------

    def is_enabled(self) -> bool:
        return (
            self._state is not None
            and (time.time() - self._state.last_used) < self.idle_timeout
        )

    def salt(self) -> bytes:
        return self._require().salt

    # --- crypto --------------------------------------------------------

    def encrypt(self, domain: FieldDomain, plaintext: bytes) -> bytes:
        st = self._require()
        if domain not in st.field_keys:
            raise ValueError(f"unknown domain: {domain!r}")
        st.last_used = time.time()
        nonce = os.urandom(NONCE_LEN)
        ct = AESGCM(bytes(st.field_keys[domain])).encrypt(nonce, plaintext, AAD)
        return nonce + ct

    def decrypt(self, domain: FieldDomain, blob: bytes) -> bytes:
        st = self._require()
        if domain not in st.field_keys:
            raise ValueError(f"unknown domain: {domain!r}")
        if len(blob) < NONCE_LEN + 16:
            raise ValueError("ciphertext blob too short")
        st.last_used = time.time()
        nonce, ct = blob[:NONCE_LEN], blob[NONCE_LEN:]
        return AESGCM(bytes(st.field_keys[domain])).decrypt(nonce, ct, AAD)

    # --- internal ------------------------------------------------------

    def _require(self) -> _UnlockedState:
        if not self.is_enabled():
            raise KeyManagerNotReady("KeyManager not unlocked or idle-timed-out")
        assert self._state is not None
        return self._state


__all__ = [
    "AAD",
    "FIELD_DOMAINS",
    "FieldDomain",
    "KEYRING_ACCOUNT_DEFAULT",
    "KEYRING_SERVICE",
    "KeyManager",
    "KeyManagerNotReady",
    "KeyringUnavailable",
    "PBKDF2_ITERATIONS",
    "SALT_LEN",
    "SEED_LEN",
    "acquire_seed",
]
