"""AI endpoint router — local-first / cloud-fallback (v0.2 Part 2 §5).

The plugin doesn't talk to provider APIs directly; it borrows
OpenAkita's already-instantiated ``LLMClient`` (whichever the host has
loaded) so users get the same model picker / API-key UX they're used
to.  Our job is ONLY to:

1. inspect every endpoint the host has registered;
2. classify each one as local (Ollama / LM Studio / vLLM / LocalAI /
   any ``localhost`` / ``127.0.0.1`` base URL) or cloud, using
   ``openakita.llm.types.is_local_endpoint_config`` (battle-tested);
3. return a routing decision ``(endpoint_name, is_local)`` per call;
4. expose a ``MockLLMResponder`` so unit tests + the acceptance script
   can run without a real LLM.

Per the v0.2 design, sensitivity rules are layered on top:

* ``raw`` payloads only ride a local endpoint unless the user has
  explicitly checked "send original to cloud" inside the consent dialog;
* ``aggregated`` and ``metadata`` can ride either, with the local one
  preferred when ``prefer_local_llm`` is true (default).

The plugin does not currently re-implement OpenAkita's failover /
retry logic — it just calls into the host LLMClient (or the mock).
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, Protocol

from .desensitizer import SensitivityLevel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# EX-P2-8 — retry / backoff tunables and helpers
# ---------------------------------------------------------------------------

#: Default number of retry attempts on top of the initial call.  Three
#: retries means up to four total calls (1 + 3).  Override via
#: ``OPENAKITA_FINANCE_AUTO_LLM_RETRIES``.
DEFAULT_LLM_RETRIES = 3
LLM_RETRIES_ENV = "OPENAKITA_FINANCE_AUTO_LLM_RETRIES"

#: Initial backoff in seconds.  Successive retries multiply by 2:
#: 1s, 2s, 4s, ...  Override via ``OPENAKITA_FINANCE_AUTO_LLM_BACKOFF_BASE``.
DEFAULT_LLM_BACKOFF_BASE_SEC = 1.0
LLM_BACKOFF_BASE_ENV = "OPENAKITA_FINANCE_AUTO_LLM_BACKOFF_BASE"

#: Maximum amount of random jitter (in seconds) added on top of the
#: deterministic backoff to spread out concurrent retriers.  Override
#: via ``OPENAKITA_FINANCE_AUTO_LLM_BACKOFF_JITTER``.
DEFAULT_LLM_BACKOFF_JITTER_SEC = 0.25
LLM_BACKOFF_JITTER_ENV = "OPENAKITA_FINANCE_AUTO_LLM_BACKOFF_JITTER"

#: Substrings (case-insensitive) that mark a transient / retryable
#: failure.  We keep this conservative — only true network / rate-limit
#: signals belong here; 4xx invalid-request bugs should fail loud and
#: fast.
_RETRYABLE_SUBSTRINGS: tuple[str, ...] = (
    "timeout", "timed out", "temporarily unavailable",
    "rate limit", "rate_limit", "ratelimited",
    "connection reset", "connection refused", "connection aborted",
    "service unavailable", "bad gateway", "gateway timeout",
    "internal server error",
    "503", "502", "504", "429", "500",
    "remote disconnected", "remote end closed",
)


def _resolve_int(env: str, default: int) -> int:
    raw = os.environ.get(env)
    if raw is None:
        return default
    try:
        n = int(raw)
    except ValueError:
        return default
    return max(0, n)


def _resolve_float(env: str, default: float) -> float:
    raw = os.environ.get(env)
    if raw is None:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


def is_retryable_llm_error(exc: BaseException) -> bool:
    """Heuristic classifier.  Returns ``True`` for transient errors —
    5xx, timeout, rate-limit, connection-reset.  4xx and ValueErrors
    are deemed permanent and short-circuit the retry loop.

    Exporting this so callers can build their own retry loops on top
    of ``FinanceAIRouter`` if they need to without copying the regex.
    """
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True
    msg = (str(exc) or "").lower()
    if not msg:
        return False
    # Explicit 4xx (not 429) is permanent.
    for code in ("400", "401", "403", "404", "405", "409", "422"):
        if code in msg and "429" not in msg:
            return False
    return any(token in msg for token in _RETRYABLE_SUBSTRINGS)


# ---------------------------------------------------------------------------
# Public response shape
# ---------------------------------------------------------------------------


@dataclass
class LLMResponse:
    """Standardised response envelope returned by the router.

    The ``model_id`` / ``provider`` strings echo back what the underlying
    LLMClient picked so the audit log can attribute the call.  ``raw``
    keeps the full provider response for debugging — never persisted.
    """

    text: str
    tokens_prompt: int = 0
    tokens_completion: int = 0
    model_id: str = ""
    provider: str = ""
    is_local: bool = False
    duration_ms: int = 0
    raw: Any = None


# ---------------------------------------------------------------------------
# LLMClient adapter Protocol
# ---------------------------------------------------------------------------


class LLMResponder(Protocol):
    """Minimal interface the router needs.

    OpenAkita's ``LLMClient`` already exposes ``async def chat(...)``;
    we wrap it with a small adapter so the router can stay decoupled
    from the host's ever-evolving API surface.
    """

    async def complete(
        self,
        *,
        prompt: str,
        endpoint_name: str,
        sensitivity_level: SensitivityLevel,
        scenario_id: str = "",
    ) -> LLMResponse:
        ...


# ---------------------------------------------------------------------------
# Mock responder (testing + acceptance)
# ---------------------------------------------------------------------------


class MockLLMResponder:
    """Deterministic fake.  Useful when:

    * pytest runs without Ollama / LM Studio installed;
    * the M2 acceptance script wants to round-trip every scenario
      without burning real tokens.

    A consumer can override the response by setting
    ``MockLLMResponder.canned_responses[(scenario_id, sensitivity)] = "..."``
    or by passing a ``responder_fn`` for full programmatic control.
    """

    def __init__(
        self,
        *,
        responder_fn: (
            Callable[[str, SensitivityLevel, str], Awaitable[str] | str] | None
        ) = None,
        endpoint_name: str = "mock",
        provider: str = "mock",
        is_local: bool = True,
    ) -> None:
        self.responder_fn = responder_fn
        self.endpoint_name = endpoint_name
        self.provider = provider
        self.is_local = is_local
        self.canned_responses: dict[tuple[str, SensitivityLevel], str] = {}
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        *,
        prompt: str,
        endpoint_name: str,
        sensitivity_level: SensitivityLevel,
        scenario_id: str = "",
    ) -> LLMResponse:
        scenario = scenario_id or (
            endpoint_name.split(":", 1)[-1] if ":" in endpoint_name else ""
        )
        key = (scenario, sensitivity_level)
        if self.responder_fn is not None:
            result = self.responder_fn(prompt, sensitivity_level, scenario)
            text = await result if hasattr(result, "__await__") else result
        elif key in self.canned_responses:
            text = self.canned_responses[key]
        else:
            text = (
                f'{{"mock": true, "scenario": "{scenario}", '
                f'"prompt_chars": {len(prompt)}}}'
            )
        self.calls.append(
            {
                "endpoint_name": endpoint_name,
                "scenario": scenario,
                "sensitivity_level": sensitivity_level,
                "prompt_len": len(prompt),
            }
        )
        return LLMResponse(
            text=text,
            tokens_prompt=max(1, len(prompt) // 4),
            tokens_completion=max(1, len(text) // 4),
            model_id=f"{self.provider}/mock-finance",
            provider=self.provider,
            is_local=self.is_local,
            duration_ms=0,
            raw=None,
        )


# ---------------------------------------------------------------------------
# Host-brain responder — routes completions through OpenAkita's own Brain
# ---------------------------------------------------------------------------


class HostBrainResponder:
    """Adapter that satisfies :class:`LLMResponder` by calling the host's
    ``Brain.think_lightweight`` one-shot completion.

    This is how the plugin reuses OpenAkita's already-configured LLM
    provider (the same model picker / API keys the user set up in the
    host) instead of asking the user to wire a second set of credentials
    inside the plugin. The host grants access via the ``brain.access``
    permission; ``plugin.py`` fetches the brain with ``api.get_brain()``
    and hands it to :class:`FinanceAutoService`. When the permission is
    absent the service keeps a ``None`` brain and the scenarios fall back
    to :class:`MockLLMResponder` (so the offline acceptance suite is
    unaffected).
    """

    def __init__(self, brain: Any) -> None:
        self._brain = brain

    @staticmethod
    def _extract_text(response: Any) -> str:
        if isinstance(response, str):
            return response
        for attr in ("content", "text"):
            value = getattr(response, attr, None)
            if value:
                return str(value)
        if isinstance(response, dict):
            return str(response.get("content") or response.get("text") or "")
        return str(response or "")

    async def complete(
        self,
        *,
        prompt: str,
        endpoint_name: str,
        sensitivity_level: SensitivityLevel,
        scenario_id: str = "",
    ) -> LLMResponse:
        started = time.perf_counter()
        system = (
            "You are a finance/accounting assistant embedded in the "
            "OpenAkita finance-auto plugin. Follow the task instructions "
            "in the user message exactly. When asked for JSON, return "
            "strict JSON with no commentary."
        )
        response = await self._brain.think_lightweight(
            prompt=prompt,
            system=system,
            max_tokens=2048,
        )
        text = self._extract_text(response)
        model_id = ""
        provider = ""
        try:
            info = self._brain.get_current_endpoint_info()
            if isinstance(info, dict):
                model_id = str(info.get("model") or "")
                provider = str(info.get("name") or "")
        except Exception:  # noqa: BLE001 — endpoint introspection is best-effort
            pass
        return LLMResponse(
            text=text,
            model_id=model_id or "host-brain",
            provider=provider or "openakita-host",
            is_local=False,
            duration_ms=int((time.perf_counter() - started) * 1000),
            raw=None,
        )


# ---------------------------------------------------------------------------
# Endpoint inventory
# ---------------------------------------------------------------------------


@dataclass
class EndpointDescriptor:
    """One row in the endpoint inventory."""

    name: str
    provider: str
    base_url: str = ""
    is_local: bool = False
    model_id: str = ""
    healthy: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


def _is_local_endpoint(provider: str, base_url: str) -> bool:
    """Best-effort: prefer OpenAkita's helper if importable; otherwise
    fall back to a static pattern check.  This keeps the plugin runnable
    when the host's package isn't on the Python path (e.g. acceptance
    script using a stub LLMClient)."""
    try:
        from openakita.llm.types import is_local_endpoint_config

        return bool(is_local_endpoint_config(provider, base_url))
    except Exception:  # noqa: BLE001
        slug = (provider or "").lower()
        if slug in {"local", "localai", "lmstudio", "ollama", "mock"}:
            return True
        url = (base_url or "").lower()
        return any(host in url for host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"))


def collect_endpoints_from_host_client(host_client: Any) -> list[EndpointDescriptor]:
    """Inspect an OpenAkita ``LLMClient`` and return every endpoint it
    knows about.  Tolerates a stub / Mock client by returning an empty
    list — the router's caller is responsible for falling back to the
    ``MockLLMResponder`` in that case.
    """
    out: list[EndpointDescriptor] = []
    if host_client is None:
        return out
    candidates = getattr(host_client, "_endpoints", None)
    if candidates is None:
        candidates = getattr(host_client, "endpoints", None)
    if candidates is None:
        return out
    iterable: list[Any]
    try:
        if isinstance(candidates, dict):
            iterable = list(candidates.values())
        else:
            iterable = list(candidates)
    except Exception:
        return out
    seen: set[str] = set()
    for cand in iterable:
        try:
            name = getattr(cand, "name", None) or getattr(cand, "endpoint_name", "")
            provider = getattr(cand, "provider", "") or getattr(cand, "provider_slug", "")
            base_url = getattr(cand, "base_url", "")
            model_id = getattr(cand, "model", "") or getattr(cand, "model_name", "")
            healthy = bool(getattr(cand, "healthy", True))
        except Exception:  # noqa: BLE001
            continue
        name_str = str(name or "")
        if not name_str or name_str in seen:
            continue
        seen.add(name_str)
        out.append(
            EndpointDescriptor(
                name=name_str,
                provider=str(provider or ""),
                base_url=str(base_url or ""),
                is_local=_is_local_endpoint(str(provider or ""), str(base_url or "")),
                model_id=str(model_id or ""),
                healthy=healthy,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


@dataclass
class RoutingConfig:
    """User-tweakable knobs.  Shipped under ``plugin.json.config`` /
    user YAML.  Defaults match v0.2 Part 2 §5.1.
    """

    prefer_local_llm: bool = True
    forbid_cloud_for_raw: bool = True
    require_consent_dialog: bool = True
    per_scenario_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)


class FinanceAIRouter:
    """Pick an endpoint and execute a completion.

    Lifecycle:

    * ``__init__`` collects endpoints (or accepts a pre-baked list).
    * ``pick_endpoint(scenario, level)`` returns ``(name, is_local)``
      with the local-first / cloud-fallback rules applied.
    * ``complete(scenario, level, prompt)`` runs the call through the
      ``LLMResponder`` adapter (defaults to the mock so the suite is
      offline-safe).

    The router never persists the call — that's the audit module's job
    (Stage 5+).
    """

    def __init__(
        self,
        *,
        responder: LLMResponder | None = None,
        endpoints: list[EndpointDescriptor] | None = None,
        host_client: Any = None,
        config: RoutingConfig | None = None,
    ) -> None:
        self.responder: LLMResponder = responder or MockLLMResponder()
        self.config: RoutingConfig = config or RoutingConfig()
        if endpoints is None:
            endpoints = collect_endpoints_from_host_client(host_client)
        # Deduplicate by name; drop unhealthy.
        seen: dict[str, EndpointDescriptor] = {}
        for ep in endpoints:
            if ep.healthy and ep.name not in seen:
                seen[ep.name] = ep
        self.endpoints: list[EndpointDescriptor] = list(seen.values())

    # ----- introspection helpers used by tests + the AI history page ----

    @property
    def local_endpoints(self) -> list[EndpointDescriptor]:
        return [e for e in self.endpoints if e.is_local]

    @property
    def cloud_endpoints(self) -> list[EndpointDescriptor]:
        return [e for e in self.endpoints if not e.is_local]

    # ----- routing -----------------------------------------------------------

    def pick_endpoint(
        self,
        *,
        scenario_id: str,
        level: SensitivityLevel,
        skip_desensitize: bool = False,
    ) -> tuple[str, bool]:
        """Return ``(endpoint_name, is_local)`` per the v0.2 rules.

        Scenarios can carry an override forcing local-only:
        ``per_scenario_overrides[scenario_id] = {"require_local_only": True}``.
        Raises ``RuntimeError`` when no endpoint satisfies the policy.
        """
        ov = self.config.per_scenario_overrides.get(scenario_id, {})
        require_local = bool(ov.get("require_local_only", False))
        # raw + skip_desensitize means the user explicitly wants the
        # original payload; per v0.2 §2.3 R4 this is allowed only on
        # local endpoints.  forbid_cloud_for_raw also kicks in for raw
        # without skip_desensitize unless the user explicitly opts in.
        if level == "raw":
            if skip_desensitize or self.config.forbid_cloud_for_raw:
                require_local = True

        if self.config.prefer_local_llm or require_local:
            if self.local_endpoints:
                ep = self.local_endpoints[0]
                return ep.name, True
            if require_local:
                raise RuntimeError(
                    f"scenario {scenario_id!r} requires a local endpoint but "
                    "none are available"
                )
        if self.cloud_endpoints:
            ep = self.cloud_endpoints[0]
            return ep.name, False
        # No real endpoints — fall back to the mock so the acceptance
        # script can complete.  We tag it ``mock:`` to make this visible
        # in the audit log.
        return "mock:" + scenario_id, True

    async def complete(
        self,
        *,
        scenario_id: str,
        level: SensitivityLevel,
        prompt: str,
        skip_desensitize: bool = False,
    ) -> LLMResponse:
        ep_name, is_local = self.pick_endpoint(
            scenario_id=scenario_id, level=level, skip_desensitize=skip_desensitize
        )
        # EX-P2-8: retry with exponential backoff on transient errors.
        # Retryable: 5xx / 429 / connection-reset / timeout.  Non-
        # retryable 4xx (auth, invalid request) short-circuits the loop
        # so we don't waste 4×prompt tokens on a permanent failure.
        max_retries = _resolve_int(LLM_RETRIES_ENV, DEFAULT_LLM_RETRIES)
        backoff_base = _resolve_float(
            LLM_BACKOFF_BASE_ENV, DEFAULT_LLM_BACKOFF_BASE_SEC
        )
        jitter_max = _resolve_float(
            LLM_BACKOFF_JITTER_ENV, DEFAULT_LLM_BACKOFF_JITTER_SEC
        )
        attempt = 0
        last_exc: BaseException | None = None
        while True:
            try:
                response = await self.responder.complete(
                    prompt=prompt,
                    endpoint_name=ep_name,
                    sensitivity_level=level,
                    scenario_id=scenario_id,
                )
                if attempt > 0:
                    logger.info(
                        "finance-auto llm: scenario=%s endpoint=%s succeeded "
                        "after %d retry(ies)",
                        scenario_id, ep_name, attempt,
                    )
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt >= max_retries or not is_retryable_llm_error(exc):
                    logger.warning(
                        "finance-auto llm: scenario=%s endpoint=%s "
                        "FAILED on attempt %d/%d (retryable=%s): %s",
                        scenario_id, ep_name, attempt + 1,
                        max_retries + 1,
                        is_retryable_llm_error(exc), exc,
                    )
                    raise
                wait = backoff_base * (2 ** attempt)
                if jitter_max > 0:
                    wait += random.uniform(0, jitter_max)
                logger.info(
                    "finance-auto llm: scenario=%s endpoint=%s "
                    "transient error on attempt %d/%d (%s); "
                    "sleeping %.2fs before retry",
                    scenario_id, ep_name, attempt + 1,
                    max_retries + 1, exc, wait,
                )
                await asyncio.sleep(wait)
                attempt += 1
                continue
        if last_exc is not None and attempt == 0:
            # Defensive: if we somehow exit the loop with last_exc set
            # but no successful response we should surface it.  In
            # practice the break above guarantees response is set when
            # attempt is 0.
            raise last_exc  # pragma: no cover
        response.is_local = is_local or response.is_local
        return response


__all__ = [
    "DEFAULT_LLM_BACKOFF_BASE_SEC",
    "DEFAULT_LLM_BACKOFF_JITTER_SEC",
    "DEFAULT_LLM_RETRIES",
    "EndpointDescriptor",
    "FinanceAIRouter",
    "HostBrainResponder",
    "LLMResponder",
    "LLMResponse",
    "LLM_BACKOFF_BASE_ENV",
    "LLM_BACKOFF_JITTER_ENV",
    "LLM_RETRIES_ENV",
    "MockLLMResponder",
    "RoutingConfig",
    "collect_endpoints_from_host_client",
    "is_retryable_llm_error",
]
