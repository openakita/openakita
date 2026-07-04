"""Field-level encryption helpers built on top of :class:`KeyManager`.

The plugin stores sensitive columns (amounts, customer names, source filename
…) into a single ``_encrypted_payload`` BLOB per row.  Each BLOB has the
following on-disk layout (v0.3 Part Infra §2.6, simplified for M1 W2):

```
+----+--------+--------+--------+--------+----------------+----------------+
| v  | len_a  | len_p  | len_d  |        | amounts cipher | pii cipher     |
| 1B | 4B BE  | 4B BE  | 4B BE  | (rsv.) | (nonce|ct)     | (nonce|ct)     |
+----+--------+--------+--------+--------+----------------+----------------+
```

* ``v`` — payload version byte (currently ``1``).
* ``len_a`` / ``len_p`` / ``len_d`` — big-endian uint32 lengths of the three
  domain ciphertexts.  Zero means "this row has no value for that domain".
* The three ciphertext sections are :func:`KeyManager.encrypt` outputs
  (``nonce|ct``) of the JSON-encoded dict for that domain.

The wrapper keeps the JSON structure flexible so future stages can add fields
without bumping the on-disk version byte — readers tolerate unknown keys.
"""

from __future__ import annotations

import json
import logging
import struct
from typing import Any

from .key_manager import KeyManager, KeyManagerNotReady

logger = logging.getLogger(__name__)

PAYLOAD_VERSION = 1
HEADER_FMT = ">BIII"  # version (1B) + 3x BE uint32 lengths
HEADER_LEN = struct.calcsize(HEADER_FMT)


def _dumps(d: dict[str, Any]) -> bytes:
    """Stable JSON dump (sorted keys, no whitespace, ensure_ascii=False)."""
    return json.dumps(d, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def pack_payload(
    km: KeyManager,
    *,
    amounts: dict[str, Any] | None = None,
    pii: dict[str, Any] | None = None,
    docrefs: dict[str, Any] | None = None,
) -> bytes:
    """Encrypt the three domain dicts and concatenate them into the on-disk
    blob.  Empty / ``None`` domain dicts are encoded as zero-length sections.

    Raises :class:`KeyManagerNotReady` if ``km`` is not unlocked.
    """
    if not km.is_enabled():
        raise KeyManagerNotReady(
            "KeyManager must be unlocked before pack_payload(); call enable_encryption() first."
        )

    parts: list[bytes] = []
    for domain, payload in (("amounts", amounts), ("pii", pii), ("docrefs", docrefs)):
        if not payload:
            parts.append(b"")
        else:
            parts.append(km.encrypt(domain, _dumps(payload)))

    header = struct.pack(
        HEADER_FMT, PAYLOAD_VERSION, len(parts[0]), len(parts[1]), len(parts[2])
    )
    return header + b"".join(parts)


class DecryptionError(RuntimeError):
    """Raised when an encrypted payload cannot be decrypted / parsed.

    EX-P2-6 (`_finance_plugin_audit_extended_report.md` §4.2): the
    plugin previously caught this kind of failure inside read paths
    and silently returned the raw blob / empty dicts.  The new
    contract is to surface decryption failures so callers can
    decide whether to refuse the read (default) or to opt in to
    corrupted-data inspection via an explicit query flag.
    """


def unpack_payload(km: KeyManager, blob: bytes) -> dict[str, dict[str, Any]]:
    """Inverse of :func:`pack_payload`.

    Returns a dict of ``{"amounts": {...}, "pii": {...}, "docrefs": {...}}``
    where each domain dict is empty if the row had no value for that domain.
    """
    if not blob:
        return {"amounts": {}, "pii": {}, "docrefs": {}}
    if not km.is_enabled():
        raise KeyManagerNotReady(
            "KeyManager must be unlocked before unpack_payload()."
        )
    if len(blob) < HEADER_LEN:
        raise ValueError(
            f"encrypted payload too short ({len(blob)}B); not a valid finance-auto blob"
        )

    version, len_a, len_p, len_d = struct.unpack(HEADER_FMT, blob[:HEADER_LEN])
    if version != PAYLOAD_VERSION:
        raise ValueError(
            f"unsupported payload version {version}; expected {PAYLOAD_VERSION}"
        )
    expected = HEADER_LEN + len_a + len_p + len_d
    if len(blob) != expected:
        raise ValueError(
            f"encrypted payload length mismatch: header says {expected} bytes "
            f"but blob is {len(blob)} bytes"
        )

    out: dict[str, dict[str, Any]] = {"amounts": {}, "pii": {}, "docrefs": {}}
    cursor = HEADER_LEN
    for domain, length in (("amounts", len_a), ("pii", len_p), ("docrefs", len_d)):
        if length == 0:
            continue
        ct = blob[cursor : cursor + length]
        cursor += length
        plaintext = km.decrypt(domain, ct)  # type: ignore[arg-type]
        try:
            out[domain] = json.loads(plaintext.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"decrypted {domain} domain is not valid JSON: {exc}"
            ) from exc
    return out


# ---------------------------------------------------------------------------
# Helper for trial_balance_rows / organizations / imports — knows which
# fields of the row dict belong to which domain.
# ---------------------------------------------------------------------------


# Field map per table — used by routes.py and the migration script.
ROW_AMOUNT_FIELDS: tuple[str, ...] = (
    "opening_debit",
    "opening_credit",
    "period_debit",
    "period_credit",
    "closing_debit",
    "closing_credit",
)
ROW_PII_FIELDS: tuple[str, ...] = ("account_name", "aux_text")

ORG_PII_FIELDS: tuple[str, ...] = ("name",)
ORG_DOCREF_FIELDS: tuple[str, ...] = ("erp_source",)

IMPORT_PII_FIELDS: tuple[str, ...] = ("source_file",)

# ParseIssue.original_data may carry account names, customer aux text or
# amounts.  When encryption is enabled we route the *entire* original_data
# dict through the pii domain (small + variable shape — encrypting the whole
# JSON blob is simpler and forces all PII into one place).
PARSE_ISSUE_PII_KEYS: frozenset[str] = frozenset({
    "account_name", "aux_text", "raw_value",
})
PARSE_ISSUE_AMOUNT_KEYS: frozenset[str] = frozenset({
    "opening_debit", "opening_credit",
    "period_debit", "period_credit",
    "closing_debit", "closing_credit",
    "imbalance_delta",
})


def split_row_fields(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Partition a trial_balance_rows dict into ``(amounts, pii)`` sub-dicts.

    Keys not present in either map are left to the caller (they live in
    plaintext columns regardless: row_index, parent_code, full_code …).
    """
    amounts = {k: row[k] for k in ROW_AMOUNT_FIELDS if k in row}
    pii = {k: row[k] for k in ROW_PII_FIELDS if k in row and row[k] is not None}
    return amounts, pii


def split_parse_issue_payload(original: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Partition a ParseIssue.original_data dict into ``(plain, amounts, pii)``.

    Used by the route layer: ``plain`` is stored cleartext in
    ``parse_issues.original_data`` so the UI can render it for free
    (account code, parent code, sheet name); ``amounts`` + ``pii`` are
    packed into a separate encrypted side-blob when the key manager is
    enabled.  The split keeps the index columns searchable while still
    encrypting customer names and figures.
    """
    plain: dict[str, Any] = {}
    amounts: dict[str, Any] = {}
    pii: dict[str, Any] = {}
    for k, v in original.items():
        if k in PARSE_ISSUE_PII_KEYS:
            if v is not None and v != "":
                pii[k] = v
        elif k in PARSE_ISSUE_AMOUNT_KEYS:
            if v is not None and v != 0 and v != 0.0:
                amounts[k] = v
        else:
            plain[k] = v
    return plain, amounts, pii


__all__ = [
    "DecryptionError",
    "HEADER_LEN",
    "IMPORT_PII_FIELDS",
    "ORG_DOCREF_FIELDS",
    "ORG_PII_FIELDS",
    "PARSE_ISSUE_AMOUNT_KEYS",
    "PARSE_ISSUE_PII_KEYS",
    "PAYLOAD_VERSION",
    "ROW_AMOUNT_FIELDS",
    "ROW_PII_FIELDS",
    "pack_payload",
    "split_parse_issue_payload",
    "split_row_fields",
    "unpack_payload",
]
