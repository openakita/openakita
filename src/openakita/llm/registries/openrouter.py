"""
OpenRouter provider registry

OpenRouter's API returns complete capability information, which is the ideal case.
"""

from .base import ModelInfo, ProviderInfo, ProviderRegistry, get_registry_client


class OpenRouterRegistry(ProviderRegistry):
    """OpenRouter registry"""

    info = ProviderInfo(
        name="OpenRouter",
        slug="openrouter",
        api_type="openai",
        default_base_url="https://openrouter.ai/api/v1",
        api_key_env_suggestion="OPENROUTER_API_KEY",
        supports_model_list=True,
        supports_capability_api=True,  # OpenRouter returns capability information
    )

    async def list_models(self, api_key: str) -> list[ModelInfo]:
        """Fetch the OpenRouter model list"""
        client = get_registry_client()
        try:
            resp = await client.get(
                f"{self.info.default_base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()

            models = []
            for m in data.get("data", []):
                model_id = m.get("id", "")
                architecture = m.get("architecture", {})
                models.append(
                    ModelInfo(
                        id=model_id,
                        name=m.get("name", model_id),
                        capabilities=self._parse_capabilities(architecture, model_id),
                        context_window=m.get("context_length"),
                        max_output_tokens=m.get("top_provider", {}).get("max_completion_tokens"),
                        pricing=m.get("pricing"),
                    )
                )
            return sorted(models, key=lambda x: x.name)

        except Exception:
            return []

    def _parse_capabilities(self, architecture: dict, model_id: str) -> dict:
        """Parse capabilities from OpenRouter architecture information"""
        input_modalities = architecture.get("input_modalities", [])
        supported_params = architecture.get("supported_parameters", [])

        # Basic capabilities from API
        caps = {
            "text": "text" in input_modalities or True,  # All models support text
            "vision": "image" in input_modalities,
            "video": False,  # OpenRouter API does not explicitly return video support
            "tools": "tools" in supported_params or "function_call" in supported_params,
            "thinking": False,  # OpenRouter API does not explicitly return this info
        }

        # Thinking capability must be inferred from model name (OpenRouter API does not return it)
        model_lower = model_id.lower()
        if any(kw in model_lower for kw in ["o1", "r1", "qwq", "thinking"]):
            caps["thinking"] = True

        # Video capability must be inferred from model name
        if any(kw in model_lower for kw in ["kimi", "gemini"]):
            caps["video"] = True

        return caps
