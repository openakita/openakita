"""tests/api/test_server_app_wiring.py

Regression guard for **F-1** (P-RC-9 post-closure smoke; round RT001).

P-RC-9 P9.6 made ``OrgRuntime.__init__`` keyword-only with three required
``Protocol`` parameters (``lookup`` / ``persistence`` / ``lifecycle_emitter``).
P-RC-9 P9.4 made ``OrgCommandService.__init__`` keyword-only after the
leading ``runtime`` arg.  The composition root in ``api/server.py`` was
left on the v1 positional call convention, which made ``openakita serve``
fail with ``TypeError`` at app construction.

This test asserts that:
1. ``create_app()`` succeeds with all-default arguments.
2. ``app.state.org_runtime`` is an ``OrgRuntime`` instance.
3. ``app.state.org_command_service`` is an ``OrgCommandService`` instance.
4. The runtime's wired ``lookup`` / ``persistence`` / ``lifecycle_emitter``
   match the OrgManager-owned siblings (composition-root invariant).
"""

from __future__ import annotations

import pytest

from openakita.api.server import create_app
from openakita.runtime.orgs.command_service import OrgCommandService
from openakita.runtime.orgs.manager import OrgManager
from openakita.runtime.orgs.runtime import OrgRuntime


@pytest.fixture()
def app():
    """Build the FastAPI app via the composition root; no agent / IM stack."""
    return create_app()


def test_create_app_constructs_org_runtime_via_v2_keyword_only_di(app):
    """RT001 -- the v2 ``OrgRuntime`` keyword-only DI must succeed.

    Pre-fix this raised
    ``TypeError: OrgRuntime.__init__() takes 1 positional argument but 2 were given``
    because ``api/server.py`` still called ``OrgRuntime(org_manager)``
    using the v1 positional convention.
    """
    org_manager = app.state.org_manager
    org_runtime = app.state.org_runtime

    assert isinstance(org_manager, OrgManager)
    assert isinstance(org_runtime, OrgRuntime)

    # Composition-root invariant: the runtime's three DI Protocols are
    # the OrgManager-owned siblings (so ``OrgRuntime`` sees the same
    # state that the REST routes operating via ``OrgManager`` see).
    assert org_runtime._lookup is org_manager
    assert org_runtime._persistence is org_manager._persistence
    assert org_runtime._lifecycle_emitter is org_manager._lifecycle


def test_create_app_constructs_command_service_via_v2_keyword_only_di(app):
    """RT001 -- the v2 ``OrgCommandService`` keyword-only DI must succeed.

    Pre-fix this raised
    ``TypeError: OrgCommandService.__init__() takes 2 positional arguments but 3 were given``
    because ``api/server.py`` still called
    ``OrgCommandService(org_runtime, session_manager)`` using the v1
    positional convention.
    """
    cs = app.state.org_command_service
    assert isinstance(cs, OrgCommandService)
    # Runtime is the composition root's runtime
    assert cs._runtime is app.state.org_runtime
