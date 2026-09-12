"""
OrcaRouter 服务商注册表

OrcaRouter 的 API 返回 input_modalities 能力信息，可直接解析。
"""

from .base import ModelInfo, ProviderInfo, ProviderRegistry, create_registry_client

_ROUTER_MODELS = (
    ModelInfo(
        id="orcarouter/auto",
        name="OrcaRouter Auto Router",
        capabilities={
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
        },
    ),
    ModelInfo(
        id="orcarouter/free",
        name="OrcaRouter Free Models Router",
        capabilities={
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
        },
    ),
)


class OrcaRouterRegistry(ProviderRegistry):
    """OrcaRouter 注册表"""

    info = ProviderInfo(
        name="OrcaRouter",
        slug="orcarouter",
        api_type="openai",
        default_base_url="https://api.orcarouter.ai/v1",
        api_key_env_suggestion="ORCAROUTER_API_KEY",
        supports_model_list=True,
        supports_capability_api=True,  # OrcaRouter 返回 input_modalities
    )

    async def list_models(self, api_key: str) -> list[ModelInfo]:
        """获取 OrcaRouter 模型列表"""
        try:
            async with create_registry_client(self.info.default_base_url) as client:
                resp = await client.get(
                    f"{self.info.default_base_url}/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                resp.raise_for_status()
                data = resp.json()

            models = list(_ROUTER_MODELS)
            seen = {m.id for m in models}
            for m in data.get("data", []):
                model_id = m.get("id", "")
                if not model_id or model_id in seen:
                    continue
                seen.add(model_id)
                architecture = m.get("architecture") or {}
                models.append(
                    ModelInfo(
                        id=model_id,
                        name=m.get("name") or model_id,
                        capabilities=self._parse_capabilities(architecture, model_id),
                        context_window=m.get("context_length"),
                        pricing=m.get("pricing"),
                    )
                )
            return sorted(models, key=lambda x: x.name)

        except Exception:
            return list(_ROUTER_MODELS)

    def _parse_capabilities(self, architecture: dict, model_id: str) -> dict:
        """从 OrcaRouter 的架构信息解析能力"""
        input_modalities = architecture.get("input_modalities", []) if architecture else []
        caps = {
            "text": True,
            "vision": "image" in input_modalities,
            "video": "video" in input_modalities,
            "tools": False,
            "thinking": False,
            "audio": "audio" in input_modalities,
            "pdf": "file" in input_modalities,
        }

        model_lower = model_id.lower()

        # Thinking 能力：OrcaRouter API 不返回，基于模型名推断
        thinking = any(
            kw in model_lower
            for kw in ["o1", "o3", "o4", "r1", "qwq", "thinking", "reasoner", "opus"]
        )
        caps["thinking"] = thinking

        # Tools 能力：OrcaRouter API 不返回 supported_parameters，按模型名关键词推断
        # （推理模型 o1/o3/o4/r1 等同样支持函数调用）
        if thinking or any(
            kw in model_lower
            for kw in [
                "qwen",
                "gpt",
                "claude",
                "deepseek",
                "kimi",
                "glm",
                "gemini",
                "moonshot",
                "minimax",
                "doubao",
                "grok",
                "mistral",
            ]
        ):
            caps["tools"] = True

        return caps
