"""Public re-export shim for ``openakita.orgs.tool_categories``.

Preserves the original v1 public import path after P-RC-11 P11.1 re-instated
the module body as the private shard ``openakita.orgs._runtime_tool_categories``
(charter R-11-2 option (b); recon section 1.4 step 2). The 4 known callers in
src/openakita/ keep importing ``openakita.orgs.tool_categories.<symbol>`` via
this shim.
"""
from ._runtime_tool_categories import *  # noqa: F401,F403
