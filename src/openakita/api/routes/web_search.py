"""Web search provider configuration & test endpoints.

Two endpoints, both consumed by ``apps/setup-center/src/components/WebSearchProviderPanel``:

* ``GET  /api/tools/web-search/providers``
    Returns the list of registered providers with availability + UI metadata
    (label / signup_url / docs_url / requires_credential / is_available).
    Used to render one card per provider in the settings panel.

* ``POST /api/tools/web-search/test``
    Run a one-off search through the named provider with a tiny query, so the
    user can click "测试" and see if their freshly-entered Key works end-to-end.
    Returns ``{ok, provider_id, error_code?, message?, results?}``. Errors are
    mapped to ``ConfigHintErrorCode`` so the frontend uses the same
    classification as the chat-side ``ConfigHintCard``.

Why a dedicated route module instead of folding into ``config.py``? The
panel needs runtime provider introspection (``available_providers()``) and
async network calls; ``config.py`` is purely sync env file IO.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ...tools.web_search import (
    NoProviderAvailable,
    ProviderError,
    available_providers,
    get_provider,
    iter_providers,
    run_web_search,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tools/web-search", tags=["搜索源"])


class TestSearchRequest(BaseModel):
    """Body for ``POST /test``."""

    provider_id: str = Field(..., description="要测试的搜索源 ID")
    query: str = Field(default="OpenAkita", description="测试查询；建议短词避免触发限流")
    max_results: int = Field(default=3, ge=1, le=10, description="返回结果数")
    timeout_seconds: float = Field(
        default=15.0,
        ge=1.0,
        le=60.0,
        description="单次测试超时（秒）；大于实际测试需求即可",
    )


class TestSearchResultItem(BaseModel):
    """One row in the test response."""

    title: str
    url: str
    snippet: str = ""


class TestSearchResponse(BaseModel):
    """Response shape for the test endpoint.

    On success: ``ok=True`` + ``results``. On failure: ``ok=False`` + ``error_code``
    + ``message``. The frontend uses ``error_code`` to color-code the result
    (red for auth/missing, yellow for rate/network) and reuses the same
    ``ConfigHintErrorCode`` vocabulary as the chat-side hints.
    """

    ok: bool
    provider_id: str
    results: list[TestSearchResultItem] = Field(default_factory=list)
    error_code: str = ""
    message: str = ""


class ProviderDescriptor(BaseModel):
    """One provider entry in the providers listing."""

    id: str
    label: str
    requires_credential: bool
    is_available: bool
    auto_detect_order: int
    signup_url: str = ""
    docs_url: str = ""


class ProvidersResponse(BaseModel):
    """Response shape for ``GET /providers``."""

    active: str = Field(default="", description="settings.web_search_provider 当前值；留空=auto")
    providers: list[ProviderDescriptor]


@router.get("/providers", response_model=ProvidersResponse)
async def list_providers() -> ProvidersResponse:
    """List all registered web_search providers with availability state."""
    from ...config import settings

    descriptors = [
        ProviderDescriptor(
            id=p.id,
            label=p.label,
            requires_credential=p.requires_credential,
            is_available=p.is_available(),
            auto_detect_order=p.auto_detect_order,
            signup_url=p.signup_url,
            docs_url=p.docs_url,
        )
        for p in iter_providers()
    ]
    return ProvidersResponse(
        active=(settings.web_search_provider or ""),
        providers=descriptors,
    )


@router.post("/test", response_model=TestSearchResponse)
async def test_search(req: TestSearchRequest) -> TestSearchResponse:
    """Run a small search via ``req.provider_id`` to verify config end-to-end.

    Always 200 (no HTTPException), with structured ``ok`` + ``error_code`` so
    the frontend can render an inline result instead of a generic error toast.
    """
    # Validate provider_id up front to give a clear UI message.
    try:
        get_provider(req.provider_id)
    except KeyError:
        return TestSearchResponse(
            ok=False,
            provider_id=req.provider_id,
            error_code="missing_credential",
            message=(
                f"未知的搜索源 {req.provider_id!r}。"
                f"已注册：{[p.id for p in iter_providers()]}"
            ),
        )

    try:
        bundle = await run_web_search(
            req.query,
            provider_id=req.provider_id,
            max_results=req.max_results,
            timeout_seconds=req.timeout_seconds,
        )
    except NoProviderAvailable as exc:
        return TestSearchResponse(
            ok=False,
            provider_id=req.provider_id,
            error_code=exc.error_code,
            message=str(exc) or "搜索源不可用",
        )
    except ProviderError as exc:
        return TestSearchResponse(
            ok=False,
            provider_id=req.provider_id,
            error_code=getattr(exc, "error_code", "unknown"),
            message=str(exc) or "测试失败",
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("[web_search.test] unexpected error testing %s", req.provider_id)
        return TestSearchResponse(
            ok=False,
            provider_id=req.provider_id,
            error_code="unknown",
            message=f"{type(exc).__name__}: {exc}",
        )

    return TestSearchResponse(
        ok=True,
        provider_id=bundle.provider_id,
        results=[
            TestSearchResultItem(title=r.title, url=r.url, snippet=r.snippet[:200])
            for r in bundle.results
        ],
    )


@router.get("/availability")
async def get_availability() -> dict:
    """Return ``{available_count, total_count, available_ids}``.

    Used by the chat UI to decide whether to badge the search panel "未配置"
    when no provider is configured. Lighter than ``/providers`` for hot polls.
    """
    avail = available_providers()
    total = iter_providers()
    return {
        "available_count": len(avail),
        "total_count": len(total),
        "available_ids": [p.id for p in avail],
    }
