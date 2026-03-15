"""MultiAgentAdapter: merges multiple agent SSE streams into one."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

_SENTINEL = object()


class MultiAgentAdapter:
    """Merges N agent streams into a single output stream.

    - Injects agent_header events on agent switch.
    - Error isolation: one agent failure does not kill others.
    - Preserves event ordering within each agent.
    """

    async def merge_streams(
        self,
        agent_streams: dict[str, tuple[dict, AsyncIterator[dict]]],
    ) -> AsyncIterator[dict]:
        """Merge multiple (agent_id -> (meta, stream)) into one output.

        Args:
            agent_streams: {agent_id: (agent_meta, refined_event_stream)}
        """
        queue: asyncio.Queue = asyncio.Queue()
        last_agent_id: str | None = None

        async def _feed(agent_id: str, meta: dict, stream: AsyncIterator[dict]):
            """Consume one agent stream, push events into shared queue."""
            try:
                async for event in stream:
                    event["agent_id"] = agent_id
                    await queue.put((agent_id, meta, event))
            except Exception as e:
                logger.error(f"[MultiAgent] Agent {agent_id} error: {e}")
                await queue.put((agent_id, meta, {
                    "type": "error",
                    "message": f"Agent {meta.get('name', agent_id)} failed: {e}",
                    "code": "agent_error",
                    "agent_id": agent_id,
                }))
            finally:
                await queue.put(_SENTINEL)

        # Start all feeders
        tasks = []
        for agent_id, (meta, stream) in agent_streams.items():
            tasks.append(asyncio.create_task(_feed(agent_id, meta, stream)))

        # Emit merged events
        done_count = 0
        while done_count < len(agent_streams):
            item = await queue.get()
            if item is _SENTINEL:
                done_count += 1
                continue
            agent_id, meta, event = item

            # Inject agent_header on agent switch
            if agent_id != last_agent_id:
                yield {
                    "type": "agent_header",
                    "agent_id": agent_id,
                    "agent_name": meta.get("name", agent_id),
                    "agent_description": meta.get("description", ""),
                }
                last_agent_id = agent_id

            # Skip per-agent done events (we emit a global done)
            if event.get("type") == "done":
                continue

            yield event

        # Cleanup
        for t in tasks:
            if not t.done():
                t.cancel()
