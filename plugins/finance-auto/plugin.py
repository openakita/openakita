# ruff: noqa: N999
"""finance-auto plugin entry (M1 W1).

Lifecycle:

* ``on_load`` — open the per-plugin SQLite (WAL), build the FastAPI router and
  hand it to the host via ``api.register_api_routes``.
* ``on_unload`` — close the SQLite handle so the file is unlocked before the
  plugin directory is rmtree'd on reinstall (Windows requirement; see
  ``fin-pulse`` plugin for the equivalent dance).

The plugin keeps **all** its code in ``finance_auto_backend/`` so the
end-to-end harness (``_e2e_run.py``) can import the same router without
booting the host.  ``plugin.py`` itself only wires the lifecycle.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from openakita.plugins.api import PluginAPI, PluginBase

logger = logging.getLogger(__name__)

PLUGIN_ID = "finance-auto"
PLUGIN_VERSION = "1.0.0-rc1"


class Plugin(PluginBase):
    """Plugin entry point — see module docstring for the lifecycle shape."""

    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        self._db: Any | None = None
        self._service: Any | None = None
        self._init_task: asyncio.Task[Any] | None = None

        # Lazy import so a stale ``data/plugins`` copy without
        # ``finance_auto_backend/`` does not blow up the host's loader.
        try:
            from finance_auto_backend.db import FinanceAutoDB
            from finance_auto_backend.routes import FinanceAutoService, build_router
        except Exception as exc:  # noqa: BLE001 — defensive plugin boundary
            api.log(
                f"finance-auto: backend import failed ({exc!r}); plugin loaded "
                "with NO routes registered. Run `openakita plugins reseed --apply`.",
                "error",
            )
            return

        host_data_dir = api.get_data_dir()
        if host_data_dir is None:
            api.log(
                "finance-auto: data.own permission missing — plugin runs "
                "without SQLite. Grant data.own and reload.",
                "error",
            )
            return
        db_path = Path(host_data_dir) / "finance_auto.sqlite"

        self._db = FinanceAutoDB(db_path)
        self._service = FinanceAutoService(self._db)

        # Wire the host LLM (OpenAkita Brain) into the AI scenarios. The host
        # attaches its Brain *after* plugins load, so we keep the PluginAPI
        # handle and resolve the brain lazily per request (see
        # ``FinanceAutoService.get_host_brain``). With ``brain.access`` granted
        # the AI scenarios reuse the host's configured model/provider; without
        # it they stay on the offline MockLLMResponder.
        self._service.plugin_api = api
        ai_backend = (
            "host-LLM (brain.access)"
            if api.has_permission("brain.access")
            else "mock (offline)"
        )

        router = build_router(self._service)
        api.register_api_routes(router)

        self._init_task = api.spawn_task(self._async_init(), name=f"{PLUGIN_ID}:db-init")

        route_count = len(getattr(router, "routes", []))
        api.log(
            f"finance-auto v{PLUGIN_VERSION} loaded — {route_count} routes "
            f"registered, SQLite path={db_path}, AI backend={ai_backend}"
        )

    async def _async_init(self) -> None:
        if self._db is None:
            return
        try:
            await self._db.init()
            logger.info(
                "finance-auto: SQLite ready (WAL); journal_mode=%s",
                await self._db.journal_mode(),
            )
            if self._service is not None:
                outcome = await self._service.auto_unlock_if_configured()
                logger.info("finance-auto: encryption auto-unlock: %s", outcome)
        except Exception as exc:  # noqa: BLE001 — top-level bootstrap
            logger.exception("finance-auto: SQLite init failed: %s", exc)
            raise

    async def on_unload(self) -> None:
        if self._init_task is not None and not self._init_task.done():
            self._init_task.cancel()
            try:
                await self._init_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001
                logger.warning("finance-auto: init task drain error: %s", exc)
        if self._db is not None:
            try:
                await self._db.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("finance-auto: SQLite close error: %s", exc)
