"""
讯飞星辰 MaaS 服务商注册表（OpenAI 兼容）。

讯飞星辰 MaaS 平台提供 OpenAI 兼容协议，同时支持 Coding Plan 订阅服务。
常规推理: https://maas-api.cn-huabei-1.xf-yun.com/v2
Coding Plan: https://maas-coding-api.cn-huabei-1.xf-yun.com/v2 (OpenAI)
             https://maas-coding-api.cn-huabei-1.xf-yun.com/anthropic (Anthropic)

Coding Plan 统一使用 model=astron-code-latest，底层模型在平台控制台切换。
"""

import httpx

from ..capabilities import infer_capabilities
from .base import ModelInfo, ProviderInfo, ProviderRegistry


class XfyunRegistry(ProviderRegistry):
    """讯飞星辰 MaaS 注册表"""

    info = ProviderInfo(
        name="讯飞星辰 MaaS (iFlytek Astron)",
        slug="xfyun",
        api_type="openai",
        default_base_url="https://maas-api.cn-huabei-1.xf-yun.com/v2",
        api_key_env_suggestion="XFYUN_API_KEY",
        supports_model_list=True,
        supports_capability_api=False,
    )

    async def list_models(self, api_key: str) -> list[ModelInfo]:
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.get(
                    f"{self.info.default_base_url}/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPError:
                return self._get_preset_models()

        models: list[ModelInfo] = []
        seen: set[str] = set()
        for m in data.get("data", []) or []:
            if not isinstance(m, dict):
                continue
            mid = (m.get("id") or "").strip()
            if not mid or mid in seen:
                continue
            seen.add(mid)
            models.append(
                ModelInfo(
                    id=mid,
                    name=mid,
                    capabilities=infer_capabilities(mid, provider_slug="xfyun"),
                )
            )
        return sorted(models, key=lambda x: x.id) if models else self._get_preset_models()

    def _get_preset_models(self) -> list[ModelInfo]:
        preset = [
            "deepseek-v3.2",
            "deepseek-v3.1",
            "glm-5",
            "glm-4.7-flash",
            "qwen3-235b-a22b",
            "qwen3.5-35b-a3b",
            "minimax-m2.5",
            "kimi-k2.5",
            "astron-code-latest",
        ]
        return [
            ModelInfo(
                id=model_id,
                name=model_id,
                capabilities=infer_capabilities(model_id, provider_slug="xfyun"),
            )
            for model_id in preset
        ]
