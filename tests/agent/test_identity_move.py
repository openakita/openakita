"""Move-compat tests for ``openakita.agent.identity``.

The full behavioural suite for the Identity loader lives at
``tests/unit/test_identity.py`` and it imports from the legacy path.
We keep that suite as-is — its 17 tests transitively prove the move
is non-breaking. This file adds two cheap structural anchors that
make a re-export regression impossible to miss:

1. The legacy import path yields the **same class object** as the
   new path (so ``isinstance`` checks elsewhere keep working).
2. ``from openakita.core import Identity`` (the lazy
   ``__getattr__`` on the legacy package) still resolves to the
   moved class.
"""

from __future__ import annotations

from openakita.agent.identity import Identity


def test_legacy_path_re_exports_same_class() -> None:
    from openakita.core.identity import Identity as Legacy

    assert Legacy is Identity


def test_lazy_attribute_on_core_package_still_works() -> None:
    from openakita import core

    assert core.Identity is Identity
