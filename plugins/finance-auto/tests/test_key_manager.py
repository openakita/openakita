"""Unit tests for ``key_manager.py`` and ``encryption.py`` (Stage 1).

Coverage targets (from the M1 W2 task plan):

* round-trip encrypt/decrypt across all three field domains;
* wrong salt / wrong seed fails closed (``InvalidTag``);
* keyring-unavailable path falls back to env var or stays disabled;
* :func:`pack_payload` / :func:`unpack_payload` survive the on-disk
  layout for sparse domains (e.g. only ``amounts`` set).
"""

from __future__ import annotations

import os
import secrets

import pytest
from cryptography.exceptions import InvalidTag
from finance_auto_backend.encryption import (
    PAYLOAD_VERSION,
    pack_payload,
    unpack_payload,
)
from finance_auto_backend.key_manager import (
    ENV_PASSPHRASE,
    SALT_LEN,
    SEED_LEN,
    KeyManager,
    KeyManagerNotReady,
    KeyringUnavailable,
    acquire_seed,
)

# ---------------------------------------------------------------------------
# KeyManager — happy-path + error-path
# ---------------------------------------------------------------------------


def test_unlock_round_trip_amounts():
    km = KeyManager()
    seed = secrets.token_bytes(SEED_LEN)
    salt = secrets.token_bytes(SALT_LEN)
    km.unlock(seed, salt)
    assert km.is_enabled()

    plaintext = b"123.45"
    blob = km.encrypt("amounts", plaintext)
    assert plaintext not in blob  # ciphertext must not include the plaintext
    assert km.decrypt("amounts", blob) == plaintext


def test_unlock_round_trip_pii_unicode():
    km = KeyManager()
    km.unlock(secrets.token_bytes(SEED_LEN), secrets.token_bytes(SALT_LEN))
    plaintext = "应收账款 — 福建陆海餐饮管理有限公司".encode()
    blob = km.encrypt("pii", plaintext)
    assert km.decrypt("pii", blob) == plaintext


def test_wrong_salt_decrypt_fails():
    km1, km2 = KeyManager(), KeyManager()
    seed = secrets.token_bytes(SEED_LEN)
    km1.unlock(seed, secrets.token_bytes(SALT_LEN))
    km2.unlock(seed, secrets.token_bytes(SALT_LEN))  # different salt
    blob = km1.encrypt("amounts", b"1000.0")
    with pytest.raises(InvalidTag):
        km2.decrypt("amounts", blob)


def test_wrong_seed_decrypt_fails():
    km1, km2 = KeyManager(), KeyManager()
    salt = secrets.token_bytes(SALT_LEN)
    km1.unlock(secrets.token_bytes(SEED_LEN), salt)
    km2.unlock(secrets.token_bytes(SEED_LEN), salt)  # different seed
    blob = km1.encrypt("amounts", b"1000.0")
    with pytest.raises(InvalidTag):
        km2.decrypt("amounts", blob)


def test_lock_clears_state():
    km = KeyManager()
    km.unlock(secrets.token_bytes(SEED_LEN), secrets.token_bytes(SALT_LEN))
    assert km.is_enabled()
    km.lock()
    assert not km.is_enabled()
    with pytest.raises(KeyManagerNotReady):
        km.encrypt("amounts", b"x")


def test_invalid_salt_length_rejected():
    km = KeyManager()
    with pytest.raises(ValueError):
        km.unlock(secrets.token_bytes(SEED_LEN), b"too-short")


def test_unknown_domain_rejected():
    km = KeyManager()
    km.unlock(secrets.token_bytes(SEED_LEN), secrets.token_bytes(SALT_LEN))
    with pytest.raises(ValueError):
        km.encrypt("not_a_real_domain", b"x")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# pack_payload / unpack_payload
# ---------------------------------------------------------------------------


def _unlocked_km() -> KeyManager:
    km = KeyManager()
    km.unlock(secrets.token_bytes(SEED_LEN), secrets.token_bytes(SALT_LEN))
    return km


def test_pack_unpack_roundtrip_full():
    km = _unlocked_km()
    blob = pack_payload(
        km,
        amounts={"opening_debit": 100.0, "closing_debit": 200.5},
        pii={"account_name": "应收账款", "aux_text": "客户A"},
        docrefs={"contract": "C-2025-001"},
    )
    out = unpack_payload(km, blob)
    assert out["amounts"]["opening_debit"] == 100.0
    assert out["amounts"]["closing_debit"] == 200.5
    assert out["pii"]["account_name"] == "应收账款"
    assert out["pii"]["aux_text"] == "客户A"
    assert out["docrefs"]["contract"] == "C-2025-001"


def test_pack_unpack_roundtrip_sparse():
    km = _unlocked_km()
    blob = pack_payload(km, amounts={"opening_debit": 1.0}, pii=None)
    out = unpack_payload(km, blob)
    assert out["amounts"] == {"opening_debit": 1.0}
    assert out["pii"] == {}
    assert out["docrefs"] == {}


def test_unpack_empty_blob_returns_empty_dicts():
    km = _unlocked_km()
    assert unpack_payload(km, b"") == {"amounts": {}, "pii": {}, "docrefs": {}}


def test_unpack_short_blob_raises():
    km = _unlocked_km()
    with pytest.raises(ValueError):
        unpack_payload(km, b"\x01\x00")


def test_unpack_version_mismatch_raises():
    km = _unlocked_km()
    blob = pack_payload(km, amounts={"x": 1})
    bad = bytes([PAYLOAD_VERSION + 1]) + blob[1:]
    with pytest.raises(ValueError):
        unpack_payload(km, bad)


def test_pack_requires_unlocked_km():
    km = KeyManager()  # locked
    with pytest.raises(KeyManagerNotReady):
        pack_payload(km, amounts={"x": 1})


# ---------------------------------------------------------------------------
# Seed acquisition (env-var fallback) — keyring path is exercised in the
# acceptance script since it touches OS state.
# ---------------------------------------------------------------------------


def test_acquire_seed_env_var(monkeypatch):
    """When the keyring is empty but the env var is set, we use the env var."""
    monkeypatch.setattr(
        "finance_auto_backend.key_manager._load_seed_from_keyring",
        lambda account=None: None,
    )
    monkeypatch.setenv(ENV_PASSPHRASE, "ci-passphrase-spike")
    seed, source = acquire_seed(create_if_missing=False)
    assert source == "env"
    assert seed == b"ci-passphrase-spike"


def test_acquire_seed_keyring_unavailable_no_create(monkeypatch):
    """When create_if_missing=False and nothing is configured, raise."""
    monkeypatch.setattr(
        "finance_auto_backend.key_manager._load_seed_from_keyring",
        lambda account=None: None,
    )
    monkeypatch.delenv(ENV_PASSPHRASE, raising=False)
    with pytest.raises(KeyringUnavailable):
        acquire_seed(create_if_missing=False)


def test_acquire_seed_keyring_unavailable_creates_seed(monkeypatch):
    """When create_if_missing=True, generate a fresh seed."""
    monkeypatch.setattr(
        "finance_auto_backend.key_manager._load_seed_from_keyring",
        lambda account=None: None,
    )
    monkeypatch.setattr(
        "finance_auto_backend.key_manager._store_seed_in_keyring",
        lambda seed, account=None: False,
    )
    monkeypatch.delenv(ENV_PASSPHRASE, raising=False)
    seed, source = acquire_seed(create_if_missing=True)
    # Failed keyring write should fall back to env-var hand-off.
    assert source == "generated"
    assert len(seed) == 32
    assert os.environ.get(ENV_PASSPHRASE) == seed.hex()
    monkeypatch.delenv(ENV_PASSPHRASE, raising=False)
