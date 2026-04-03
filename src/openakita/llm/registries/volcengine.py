"""
火山引擎 (Volcengine / 火山方舟 Ark) 服务商注册表

火山方舟是字节跳动旗下的大模型服务平台，提供 OpenAI 兼容的 API 接口。
支持豆包(Doubao)系列模型、DeepSeek 等模型。

API 文档: https://www.volcengine.com/docs/82379/1330626
Base URL: https://ark.cn-beijing.volces.com/api/v3
"""

from ..capabilities import infer_capabilities
from .base import ModelInfo, ProviderInfo, ProviderRegistry, create_registry_client


class VolcEngineRegistry(ProviderRegistry):
    """火山引擎 (Volcengine Ark) 注册表"""

    info = ProviderInfo(
        name="火山引擎 (Volcengine)",
        slug="volcengine",
        api_type="openai",
        default_base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key_env_suggestion="ARK_API_KEY",
        supports_model_list=True,
        supports_capability_api=False,
    )

    async def list_models(self, api_key: str) -> list[ModelInfo]:
        """
        获取火山引擎模型列表

        火山方舟兼容 OpenAI /models 接口。
        如果 API 调用失败，返回预置的常用模型列表。
        """
        try:
            async with create_registry_client(self.info.default_base_url) as client:
                resp = await client.get(
                    f"{self.info.default_base_url}/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                resp.raise_for_status()
                data = resp.json()

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
                        capabilities=infer_capabilities(mid, provider_slug="volcengine"),
                    )
                )
            return sorted(models, key=lambda x: x.id)

        except Exception:
            return self._get_preset_models()

    def get_model_capabilities(self, model_id: str) -> dict:
        """获取模型能力"""
        return infer_capabilities(model_id, provider_slug="volcengine")

    def _get_preset_models(self) -> list[ModelInfo]:
        """返回预置模型列表（火山方舟常用模型）"""
        preset = [
            # 豆包 (Doubao) 系列
            "doubao-seed-1-6",
            "doubao-seed-code",
            "doubao-1-5-pro-256k",
            "doubao-1-5-pro-32k",
            "doubao-1-5-lite-32k",
            "doubao-1-5-vision-pro-32k",
            "doubao-pro-256k",
            "doubao-pro-32k",
            "doubao-pro-4k",
            "doubao-lite-128k",
            "doubao-lite-32k",
            "doubao-lite-4k",
            "doubao-vision-pro-32k",
            "doubao-vision-lite-32k",
            # DeepSeek 系列 (火山方舟托管)
            "deepseek-r1",
            "deepseek-v3",
            "deepseek-r1-distill-qwen-32b",
        ]

        return [
            ModelInfo(
                id=model_id,
                name=model_id,
                capabilities=infer_capabilities(model_id, provider_slug="volcengine"),
            )
            for model_id in preset
        ]
