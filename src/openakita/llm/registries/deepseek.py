"""
DeepSeek 服务商注册表（OpenAI 兼容）。

DeepSeek 官方提供 OpenAI 兼容协议，通常支持 `/v1/models`。
如果 API 拉取失败，则回退到预置模型列表（用于离线/网络受限/权限不足场景）。
"""

from ..capabilities import infer_capabilities
from .base import ModelInfo, ProviderInfo, ProviderRegistry, get_registry_client


class DeepSeekRegistry(ProviderRegistry):
    """DeepSeek 注册表"""

    info = ProviderInfo(
        name="DeepSeek",
        slug="deepseek",
        api_type="openai",
        default_base_url="https://api.deepseek.com/v1",
        api_key_env_suggestion="DEEPSEEK_API_KEY",
        supports_model_list=True,
        supports_capability_api=False,
    )

    async def list_models(self, api_key: str) -> list[ModelInfo]:
        client = get_registry_client()
        try:
            resp = await client.get(
                f"{self.info.default_base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
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
                    capabilities=infer_capabilities(mid, provider_slug="deepseek"),
                )
            )
        return sorted(models, key=lambda x: x.id)

    def _get_preset_models(self) -> list[ModelInfo]:
        preset = [
            "deepseek-chat",
            "deepseek-coder",
            "deepseek-v3",
            "deepseek-r1",
        ]
        return [
            ModelInfo(
                id=model_id,
                name=model_id,
                capabilities=infer_capabilities(model_id, provider_slug="deepseek"),
            )
            for model_id in preset
        ]
