"""
MiniMax 服务商注册表（OpenAI 兼容）

参考（常见 base_url）：
- 中国区： https://api.minimaxi.com/v1
- 国际区： https://api.minimax.io/v1

MiniMax 现已提供 OpenAI 兼容的 ``GET /v1/models`` 列表端点。优先在线拉取，
拉取失败（旧网关无该端点 / 网络错误 / Key 无效等）时回退到内置候选列表。

判断技巧：在线列表会包含最新模型（如 MiniMax-M3），内置回退列表不含 M3。
"""

import logging

from .base import ModelInfo, ProviderInfo, ProviderRegistry, create_registry_client

_logger = logging.getLogger(__name__)

# 内置回退候选（不含 M3）：仅在线拉取失败时使用。
_FALLBACK_MODEL_IDS = [
    "MiniMax-M2.7",
    "MiniMax-M2.5",
    "MiniMax-M2.5-highspeed",
    "MiniMax-M2.1",
    "MiniMax-M2.1-highspeed",
    "MiniMax-M2",
]


async def _fetch_minimax_models(base_url: str, api_key: str) -> list[ModelInfo]:
    """调用 OpenAI 兼容的 /models 端点，失败时回退内置候选。"""
    from ..capabilities import infer_capabilities

    base = base_url.rstrip("/")
    url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"

    try:
        async with create_registry_client(base_url) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {api_key}"})
            resp.raise_for_status()
            data = resp.json()

        out: list[ModelInfo] = []
        seen: set[str] = set()
        for m in data.get("data", []) or []:
            mid = (m.get("id") or "").strip()
            if not mid or mid in seen:
                continue
            seen.add(mid)
            out.append(
                ModelInfo(
                    id=mid,
                    name=str(m.get("display_name") or mid),
                    capabilities=infer_capabilities(mid, provider_slug="minimax"),
                )
            )
        if out:
            return sorted(out, key=lambda x: x.id)
        _logger.warning("MiniMax /models 返回空列表，回退到内置候选")
    except Exception as e:
        _logger.warning("MiniMax 在线模型列表拉取失败，回退到内置候选: %s", e)

    return [
        ModelInfo(
            id=mid,
            name=mid,
            capabilities=infer_capabilities(mid, provider_slug="minimax"),
        )
        for mid in _FALLBACK_MODEL_IDS
    ]


class MiniMaxChinaRegistry(ProviderRegistry):
    info = ProviderInfo(
        name="MiniMax（中国区）",
        slug="minimax-cn",
        api_type="openai",
        default_base_url="https://api.minimaxi.com/v1",
        api_key_env_suggestion="MINIMAX_API_KEY",
        supports_model_list=True,
        supports_capability_api=False,
    )

    async def list_models(self, api_key: str) -> list[ModelInfo]:
        return await _fetch_minimax_models(self.info.default_base_url, api_key)


class MiniMaxInternationalRegistry(ProviderRegistry):
    info = ProviderInfo(
        name="MiniMax（国际区）",
        slug="minimax-int",
        api_type="openai",
        default_base_url="https://api.minimax.io/v1",
        api_key_env_suggestion="MINIMAX_API_KEY",
        supports_model_list=True,
        supports_capability_api=False,
    )

    async def list_models(self, api_key: str) -> list[ModelInfo]:
        return await _fetch_minimax_models(self.info.default_base_url, api_key)
