"""Storage backends for the v2 runtime.

Each backend lives in its own module (:mod:`sqlite`, :mod:`memory`,
:mod:`json_file`) so they can own their own dependencies. The
``MemoryCheckpointer`` is re-exported from :mod:`openakita.runtime.checkpoint`
because it is part of the leaf protocol surface.
"""

from __future__ import annotations
