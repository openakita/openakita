"""Schedule models for the v2 OrgNodeScheduler (P-RC-9 P9.3).

Duplicates v1 ``openakita.orgs.models.NodeSchedule`` /
``ScheduleType`` under the v2 namespace so
``runtime/orgs/node_scheduler.py`` has zero ``openakita.orgs.*``
imports (P-RC-9-PLAN section 0.3 invariant) and v1 can be
deleted wholesale at P9.9. Parity is enforced byte-for-byte
via ``to_dict()`` round-trip; the dataclass type identity
intentionally differs across the namespace split.

ID minting (Nit-1 fold-in from G-RC-9.2): v1
``uuid.uuid4().hex[:12]`` switches to a wall-clock +
monotonic-counter hybrid (``<13-digit ms>_<8-digit
process-monotonic counter>_<6 hex random>``). The wall-clock
prefix keeps cross-restart sortability; the monotonic counter
guarantees strict within-process ordering even if NTP rolls
``time.time()`` backwards mid-burst (Nit-1 of G-RC-9.2
identifies this as a documented risk of the
``project_models.py`` mint strategy). v1 vs v2 parity tests
ignore the ``id`` field (P-RC-9-PLAN section 5.2 NodeScheduler
ignore set).

ADR refs: ADR-0011 (subsystem decomposition; shared model
layer for NodeScheduler), ADR-0012 (orgs/ deletion strategy
-- no shim under v1).
"""

from __future__ import annotations

import itertools
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

__all__ = [
    "NodeSchedule",
    "ScheduleType",
    "new_schedule_id",
    "now_iso",
]


def now_iso() -> str:
    """ISO-8601 UTC timestamp string; mirrors v1 ``_now_iso``."""
    return datetime.now(UTC).isoformat()


# Monotonic in-process counter used as the second component of every
# minted schedule id. Initialised at module load so it survives across
# every ``OrgNodeScheduler`` instance in the same process. The lock is
# held for the increment-and-read step so two threads can never read
# the same value (Nit-1 of G-RC-9.2).
_ID_COUNTER = itertools.count(0)
_ID_LOCK = threading.Lock()


def new_schedule_id() -> str:
    """Mint a fresh schedule id.

    Layout: ``sched_<13-digit ms wall-clock>_<8-digit monotonic
    counter>_<6 hex random>``. Total ~36 chars. The wall-clock
    prefix is loosely chronologically sortable across runs; the
    monotonic counter pins strict within-process ordering even
    if ``time.time()`` rolls backwards on NTP correction (the
    G-RC-9.2 Nit-1 hazard). The 6-hex suffix (24 random bits)
    breaks ties in the rare case two processes mint at the same
    ms and the same counter value (only possible right after a
    process restart).
    """
    ts_ms = int(time.time() * 1000)
    with _ID_LOCK:
        seq = next(_ID_COUNTER)
    rand = secrets.token_hex(3)  # 6 hex chars
    return f"sched_{ts_ms:013d}_{seq:08d}_{rand}"


class ScheduleType(StrEnum):
    """Schedule kind; matches v1 enum values verbatim.

    .. note::

       ``CRON`` is declared for v1 compatibility but neither v1
       nor v2 evaluates the ``NodeSchedule.cron`` field --
       v1 ``OrgNodeScheduler._schedule_loop`` falls through to
       interval timing for any non-``ONCE`` schedule (see
       :func:`openakita.runtime.orgs.node_scheduler.compute_next_fire_time`).
       Cron-string evaluation is intentionally deferred to a
       future P-RC-10+ semantic upgrade; lifting it forward
       here would break the P-RC-9-PLAN section 0.2 parity gate.
    """

    CRON = "cron"
    INTERVAL = "interval"
    ONCE = "once"


@dataclass
class NodeSchedule:
    """v2 node schedule; ``to_dict`` shape matches v1 byte-for-byte."""

    id: str = field(default_factory=new_schedule_id)
    name: str = ""
    schedule_type: ScheduleType = ScheduleType.INTERVAL
    cron: str | None = None
    interval_s: int | None = None
    run_at: str | None = None
    prompt: str = ""
    enabled: bool = True
    report_to: str | None = None
    report_condition: str = "on_issue"
    max_tokens_per_run: int = 2000
    last_run_at: str | None = None
    last_result_summary: str | None = None
    consecutive_clean: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "schedule_type": self.schedule_type.value,
            "cron": self.cron,
            "interval_s": self.interval_s,
            "run_at": self.run_at,
            "prompt": self.prompt,
            "enabled": self.enabled,
            "report_to": self.report_to,
            "report_condition": self.report_condition,
            "max_tokens_per_run": self.max_tokens_per_run,
            "last_run_at": self.last_run_at,
            "last_result_summary": self.last_result_summary,
            "consecutive_clean": self.consecutive_clean,
        }

    @classmethod
    def from_dict(cls, d: dict) -> NodeSchedule:
        d = dict(d)
        if "schedule_type" in d and isinstance(d["schedule_type"], str):
            try:
                d["schedule_type"] = ScheduleType(d["schedule_type"])
            except ValueError:
                d["schedule_type"] = ScheduleType.INTERVAL
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
