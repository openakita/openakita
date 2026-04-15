from __future__ import annotations

from dataclasses import dataclass, field

from .types import EndpointConfig


@dataclass(frozen=True)
class ResolvedModelContext:
    """本轮请求实际绑定的模型上下文。"""

    endpoint_name: str = ""
    model: str = ""
    provider: str = ""
    api_type: str = ""
    capabilities: tuple[str, ...] = ()
    context_window: int = 0
    effective_context_window: int = 0
    max_tokens: int = 0
    thinking_param_style: str = "none"
    selected_by: str = "none"
    allows_tools: bool = False
    fallback_chain: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_endpoint(
        cls,
        endpoint: EndpointConfig,
        *,
        selected_by: str,
        fallback_chain: list[str] | tuple[str, ...] | None = None,
    ) -> "ResolvedModelContext":
        caps = tuple(str(cap) for cap in (endpoint.capabilities or []))
        return cls(
            endpoint_name=endpoint.name,
            model=endpoint.model,
            provider=endpoint.provider,
            api_type=endpoint.api_type,
            capabilities=caps,
            context_window=int(endpoint.context_window or 0),
            effective_context_window=int(endpoint.get_effective_context_window() or 0),
            max_tokens=int(endpoint.max_tokens or 0),
            thinking_param_style=endpoint.get_thinking_param_style(),
            selected_by=selected_by,
            allows_tools=endpoint.has_capability("tools"),
            fallback_chain=tuple(fallback_chain or ()),
        )

    @property
    def has_tools(self) -> bool:
        return self.allows_tools
