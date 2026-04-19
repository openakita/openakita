"""
Kimi (Moonshot AI) provider registry (OpenAI-compatible)

Notes:
- China region: api.moonshot.cn
- International region: api.moonshot.ai
"""

from ..capabilities import infer_capabilities
from .base import ModelInfo, ProviderInfo, ProviderRegistry, get_registry_client


class KimiChinaRegistry(ProviderRegistry):
    info = ProviderInfo(
        name="Kimi (Moonshot AI · China)",
        slug="kimi-cn",
        api_type="openai",
        default_base_url="https://api.moonshot.cn/v1",
        api_key_env_suggestion="KIMI_API_KEY",
        supports_model_list=True,
        supports_capability_api=False,
    )

    async def list_models(self, api_key: str) -> list[ModelInfo]:
        client = get_registry_client()
        resp = await client.get(
            f"{self.info.default_base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        resp.raise_for_status()
        data = resp.json()

        out: list[ModelInfo] = []
        for m in data.get("data", []) or []:
            mid = (m.get("id") or "").strip()
            if not mid:
                continue
            out.append(
                ModelInfo(
                    id=mid,
                    name=mid,
                    capabilities=infer_capabilities(mid, provider_slug="kimi"),
                )
            )
        return sorted(out, key=lambda x: x.id)


class KimiInternationalRegistry(ProviderRegistry):
    info = ProviderInfo(
        name="Kimi (Moonshot AI · International)",
        slug="kimi-int",
        api_type="openai",
        default_base_url="https://api.moonshot.ai/v1",
        api_key_env_suggestion="KIMI_API_KEY",
        supports_model_list=True,
        supports_capability_api=False,
    )

    async def list_models(self, api_key: str) -> list[ModelInfo]:
        client = get_registry_client()
        resp = await client.get(
            f"{self.info.default_base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        resp.raise_for_status()
        data = resp.json()

        out: list[ModelInfo] = []
        for m in data.get("data", []) or []:
            mid = (m.get("id") or "").strip()
            if not mid:
                continue
            out.append(
                ModelInfo(
                    id=mid,
                    name=mid,
                    capabilities=infer_capabilities(mid, provider_slug="kimi"),
                )
            )
        return sorted(out, key=lambda x: x.id)
