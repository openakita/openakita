"""C17 Phase E — ChainedJsonlWriter cross-process lock + ParamMutationAuditor
sanitize/chain integration.

Covers:

- ``_reload_last_hash_from_disk`` syncs the in-memory tail with what
  another writer (or another process) committed.
- The full chain stays verifiable after that re-sync.
- ``_sanitize_for_chain`` walks every weird type we care about
  (Path / datetime / Exception / circular dict / deep nesting / set / tuple)
  without raising and the resulting tree is JSON-native.
- ``ParamMutationAuditor.write`` now produces a chain that ``verify_chain``
  accepts — i.e. C10 + C16 + C17 stay glued together.

Multi-process tests use ``subprocess`` to fork *real* OS processes so we
exercise the ``filelock`` codepath end-to-end. We keep them small and
bounded so they don't blow up CI runtime.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from openakita.core.policy_v2 import audit_chain
from openakita.core.policy_v2.audit_chain import (
    GENESIS_HASH,
    ChainedJsonlWriter,
    reset_writers_for_testing,
    verify_chain,
)
from openakita.core.policy_v2.param_mutation_audit import (
    _SANITIZE_MAX_DEPTH,
    ParamMutationAuditor,
    _sanitize_for_chain,
)

# ---------------------------------------------------------------------------
# _reload_last_hash_from_disk + cross-process lock semantics
# ---------------------------------------------------------------------------


class TestReloadLastHash:
    def setup_method(self) -> None:
        reset_writers_for_testing()

    def teardown_method(self) -> None:
        reset_writers_for_testing()

    def test_reload_picks_up_external_append(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        w = ChainedJsonlWriter(path)
        # Initial state — empty file.
        assert w.last_hash == GENESIS_HASH

        # Simulate another process: append a full line directly with a
        # valid chain head computed from our serializer.
        record = {"ts": 1.0, "op": "external"}
        from openakita.core.policy_v2.audit_chain import _compute_row_hash

        enriched = {**record, "prev_hash": GENESIS_HASH}
        external_hash = _compute_row_hash(enriched)
        enriched["row_hash"] = external_hash
        from openakita.core.policy_v2.audit_chain import _canonical_dumps

        with open(path, "a", encoding="utf-8") as fh:
            fh.write(_canonical_dumps(enriched) + "\n")

        # Local writer doesn't know about the external write until we trigger
        # the reload. After a call to ``_reload_last_hash_from_disk`` it
        # should reflect the external head.
        w._reload_last_hash_from_disk()
        assert w.last_hash == external_hash

        # Append via our writer now — must chain off the external head.
        appended = w.append({"ts": 2.0, "op": "local"})
        assert appended["prev_hash"] == external_hash

        # And the whole file verifies.
        res = verify_chain(path)
        assert res.ok, res.reason

    def test_two_writers_same_path_interleave(self, tmp_path: Path) -> None:
        """Two ``ChainedJsonlWriter`` instances appending to the same file
        in alternation produce a single valid chain (no fork)."""
        path = tmp_path / "audit.jsonl"
        # Force two separate writers (no singleton sharing).
        w1 = ChainedJsonlWriter(path)
        w2 = ChainedJsonlWriter(path)
        for i in range(5):
            (w1 if i % 2 == 0 else w2).append({"ts": float(i), "i": i})
        res = verify_chain(path)
        assert res.ok, res.reason
        assert res.total == 5

    def test_no_filelock_falls_back_gracefully(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When filelock isn't available, writer still works in single-process
        mode (process-level Lock only)."""
        monkeypatch.setattr(audit_chain, "_HAS_FILELOCK", False)
        path = tmp_path / "audit.jsonl"
        w = ChainedJsonlWriter(path)
        # Reset the filelock attribute that the constructor populated *before*
        # we patched _HAS_FILELOCK — keeps the test honest.
        w._filelock = None
        for i in range(3):
            w.append({"ts": float(i)})
        res = verify_chain(path)
        assert res.ok, res.reason
        assert res.total == 3


# ---------------------------------------------------------------------------
# Real multi-process append exercising filelock
# ---------------------------------------------------------------------------


_WORKER_SOURCE = """
import json
import sys
from pathlib import Path
from openakita.core.policy_v2.audit_chain import (
    ChainedJsonlWriter, reset_writers_for_testing
)

path = Path(sys.argv[1])
n = int(sys.argv[2])
tag = sys.argv[3]
reset_writers_for_testing()
w = ChainedJsonlWriter(path)
for i in range(n):
    w.append({"ts": float(i), "tag": tag, "i": i})
print("ok", tag, n)
"""


@pytest.mark.skipif(
    sys.platform.startswith("win") and not sys.executable,
    reason="multi-process spawn needs a real python interpreter",
)
class TestMultiProcessAppend:
    def test_two_subprocesses_interleave(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        worker = tmp_path / "worker.py"
        worker.write_text(_WORKER_SOURCE, encoding="utf-8")

        def run(tag: str, n: int) -> subprocess.Popen[str]:
            env = os.environ.copy()
            src_path = str(Path(__file__).resolve().parents[2] / "src")
            env["PYTHONPATH"] = (
                src_path
                if not env.get("PYTHONPATH")
                else f"{src_path}{os.pathsep}{env['PYTHONPATH']}"
            )
            return subprocess.Popen(
                [sys.executable, str(worker), str(path), str(n), tag],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )

        p1 = run("A", 10)
        p2 = run("B", 10)
        out1, err1 = p1.communicate(timeout=60)
        out2, err2 = p2.communicate(timeout=60)
        assert p1.returncode == 0, f"worker A failed: {err1}"
        assert p2.returncode == 0, f"worker B failed: {err2}"

        # Whole file must verify with no torn writes.
        res = verify_chain(path)
        assert res.ok, f"verify failed: reason={res.reason} bad_line={res.first_bad_line}"
        assert res.total == 20

        # And both tags are present in the right counts.
        with open(path, encoding="utf-8") as f:
            tags = [json.loads(line)["tag"] for line in f if line.strip()]
        assert tags.count("A") == 10
        assert tags.count("B") == 10


# ---------------------------------------------------------------------------
# _sanitize_for_chain
# ---------------------------------------------------------------------------


class _Weird:
    def __init__(self, x: int) -> None:
        self.x = x

    def __repr__(self) -> str:
        return f"Weird({self.x})"


class TestSanitizeForChain:
    def test_primitives_pass_through(self) -> None:
        assert _sanitize_for_chain(None) is None
        assert _sanitize_for_chain(True) is True
        assert _sanitize_for_chain(42) == 42
        assert _sanitize_for_chain(3.14) == 3.14
        assert _sanitize_for_chain("hello") == "hello"

    def test_path_to_str(self) -> None:
        out = _sanitize_for_chain(Path("/tmp/foo.txt"))
        assert isinstance(out, str)
        assert "foo.txt" in out

    def test_datetime_to_isoformat(self) -> None:
        dt = datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC)
        out = _sanitize_for_chain(dt)
        assert out == "2026-05-14T12:00:00+00:00"

    def test_set_and_tuple_to_list(self) -> None:
        out = _sanitize_for_chain({1, 2, 3})
        assert isinstance(out, list)
        assert sorted(out) == [1, 2, 3]
        out2 = _sanitize_for_chain((4, 5, 6))
        assert isinstance(out2, list)
        assert out2 == [4, 5, 6]

    def test_exception_stringified(self) -> None:
        out = _sanitize_for_chain(ValueError("boom"))
        assert isinstance(out, str)
        assert "ValueError" in out
        assert "boom" in out

    def test_arbitrary_object_repr_fallback(self) -> None:
        out = _sanitize_for_chain(_Weird(5))
        assert isinstance(out, str)
        assert "Weird" in out

    def test_circular_dict_stops_at_max_depth(self) -> None:
        a: dict[str, Any] = {}
        a["self"] = a  # circular
        out = _sanitize_for_chain(a)
        # Walk in and check we hit a truncation stub at some depth.
        depth = 0
        cur: Any = out
        while isinstance(cur, dict) and "self" in cur:
            cur = cur["self"]
            depth += 1
            if depth > _SANITIZE_MAX_DEPTH + 2:
                pytest.fail("sanitize did not bound circular dict")
        assert isinstance(cur, str)
        assert "truncated" in cur

    def test_deeply_nested_list_truncated(self) -> None:
        deep: Any = "leaf"
        for _ in range(_SANITIZE_MAX_DEPTH + 5):
            deep = [deep]
        out = _sanitize_for_chain(deep)
        # Walk in until we hit either a str truncation marker or the leaf.
        cur: Any = out
        count = 0
        while isinstance(cur, list) and cur:
            cur = cur[0]
            count += 1
            if count > _SANITIZE_MAX_DEPTH + 10:
                pytest.fail("sanitize did not bound nesting")
        assert isinstance(cur, str)

    def test_long_string_truncated(self) -> None:
        s = "x" * (audit_chain.__dict__.get("_SANITIZE_MAX_STR_LEN", 8192) or 8192) * 2
        # Import the actual cap from the module under test:
        from openakita.core.policy_v2 import param_mutation_audit as pma

        s = "x" * (pma._SANITIZE_MAX_STR_LEN * 2)
        out = _sanitize_for_chain(s)
        assert isinstance(out, str)
        assert len(out) <= pma._SANITIZE_MAX_STR_LEN + 64
        assert "truncated" in out

    def test_result_is_json_serializable(self) -> None:
        """End-to-end: the sanitized tree round-trips through canonical_dumps
        (which has no ``default=`` fallback)."""
        weird = {
            "path": Path("/tmp"),
            "when": datetime(2026, 5, 14, tzinfo=UTC),
            "tup": (1, 2, 3),
            "set": {"a", "b"},
            "err": KeyError("x"),
            "deep": [{"k": [1, 2]}],
            "obj": _Weird(7),
            "leaf": None,
        }
        sanitized = _sanitize_for_chain(weird)
        # canonical_dumps must succeed without raising.
        from openakita.core.policy_v2.audit_chain import _canonical_dumps

        out = _canonical_dumps(sanitized)
        assert "Weird" in out
        assert "2026-05-14" in out


# ---------------------------------------------------------------------------
# ParamMutationAuditor → ChainedJsonlWriter integration
# ---------------------------------------------------------------------------


class TestParamMutationAuditorChain:
    def setup_method(self) -> None:
        reset_writers_for_testing()

    def teardown_method(self) -> None:
        reset_writers_for_testing()

    def test_write_creates_chained_file(self, tmp_path: Path) -> None:
        from openakita.core.policy_v2.param_mutation_audit import (
            ParamAuditOutcome,
            ParamDiff,
        )

        auditor = ParamMutationAuditor(audit_dir=tmp_path)
        outcome = ParamAuditOutcome(
            diffs=[ParamDiff("a.b", before=1, after=2, op="modify")],
            allowed=True,
            candidate_plugin_ids=["plug.example"],
        )
        auditor.write(
            tool_name="shell",
            outcome=outcome,
            before={"a": {"b": 1}},
            after={"a": {"b": 2}},
        )

        path = auditor.audit_path
        assert path.exists()
        # Single line, with full chain fields populated.
        with open(path, encoding="utf-8") as f:
            lines = f.read().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["row_hash"]
        assert record["prev_hash"] == GENESIS_HASH
        assert record["tool_name"] == "shell"
        assert record["diffs"][0]["path"] == "a.b"

    def test_write_handles_unhashable_value(self, tmp_path: Path) -> None:
        """A non-JSON-native ``before``/``after`` value must not crash the
        write (we sanitize first)."""
        from openakita.core.policy_v2.param_mutation_audit import (
            ParamAuditOutcome,
            ParamDiff,
        )

        auditor = ParamMutationAuditor(audit_dir=tmp_path)
        weird_before = {"err": ValueError("oops"), "p": Path("/etc")}
        weird_after = {"err": RuntimeError("new"), "p": Path("/tmp")}
        outcome = ParamAuditOutcome(
            diffs=[ParamDiff("err", before=weird_before["err"], after=weird_after["err"], op="modify")],
            allowed=False,
            candidate_plugin_ids=[],
        )
        # Must not raise.
        auditor.write(
            tool_name="shell",
            outcome=outcome,
            before=weird_before,
            after=weird_after,
        )
        path = auditor.audit_path
        assert path.exists()
        # And the chain must verify even with unusual content.
        res = verify_chain(path)
        assert res.ok, res.reason

    def test_no_diff_no_write(self, tmp_path: Path) -> None:
        from openakita.core.policy_v2.param_mutation_audit import (
            ParamAuditOutcome,
        )

        auditor = ParamMutationAuditor(audit_dir=tmp_path)
        outcome = ParamAuditOutcome(
            diffs=[], allowed=True, candidate_plugin_ids=[]
        )
        auditor.write(
            tool_name="shell",
            outcome=outcome,
            before={"a": 1},
            after={"a": 1},
        )
        assert not auditor.audit_path.exists()

    def test_multiple_writes_chain_verifies(self, tmp_path: Path) -> None:
        from openakita.core.policy_v2.param_mutation_audit import (
            ParamAuditOutcome,
            ParamDiff,
        )

        auditor = ParamMutationAuditor(audit_dir=tmp_path)
        for i in range(5):
            outcome = ParamAuditOutcome(
                diffs=[ParamDiff(f"x[{i}]", before=i, after=i + 1, op="modify")],
                allowed=True,
                candidate_plugin_ids=["plug.example"],
            )
            auditor.write(
                tool_name="shell",
                outcome=outcome,
                before={"x": i},
                after={"x": i + 1},
            )
        res = verify_chain(auditor.audit_path)
        assert res.ok, res.reason
        assert res.total == 5
