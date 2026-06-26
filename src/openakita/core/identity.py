"""Re-export shim — identity loader moved to ``agent.identity``.

The canonical home of :class:`Identity` is now
:mod:`openakita.agent.identity`, per ADR-0003 and the Phase 2
sub-commit plan in ``docs/revamp/core_audit.md``. This shim keeps
every existing import path working — ``from openakita.core.identity
import Identity``, the lazy attribute exposure in
``openakita/core/__init__.py``, and the ``main.py`` boot path —
until Phase 8 mechanically removes the legacy ``core/`` tree.

Do not add new code here — only re-export symbols that already live in
``openakita.agent.identity`` so legacy import paths keep resolving.
"""

from __future__ import annotations

from openakita.agent.identity import (
    _AGENT_NAME_PLACEHOLDER,
    _HASH_FILE,
    Identity,
    _apply_agent_name_placeholder,
    _file_hash,
    _load_hashes,
    _resolve_bundled_identity_template,
    _save_hashes,
)

__all__ = [
    "Identity",
    "_HASH_FILE",
    "_AGENT_NAME_PLACEHOLDER",
    "_apply_agent_name_placeholder",
    "_resolve_bundled_identity_template",
    "_file_hash",
    "_load_hashes",
    "_save_hashes",
]
