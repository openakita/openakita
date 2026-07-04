"""RC-5 S3: production :class:`SupervisorLLMClient` over the gateway ``LLMClient``.

This is the production-grade promotion of the spike/Q2 ``GatewayLLMClient``
(``_rc5_biz/q2_live/q2_harness.py`` / ``_rc5_biz/gap5_spike/``). The spike's
adapter was a harness convenience; this module is the real seam the
``OrgCommandService.submit`` gray-launch path injects into
:func:`openakita.runtime.supervisor_factory.build_supervisor_for_command`.

Why a separate, narrow adapter (the two-protocol trap, RC-5 §2)
---------------------------------------------------------------
The orchestration brain (:class:`~openakita.runtime.llm_supervisor_brain.LLMSupervisorBrain`)
depends only on the narrow :class:`~openakita.runtime.llm_supervisor_brain.SupervisorLLMClient`
``complete`` protocol -- NOT on the wide gateway ``LLMClient`` /
``agent.brain.Brain`` surfaces. Keeping this adapter thin means the brain
carries zero coupling to the gateway; tests inject a scripted fake, production
injects this.

Design notes
------------
* **No-thinking endpoint lock (cost + JSON stability).** The orchestration
  prompts want pure JSON; thinking-mode chain-of-thought prefixes pollute the
  JSON head and trigger parse retries (see ``_rc5_biz/sprint_s1/s1_report.md``
  §3). We lock onto the dedicated no-thinking endpoint
  (``settings.orgs_supervisor_llm_endpoint``) via a **per-conversation**
  override so the process-wide default model is never clobbered, and pass
  ``enable_thinking=False`` as defence-in-depth. If the endpoint is absent on
  this deployment (the endpoint config is git-ignored runtime state), we log
  once and fall back to default routing with ``enable_thinking=False`` --
  never crashing the submit path.
* **Cancel bridge (RC-4).** ``cancel_event`` is forwarded straight into
  ``LLMClient.chat`` so a user "stop" aborts the in-flight ``httpx`` request
  immediately (validated by the Q2 cancel probe).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING

from openakita.llm.types import Message

if TYPE_CHECKING:  # pragma: no cover -- import-cycle / type-only
    from openakita.llm.client import LLMClient

__all__ = [
    "DEFAULT_SUPERVISOR_LLM_ENDPOINT",
    "GatewaySupervisorLLMClient",
]

logger = logging.getLogger(__name__)

#: The no-thinking endpoint the orchestration brain prefers. Matches the
#: endpoint provisioned in RC-5 sprint S1 (``_endpoint_config_change.md``).
#: A deployment without this endpoint falls back to default routing.
DEFAULT_SUPERVISOR_LLM_ENDPOINT = "dashscope-qwen3.5-plus-nothinking"


class GatewaySupervisorLLMClient:
    """Adapt the gateway :class:`~openakita.llm.client.LLMClient` to the narrow
    ``SupervisorLLMClient`` ``complete`` seam.

    Args:
        client: the gateway LLM client (typically
            :func:`openakita.llm.client.get_default_client`).
        endpoint: preferred no-thinking endpoint name. ``None`` disables the
            endpoint lock (default routing + ``enable_thinking=False``).
        max_tokens: output cap per orchestration call.
        conversation_id: scope for the per-conversation endpoint override so
            the process-wide default model is never mutated. Auto-minted when
            omitted.
    """

    def __init__(
        self,
        client: LLMClient,
        *,
        endpoint: str | None = DEFAULT_SUPERVISOR_LLM_ENDPOINT,
        max_tokens: int = 2048,
        conversation_id: str | None = None,
    ) -> None:
        self._client = client
        self._endpoint = endpoint or None
        self._max_tokens = max_tokens
        self._conversation_id = conversation_id or f"orgsup-{uuid.uuid4().hex[:12]}"
        self._endpoint_locked = False

    def _ensure_endpoint_lock(self) -> None:
        """Best-effort one-time lock onto the no-thinking endpoint.

        Uses a per-conversation override (``conversation_id``) so flipping the
        orchestration model never affects the global default client used by
        chat / agents. Failures (endpoint missing, switch refused) are logged
        once and swallowed -- the call then proceeds on default routing.
        """
        if self._endpoint_locked or not self._endpoint:
            return
        self._endpoint_locked = True  # mark first so we only try once
        try:
            ok, msg = self._client.switch_model(
                self._endpoint,
                conversation_id=self._conversation_id,
                policy="require",
                reason="rc5 org orchestration brain (no-thinking)",
            )
            if not ok:
                logger.warning(
                    "GatewaySupervisorLLMClient: could not lock endpoint %r "
                    "(%s); falling back to default routing with "
                    "enable_thinking=False",
                    self._endpoint,
                    msg,
                )
        except Exception:  # noqa: BLE001 -- endpoint lock must never crash submit
            logger.warning(
                "GatewaySupervisorLLMClient: switch_model(%r) raised; "
                "falling back to default routing",
                self._endpoint,
                exc_info=True,
            )

    async def complete(
        self,
        *,
        role: str,
        system: str,
        user: str,
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        """Run one orchestration LLM call and return the text body.

        ``role`` (``facts`` / ``plan`` / ``progress_ledger``) is accepted for
        the cheap-model-tiering seam but currently routed uniformly to the
        no-thinking endpoint; per-role tiering is the deferred cost follow-up.
        """
        self._ensure_endpoint_lock()
        resp = await self._client.chat(
            messages=[Message(role="user", content=user)],
            system=system,
            max_tokens=self._max_tokens,
            temperature=0.0,
            enable_thinking=False,
            conversation_id=self._conversation_id,
            cancel_event=cancel_event,
        )
        return resp.text
