"""Tests for EX-P2-6 — raise DecryptionError on encrypted payload failure.

The pre-fix code silently fell back to ``{"amounts": {}, "pii": {}}``
when a row's ``_encrypted_payload`` couldn't be decoded.  This suite
proves the read paths now:

* raise :class:`encryption.DecryptionError` by default, AND
* honour the ``?accept_corrupted=true`` opt-in (returns empty dicts
  and logs WARNING instead of raising).

We exercise the lowest-level helper (``_maybe_unpack``) directly to
keep the test independent of the key-manager lock state, then
sanity-check the parse_issue helper too.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest import mock

import pytest

from finance_auto_backend.db import FinanceAutoDB
from finance_auto_backend.encryption import DecryptionError
from finance_auto_backend.key_manager import KeyManager
from finance_auto_backend.routes import FinanceAutoService, _maybe_unpack
from finance_auto_backend.parse_issue_routes import _decode_original_data


@pytest.fixture()
def unlocked_service(tmp_path: Path):
    db_path = tmp_path / "decrypt_failure.sqlite"
    db = FinanceAutoDB(db_path)
    asyncio.run(db.init())
    km = KeyManager()
    # Manually unlock the key manager with a deterministic seed; we
    # never need to actually unpack a *real* blob — we feed garbage
    # to force the failure path.
    km.unlock(b"\xaa" * 32, b"\xbb" * 32)
    service = FinanceAutoService(db, key_manager=km)
    yield service, km
    asyncio.run(db.close())


def test_maybe_unpack_raises_on_corrupt_blob(unlocked_service) -> None:
    _service, km = unlocked_service
    # Garbage blob long enough to trip the header parser but with a
    # malformed signature inside — guaranteed to fail decrypt.
    garbage = b"\x99" * 256
    with pytest.raises(DecryptionError):
        _maybe_unpack(km, garbage)


def test_maybe_unpack_accept_corrupted_falls_back(unlocked_service) -> None:
    _service, km = unlocked_service
    garbage = b"\x99" * 256
    out = _maybe_unpack(km, garbage, accept_corrupted=True)
    assert out == {"amounts": {}, "pii": {}, "docrefs": {}}


def test_decode_original_data_raises_on_corrupt_blob(unlocked_service) -> None:
    service, _km = unlocked_service
    # Wrap a hex-encoded garbage blob just like the real parse_issue
    # encrypted-side-channel persists it.
    raw_json = json.dumps({"__enc_blob__": ("99" * 256)})
    with pytest.raises(DecryptionError):
        _decode_original_data(service, raw_json)


def test_decode_original_data_accept_corrupted_logs_and_returns(
    unlocked_service, caplog: pytest.LogCaptureFixture
) -> None:
    service, _km = unlocked_service
    raw_json = json.dumps({"__enc_blob__": ("99" * 256), "extra": "kept"})
    out = _decode_original_data(service, raw_json, accept_corrupted=True)
    # No raise; the plain side of the record survives unchanged.
    assert out["extra"] == "kept"
    # And we logged the opt-in fallback so audits aren't blind.
    assert any(
        "accept_corrupted=true" in rec.getMessage()
        for rec in caplog.records
    )


def test_maybe_unpack_empty_blob_returns_empty_dicts(unlocked_service) -> None:
    """``_maybe_unpack`` should always short-circuit on an empty blob —
    this is the legitimate "row has no encrypted payload" branch and
    must NEVER raise so cleartext-only DBs keep working."""
    _service, km = unlocked_service
    assert _maybe_unpack(km, b"") == {"amounts": {}, "pii": {}, "docrefs": {}}
    assert _maybe_unpack(km, None) == {"amounts": {}, "pii": {}, "docrefs": {}}


def test_maybe_unpack_with_disabled_keymanager_returns_empty(tmp_path: Path) -> None:
    """When the key manager isn't unlocked at all (cleartext-only DB),
    the helper must NOT raise — encryption is simply off and the row
    is read from cleartext columns by the caller."""
    db_path = tmp_path / "no_km.sqlite"
    db = FinanceAutoDB(db_path)
    asyncio.run(db.init())
    try:
        service = FinanceAutoService(db)
        assert not service.encryption_enabled()
        out = _maybe_unpack(service.key_manager, b"some-blob-bytes")
        assert out == {"amounts": {}, "pii": {}, "docrefs": {}}
    finally:
        asyncio.run(db.close())
