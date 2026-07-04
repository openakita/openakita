"""Re-export shim for the persona manager.

Canonical home is :mod:`openakita.agent.persona`. This module exists
solely so legacy import paths (``openakita.core.persona``) resolve to
the *same* class/function objects as the new path. Removing it
without first migrating callers would break:

* ``openakita.core.agent`` (lazy ``from .persona import PersonaManager``)
* ``openakita.core.trait_miner`` (uses ``PERSONA_DIMENSIONS`` /
  ``PersonaTrait`` / ``PersonaManager``)
* tooling under ``tests/unit/test_persona.py``
* Any pickled traits or in-flight memory objects whose
  ``__module__`` is ``openakita.core.persona``.

Phase 8 cleanup will delete this shim once those callers have been
updated.
"""

from openakita.agent.persona import (
    PERSONA_DIMENSIONS,
    MergedPersona,
    PersonaManager,
    PersonaTrait,
    persist_trait_to_memory,
)

__all__ = [
    "PERSONA_DIMENSIONS",
    "MergedPersona",
    "PersonaManager",
    "PersonaTrait",
    "persist_trait_to_memory",
]
