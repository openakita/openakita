"""Re-export shim — UI confirm bus moved to ``agent.ui_confirm_bus``.

The canonical home of :class:`UIConfirmBus`, :func:`get_ui_confirm_bus`,
and :func:`reset_ui_confirm_bus` is now
:mod:`openakita.agent.ui_confirm_bus` per ADR-0003 and the Phase 2
sub-commit plan in ``docs/revamp/core_audit.md``.

This shim preserves the legacy import path until Phase 8
mechanical cleanup so the following call sites keep working:

* :mod:`openakita.api.server`
* :mod:`openakita.api.routes.config`
* :mod:`openakita.api.routes.sessions`
* The C13 / C17 / C18 test suites under :mod:`tests.unit.test_*`

Do not add new code here.
"""

from __future__ import annotations

from openakita.agent.ui_confirm_bus import (
    UIConfirmBus,
    get_ui_confirm_bus,
    reset_ui_confirm_bus,
)

__all__ = [
    "UIConfirmBus",
    "get_ui_confirm_bus",
    "reset_ui_confirm_bus",
]
