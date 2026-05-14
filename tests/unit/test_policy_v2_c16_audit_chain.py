"""C16 Phase C — Audit JSONL hash chain tests.

Covers:

- ``_compute_row_hash`` excludes ``row_hash`` from its own input.
- ``ChainedJsonlWriter`` round-trip: appended records carry ``prev_hash``
  + ``row_hash`` and ``verify_chain`` returns ``ok=True``.
- Legacy prefix: pre-existing rows without ``row_hash`` are surfaced
  separately from tamper.
- Tamper detection: mutate a middle row, verifier reports the exact line.
- Truncated tail recovery: partial trailing line is dropped on writer
  open and reported, not flagged as tamper.
- Per-path singleton: two ``get_writer(same_path)`` instances share the
  same lock + last-hash cursor.
- Threading concurrency: N threads append M lines each; final chain
  verifies and no writes are lost.
- ``AuditLogger`` produces chained rows with top-level ``safety_immune``
  and the nested ``meta.safety_immune_match`` is preserved (backward
  compat).
- ``record_decision`` and ``_append_audit`` (system_tasks) use the
  shared chain writer end-to-end.
- Float ``ts`` is deterministic across round-trips.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from openakita.core.policy_v2 import audit_chain as ac


@pytest.fixture(autouse=True)
def _reset_writers():
    """Each test gets a fresh writer-singleton map."""
    ac.reset_writers_for_testing()
    yield
    ac.reset_writers_for_testing()


# ---------------------------------------------------------------------------
# Hash primitives
# ---------------------------------------------------------------------------


def test_compute_row_hash_excludes_row_hash_field():
    rec = {"a": 1, "b": "x", "prev_hash": ac.GENESIS_HASH}
    expected = ac._compute_row_hash(rec)
    # If row_hash sneaks into the input, function must raise.
    with pytest.raises(ValueError):
        ac._compute_row_hash({**rec, "row_hash": "deadbeef"})
    # Re-computing with same input is stable.
    assert ac._compute_row_hash(rec) == expected


def test_canonical_dumps_is_sort_stable():
    a = ac._canonical_dumps({"b": 2, "a": 1})
    b = ac._canonical_dumps({"a": 1, "b": 2})
    assert a == b == '{"a":1,"b":2}'


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_writer_round_trip(tmp_path: Path):
    audit = tmp_path / "audit.jsonl"
    writer = ac.get_writer(audit)
    e1 = writer.append({"ts": 1.0, "tool": "read_file", "decision": "ALLOW"})
    e2 = writer.append({"ts": 2.0, "tool": "write_file", "decision": "DENY"})

    assert e1["prev_hash"] == ac.GENESIS_HASH
    assert e2["prev_hash"] == e1["row_hash"]
    assert e1["row_hash"] != e2["row_hash"]

    result = ac.verify_chain(audit)
    assert result.ok is True
    assert result.total == 2
    assert result.legacy_prefix_lines == 0
    assert result.first_bad_line is None


def test_writer_persists_to_disk_in_canonical_form(tmp_path: Path):
    audit = tmp_path / "audit.jsonl"
    writer = ac.get_writer(audit)
    writer.append({"a": 1, "b": 2})

    raw = audit.read_text(encoding="utf-8").splitlines()
    assert len(raw) == 1
    parsed = json.loads(raw[0])
    expected = ac._compute_row_hash({k: v for k, v in parsed.items() if k != "row_hash"})
    assert parsed["row_hash"] == expected


# ---------------------------------------------------------------------------
# Legacy prefix
# ---------------------------------------------------------------------------


def test_legacy_prefix_is_surfaced_not_flagged_as_tamper(tmp_path: Path):
    audit = tmp_path / "audit.jsonl"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text(
        '{"ts":1.0,"tool":"old","decision":"ALLOW"}\n'
        '{"ts":2.0,"tool":"old2","decision":"DENY"}\n'
        '{"ts":3.0,"tool":"old3","decision":"ALLOW"}\n',
        encoding="utf-8",
    )

    writer = ac.get_writer(audit)
    writer.append({"ts": 4.0, "tool": "chained", "decision": "ALLOW"})
    writer.append({"ts": 5.0, "tool": "chained2", "decision": "DENY"})

    result = ac.verify_chain(audit)
    assert result.ok is True
    assert result.total == 5
    assert result.legacy_prefix_lines == 3
    assert result.first_bad_line is None


def test_legacy_then_chain_bootstrap_starts_from_genesis(tmp_path: Path):
    audit = tmp_path / "audit.jsonl"
    audit.write_text('{"ts":1.0,"tool":"legacy"}\n', encoding="utf-8")
    writer = ac.get_writer(audit)
    enriched = writer.append({"ts": 2.0, "tool": "first_chained"})
    assert enriched["prev_hash"] == ac.GENESIS_HASH


# ---------------------------------------------------------------------------
# Tamper detection
# ---------------------------------------------------------------------------


def test_tamper_detected_at_exact_line(tmp_path: Path):
    audit = tmp_path / "audit.jsonl"
    writer = ac.get_writer(audit)
    for i in range(10):
        writer.append({"ts": float(i), "tool": f"tool_{i}", "decision": "ALLOW"})

    lines = audit.read_text(encoding="utf-8").splitlines()
    parsed = [json.loads(line) for line in lines]
    parsed[4]["decision"] = "DENY"
    # We do NOT recompute the row_hash — that's the whole point.
    audit.write_text(
        "\n".join(json.dumps(p, sort_keys=True, separators=(",", ":")) for p in parsed)
        + "\n",
        encoding="utf-8",
    )

    result = ac.verify_chain(audit)
    assert result.ok is False
    assert result.first_bad_line == 5  # 1-indexed
    assert "row_hash mismatch" in (result.reason or "")


def test_tamper_on_prev_hash_caught(tmp_path: Path):
    audit = tmp_path / "audit.jsonl"
    writer = ac.get_writer(audit)
    for i in range(5):
        writer.append({"ts": float(i), "tool": f"t{i}"})

    lines = audit.read_text(encoding="utf-8").splitlines()
    parsed = [json.loads(line) for line in lines]
    parsed[2]["prev_hash"] = "f" * 64
    audit.write_text(
        "\n".join(json.dumps(p, sort_keys=True, separators=(",", ":")) for p in parsed)
        + "\n",
        encoding="utf-8",
    )

    result = ac.verify_chain(audit)
    assert result.ok is False
    assert result.first_bad_line == 3


# ---------------------------------------------------------------------------
# Truncated tail
# ---------------------------------------------------------------------------


def test_truncated_tail_recovered_on_open(tmp_path: Path):
    audit = tmp_path / "audit.jsonl"
    writer = ac.get_writer(audit)
    for i in range(5):
        writer.append({"ts": float(i), "tool": f"t{i}"})

    # Simulate crash: append partial JSON without newline.
    with audit.open("a", encoding="utf-8") as f:
        f.write('{"ts":99.0,"tool":"par')

    ac.reset_writers_for_testing()
    new_writer = ac.get_writer(audit)
    assert new_writer.truncated_tail_recovered is True

    # File should now end cleanly with the last full line.
    raw = audit.read_text(encoding="utf-8")
    assert raw.endswith("}\n")

    # Subsequent appends still chain correctly.
    new_writer.append({"ts": 100.0, "tool": "after_recovery"})
    result = ac.verify_chain(audit)
    assert result.ok is True
    assert result.total == 6


# ---------------------------------------------------------------------------
# Singleton map
# ---------------------------------------------------------------------------


def test_get_writer_singleton_per_path(tmp_path: Path):
    audit = tmp_path / "audit.jsonl"
    a = ac.get_writer(audit)
    b = ac.get_writer(audit)
    assert a is b

    a.append({"ts": 1.0, "tool": "x"})
    b.append({"ts": 2.0, "tool": "y"})
    result = ac.verify_chain(audit)
    assert result.ok is True
    assert result.total == 2


def test_get_writer_singleton_resolves_path(tmp_path: Path):
    audit = tmp_path / "audit.jsonl"
    a = ac.get_writer(audit)
    b = ac.get_writer(str(audit))
    assert a is b


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


def test_threaded_appends_preserve_chain(tmp_path: Path):
    audit = tmp_path / "audit.jsonl"
    writer = ac.get_writer(audit)

    THREADS = 8
    PER = 50

    def _worker(tid: int) -> None:
        for i in range(PER):
            writer.append({"ts": time.time(), "tid": tid, "i": i})

    threads = [threading.Thread(target=_worker, args=(t,)) for t in range(THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    result = ac.verify_chain(audit)
    assert result.ok is True
    assert result.total == THREADS * PER


# ---------------------------------------------------------------------------
# AuditLogger integration
# ---------------------------------------------------------------------------


def test_audit_logger_writes_chained_rows_and_promotes_safety_immune(
    tmp_path: Path, monkeypatch
):
    from openakita.core import audit_logger as al

    audit_path = tmp_path / "policy_decisions.jsonl"
    logger = al.AuditLogger(path=str(audit_path), enabled=True, include_chain=True)
    logger.log(
        tool_name="rm",
        decision="CONFIRM",
        reason="safety_immune match",
        metadata={"safety_immune_match": True},
    )

    result = ac.verify_chain(audit_path)
    assert result.ok is True
    assert result.total == 1

    obj = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert obj["safety_immune"] is True
    assert obj["meta"]["safety_immune_match"] is True
    assert "row_hash" in obj
    assert "prev_hash" in obj


def test_audit_logger_include_chain_false_skips_chain(tmp_path: Path):
    from openakita.core import audit_logger as al

    audit_path = tmp_path / "policy_decisions.jsonl"
    logger = al.AuditLogger(path=str(audit_path), enabled=True, include_chain=False)
    logger.log(
        tool_name="rm",
        decision="ALLOW",
        reason="ok",
        metadata={"safety_immune_match": False},
    )

    obj = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert "row_hash" not in obj
    assert "prev_hash" not in obj
    # safety_immune still promoted even without chain
    assert obj.get("safety_immune") is False


# ---------------------------------------------------------------------------
# evolution_window + system_tasks integration
# ---------------------------------------------------------------------------


def test_record_decision_writes_chained_row(tmp_path: Path):
    from openakita.core.policy_v2 import evolution_window as ew

    audit = tmp_path / "evolution_decisions.jsonl"
    ew.record_decision(
        fix_id="fix-001",
        audit_path=audit,
        decision_record={"tool": "edit_file", "decision": "ALLOW"},
    )
    result = ac.verify_chain(audit)
    assert result.ok is True
    assert result.total == 1
    obj = json.loads(audit.read_text(encoding="utf-8").splitlines()[0])
    assert obj["fix_id"] == "fix-001"
    assert "row_hash" in obj


def test_system_tasks_append_audit_writes_chained_row(tmp_path: Path):
    from openakita.core.policy_v2 import system_tasks as st

    audit = tmp_path / "system_tasks.jsonl"
    st._append_audit(audit, {"type": "system_task_bypass_start", "task_id": "rotate_token"})
    result = ac.verify_chain(audit)
    assert result.ok is True
    assert result.total == 1


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_float_ts_round_trip_deterministic(tmp_path: Path):
    audit = tmp_path / "audit.jsonl"
    writer = ac.get_writer(audit)
    writer.append({"ts": 1747200000.123456, "tool": "x"})
    # Verifier independently re-canonicalises and re-hashes; should match.
    result = ac.verify_chain(audit)
    assert result.ok is True
