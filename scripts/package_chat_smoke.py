#!/usr/bin/env python3
"""Run a minimal /api/chat request against source copied into a backend bundle."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock


async def _run(internal_dir: Path) -> None:
    sys.path.insert(0, str(internal_dir))

    from httpx import ASGITransport, AsyncClient

    import openakita
    from openakita.api.routes import chat as chat_routes
    from openakita.api.server import create_app

    imported_from = Path(openakita.__file__).resolve()
    if not imported_from.is_relative_to(internal_dir):
        raise RuntimeError(f"openakita imported from {imported_from}, not bundle {internal_dir}")
    if internal_dir.name != "src":
        import requests_toolbelt

        bundled_toolbelt = internal_dir / "requests_toolbelt"
        toolbelt_from = Path(requests_toolbelt.__file__).resolve()
        if not bundled_toolbelt.is_dir():
            raise RuntimeError(f"bundled requests_toolbelt missing from {internal_dir}")
        if not toolbelt_from.is_relative_to(internal_dir):
            raise RuntimeError(
                f"requests_toolbelt imported from {toolbelt_from}, not bundle {internal_dir}"
            )

    agent = MagicMock()
    agent.initialized = True
    agent._initialized = True
    agent.state.has_active_task = False
    agent.state.is_task_cancelled = False
    agent.brain.model = "package-smoke-model"
    agent.settings.max_iterations = 1
    agent.session_manager = None
    agent.insert_user_message = AsyncMock(return_value=True)

    async def fake_stream(*args, **kwargs):
        yield {"type": "text_delta", "content": "package smoke ok"}
        yield {"type": "done"}

    agent.chat_with_session_stream = fake_stream
    agent.chat_with_session = AsyncMock(return_value="package smoke ok")
    chat_routes._chat_endpoint_names = lambda: {"package-smoke"}
    chat_routes._resolve_agent = lambda value: value

    app = create_app(agent=agent, shutdown_event=asyncio.Event())
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://127.0.0.1",
    ) as client:
        response = await client.post(
            "/api/chat",
            json={"message": "package smoke", "conversation_id": "package-smoke"},
        )

    if response.status_code != 200:
        raise RuntimeError(f"POST /api/chat returned {response.status_code}: {response.text[:500]}")
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" not in content_type:
        raise RuntimeError(f"POST /api/chat returned unexpected content type {content_type!r}")
    if "package smoke ok" not in response.text:
        raise RuntimeError("POST /api/chat SSE response did not contain the mock agent output")
    print(f"[OK] bundled POST /api/chat smoke passed ({response.status_code})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--internal-dir", required=True)
    args = parser.parse_args()
    asyncio.run(_run(Path(args.internal_dir).resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
