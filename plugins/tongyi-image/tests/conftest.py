"""Per-plugin test bootstrap: keep modules isolated from sibling plugins.

Several plugins (tongyi-image / video-translator / poster-maker / ...) ship
top-level modules with the SAME name (``task_manager``, ``providers``,
``templates`` ...).  When pytest collects across plugin trees, Python's
import cache happily returns the first one it loaded — leading to
``ImportError: cannot import name 'TaskManager'`` on the second plugin.

We invalidate the caches at conftest-load time so each plugin gets a clean
import surface (the matching ``plugins/*/tests/conftest.py`` files do the
same).  This must mirror the namespace used by the plugin under test.
"""
import sys
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

for _m in ("providers", "highlight_engine", "subtitle_engine", "studio_engine",
          "poster_engine", "translator_engine", "templates", "task_manager",
          "storyboard_engine",
          "tongyi_task_manager", "tongyi_prompt_optimizer",
          "tongyi_dashscope_client", "tongyi_models"):
    sys.modules.pop(_m, None)


# ── shared dashscope client test helpers ─────────────────────────────


async def make_dashscope_client(handler, api_key: str = "sk-test"):
    """Build a DashScopeClient whose ``_client`` is wired to a stub
    ``httpx.MockTransport`` instead of the real DashScope API.

    Each test owns its own client + handler — never share across tests
    because httpx connection pools are not safe to clone and our handler
    typically captures per-test state (request log, response overrides).

    Caller is responsible for ``await client.close()`` in a finally block.
    """
    import httpx
    from tongyi_dashscope_client import DashScopeClient

    client = DashScopeClient(api_key)
    # Discard the real network client created in __init__ before we replace
    # it; otherwise the original AsyncClient leaks an open socket pool.
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url=client._base_url,
        timeout=httpx.Timeout(120, connect=15),
        headers=client._make_headers(),
        transport=httpx.MockTransport(handler),
    )
    return client
