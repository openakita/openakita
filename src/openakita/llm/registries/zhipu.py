"""
智谱 AI (Zhipu / GLM) 服务商注册表（OpenAI 兼容）

说明：
- 国内区：open.bigmodel.cn  → https://open.bigmodel.cn/api/paas/v4
- 国际区：z.ai / api.z.ai   → https://api.z.ai/api/paas/v4

两个区域 API 完全 OpenAI 兼容，支持 /v4/chat/completions、/v4/models 等。
模型通用：GLM-5、GLM-4.7、GLM-4.6V、GLM-4.5、GLM-4 等。

API 文档:
  国内: https://open.bigmodel.cn/dev/api
  国际: https://docs.z.ai/
"""

import httpx

from ..capabilities import infer_capabilities
from .base import ModelInfo, ProviderInfo, ProviderRegistry


class ZhipuChinaRegistry(ProviderRegistry):
    """智谱 AI 中国区注册表"""

    info = ProviderInfo(
        name="智谱 AI (Zhipu·中国区)",
        slug="zhipu-cn",
        api_type="openai",
        default_base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key_env_suggestion="ZHIPU_API_KEY",
        supports_model_list=True,
        supports_capability_api=False,
    )

    async def list_models(self, api_key: str) -> list[ModelInfo]:
        """
        获取智谱 AI 中国区模型列表。

        智谱兼容 OpenAI /models 接口。
        如果 API 调用失败，返回预置的常用模型列表。
        """
        async with httpx.AsyncClient(timeout=30) as client:
            try:
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
                            capabilities=infer_capabilities(mid, provider_slug="zhipu"),
                        )
                    )
                return sorted(models, key=lambda x: x.id)

            except httpx.HTTPError:
                return self._get_preset_models()

    def _get_preset_models(self) -> list[ModelInfo]:
        """返回预置模型列表"""
        preset = [
            # GLM-5 系列（最新旗舰）
            "glm-5",
            "glm-5-plus",
            # GLM-4.7 系列
            "glm-4.7",
            # GLM-4.6 系列
            "glm-4.6v",
            # GLM-4.5 系列
            "glm-4.5v",
            # GLM-4 系列
            "glm-4",
            "glm-4-plus",
            "glm-4-air",
            "glm-4-airx",
            "glm-4-long",
            "glm-4-flash",
            "glm-4-flashx",
            # 视觉模型
            "glm-4v",
            "glm-4v-plus",
            # 工具/Agent 模型
            "autoglm-phone",
        ]
        return [
            ModelInfo(
                id=model_id,
                name=model_id,
                capabilities=infer_capabilities(model_id, provider_slug="zhipu"),
            )
            for model_id in preset
        ]


class ZhipuInternationalRegistry(ProviderRegistry):
    """智谱 AI 国际区 (Z.AI) 注册表"""

    info = ProviderInfo(
        name="Zhipu AI (Z.AI·International)",
        slug="zhipu-int",
        api_type="openai",
        default_base_url="https://api.z.ai/api/paas/v4",
        api_key_env_suggestion="ZHIPU_API_KEY",
        supports_model_list=True,
        supports_capability_api=False,
    )

    async def list_models(self, api_key: str) -> list[ModelInfo]:
        """
        获取智谱 AI 国际区模型列表。

        国际区 API 与国内区完全兼容。
        """
        async with httpx.AsyncClient(timeout=30) as client:
            try:
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
                            capabilities=infer_capabilities(mid, provider_slug="zhipu"),
                        )
                    )
                return sorted(models, key=lambda x: x.id)

            except httpx.HTTPError:
                return self._get_preset_models()

    def _get_preset_models(self) -> list[ModelInfo]:
        """返回预置模型列表（与国内区共享模型）"""
        preset = [
            "glm-5",
            "glm-5-plus",
            "glm-4.7",
            "glm-4.6v",
            "glm-4.5v",
            "glm-4",
            "glm-4-plus",
            "glm-4-air",
            "glm-4v",
            "glm-4v-plus",
            "autoglm-phone",
        ]
        return [
            ModelInfo(
                id=model_id,
                name=model_id,
                capabilities=infer_capabilities(model_id, provider_slug="zhipu"),
            )
            for model_id in preset
        ]
