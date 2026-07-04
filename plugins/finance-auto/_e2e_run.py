"""Standalone end-to-end harness for finance-auto M1 W1.

Boots a minimal FastAPI app that exposes the plugin's router under
``/api/plugins/finance-auto/...`` (the same prefix the host PluginManager
would use), so we can validate the create-org → upload → parse → query loop
with plain curl without booting the rest of OpenAkita.

Usage::

    d:\\OpenAkita\\.venv\\Scripts\\python.exe plugins\\finance-auto\\_e2e_run.py \\
        --port 18901 --db tmp_finance_auto_e2e.sqlite

The host plugin (``plugin.py``) and this harness share the SAME router and
service classes from ``finance_auto_backend/routes.py`` — keeping them in
sync is a single-source-of-truth concern.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from fastapi import FastAPI

# Ensure the plugin dir is on sys.path so ``finance_auto_backend`` resolves.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from finance_auto_backend.routes import build_router_and_service  # noqa: E402

logger = logging.getLogger("finance-auto.e2e")


def create_app(db_path: Path) -> tuple[FastAPI, object]:
    app = FastAPI(title="finance-auto e2e harness")
    router, service, db = build_router_and_service(db_path)
    app.include_router(router, prefix="/api/plugins/finance-auto")

    @app.on_event("startup")
    async def _startup() -> None:
        await db.init()
        logger.info("[finance-auto.e2e] DB ready at %s (WAL)", db.path)
        outcome = await service.auto_unlock_if_configured()
        logger.info("[finance-auto.e2e] encryption auto-unlock: %s", outcome)

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await db.close()
        logger.info("[finance-auto.e2e] DB closed")

    return app, service


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18901)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--db",
        default=str(_HERE / "tmp_finance_auto_e2e.sqlite"),
        help="SQLite path (will be created if missing)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete the SQLite file before starting (fresh demo)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    db_path = Path(args.db).resolve()
    if args.reset:
        for ext in ("", "-wal", "-shm"):
            p = Path(str(db_path) + ext)
            if p.exists():
                p.unlink(missing_ok=True)
                logger.info("removed %s", p)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    app, _ = create_app(db_path)

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
