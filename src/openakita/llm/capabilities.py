"""
Preset model capability table

Seven capabilities:
- text: whether text input/output is supported (all models support this)
- vision: whether image input is supported (image_url type, OpenAI standard format)
- video: whether video input is supported (video_url type, Kimi private extension / DashScope compatible)
- tools: whether tool calling (function calling) is supported
- thinking: whether thinking mode (deep reasoning) is supported
- audio: whether native audio input is supported (input_audio / inline_data, etc.)
- pdf: whether native PDF document input is supported (document content block)

Note: the same model from different providers may have different capabilities.
Structure: MODEL_CAPABILITIES[provider_slug][model_name] = {...}
"""

# Preset model capability table
MODEL_CAPABILITIES = {
    # ============================================================
    # Official Providers
    # ============================================================
    "openai": {
        # OpenAI official
        "gpt-5": {"text": True, "vision": True, "video": False, "tools": True, "thinking": False},
        "gpt-5.2": {"text": True, "vision": True, "video": False, "tools": True, "thinking": False},
        "gpt-4o": {"text": True, "vision": True, "video": False, "tools": True, "thinking": False},
        "gpt-4o-audio": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
            "audio": True,
        },
        "gpt-4o-mini": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "gpt-4-vision": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "gpt-4-turbo": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "gpt-4": {"text": True, "vision": False, "video": False, "tools": True, "thinking": False},
        "gpt-3.5-turbo": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "o1": {"text": True, "vision": True, "video": False, "tools": True, "thinking": True},
        "o1-mini": {"text": True, "vision": False, "video": False, "tools": True, "thinking": True},
        "o1-preview": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
        },
    },
    "anthropic": {
        # Anthropic official — all Claude 3+ models support native PDF input
        "claude-opus-4.5": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
            "pdf": True,
        },
        "claude-sonnet-4.5": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
            "pdf": True,
        },
        "claude-haiku-4.5": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
            "pdf": True,
        },
        "claude-3-opus": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
            "pdf": True,
        },
        "claude-3-sonnet": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
            "pdf": True,
        },
        "claude-3-haiku": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
            "pdf": True,
        },
        "claude-3-5-sonnet": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
            "pdf": True,
        },
        "claude-3-5-haiku": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
            "pdf": True,
        },
    },
    "deepseek": {
        # DeepSeek official
        "deepseek-v3.2": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "deepseek-v3": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "deepseek-chat": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "deepseek-coder": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "deepseek-vl2": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": False,
            "thinking": False,
        },
        "deepseek-vl2-base": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": False,
            "thinking": False,
        },
        "deepseek-r1": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
            "thinking_only": True,
        },
        "deepseek-r1-lite": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
            "thinking_only": True,
        },
        "deepseek-reasoner": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
            "thinking_only": True,
        },
    },
    "moonshot": {
        # Kimi / Moonshot AI official
        # Note: Kimi is one of the few models currently supporting video understanding; video requests are routed here preferentially
        "kimi-k2.5": {
            "text": True,
            "vision": True,
            "video": True,
            "tools": True,
            "thinking": False,
        },
        "kimi-k2": {"text": True, "vision": True, "video": True, "tools": True, "thinking": False},
        "moonshot-v1-8k": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "moonshot-v1-32k": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "moonshot-v1-128k": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
    },
    "dashscope": {
        # Alibaba Cloud DashScope (Tongyi Qianwen official)
        "qwen3-vl": {"text": True, "vision": True, "video": True, "tools": True, "thinking": True},
        "qwen2.5-vl": {
            "text": True,
            "vision": True,
            "video": True,
            "tools": True,
            "thinking": False,
        },
        "qwen3": {"text": True, "vision": False, "video": False, "tools": True, "thinking": True},
        # Commercial versions
        "qwen-max": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "qwen-max-latest": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "qwen-plus": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
        },
        "qwen-plus-latest": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
        },
        "qwen-flash": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
        },
        "qwen-turbo": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
        },
        "qwen-turbo-latest": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
        },
        # Qwen3.5 series - dual mode (supports enable_thinking toggle)
        "qwen3.5-plus": {
            "text": True,
            "vision": True,
            "video": True,
            "tools": True,
            "thinking": True,
        },
        "qwen3.5-turbo": {
            "text": True,
            "vision": True,
            "video": True,
            "tools": True,
            "thinking": True,
        },
        # Qwen3 open-source - thinking-only mode
        "qwen3-235b-a22b-thinking": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
            "thinking_only": True,
        },
        "qwen3-30b-a3b-thinking": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
            "thinking_only": True,
        },
        # Qwen3 open-source - non-thinking mode only
        "qwen3-235b-a22b-instruct": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "qwen3-30b-a3b-instruct": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        # Vision models (Qwen-VL series supports video input)
        "qwen-vl-max": {
            "text": True,
            "vision": True,
            "video": True,
            "tools": True,
            "thinking": False,
        },
        "qwen-vl-max-latest": {
            "text": True,
            "vision": True,
            "video": True,
            "tools": True,
            "thinking": False,
        },
        "qwen-vl-plus": {
            "text": True,
            "vision": True,
            "video": True,
            "tools": True,
            "thinking": False,
        },
        "qwen-vl-plus-latest": {
            "text": True,
            "vision": True,
            "video": True,
            "tools": True,
            "thinking": False,
        },
        "qwen3-vl-plus": {
            "text": True,
            "vision": True,
            "video": True,
            "tools": True,
            "thinking": True,
        },
        "qwen3-vl-flash": {
            "text": True,
            "vision": True,
            "video": True,
            "tools": True,
            "thinking": True,
        },
        # Audio models
        "qwen-audio-turbo": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": False,
            "thinking": False,
            "audio": True,
        },
        "qwen2-audio": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": False,
            "thinking": False,
            "audio": True,
        },
        # QwQ reasoning models
        "qwq-plus": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
            "thinking_only": True,
        },
        "qwq-32b": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
            "thinking_only": True,
        },
        "qvq-max": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": False,
            "thinking": True,
            "thinking_only": True,
        },
        # DashScope third-party models — MiniMax (thinking-only, does not accept enable_thinking=False)
        "MiniMax-M2.5": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
            "thinking_only": True,
        },
        "MiniMax-M2.1": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
            "thinking_only": True,
        },
        "MiniMax-M2": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
            "thinking_only": True,
        },
        # DashScope third-party models — Kimi / GLM (supports enable_thinking toggle)
        "kimi-k2.5": {
            "text": True,
            "vision": True,
            "video": True,
            "tools": True,
            "thinking": False,
        },
        "glm-5": {"text": True, "vision": False, "video": False, "tools": True, "thinking": False},
    },
    "minimax": {
        # MiniMax official (does not support the /v1/models endpoint)
        # M2+ series are all thinking-only models; they do not accept enable_thinking=False
        "minimax-m2.5": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
            "thinking_only": True,
        },
        "minimax-m2.5-highspeed": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
            "thinking_only": True,
        },
        "minimax-m2.1": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
            "thinking_only": True,
        },
        "minimax-m2.1-highspeed": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
            "thinking_only": True,
        },
        "minimax-m2": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
            "thinking_only": True,
        },
        "abab6.5s-chat": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "abab6.5-chat": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
    },
    "zhipu": {
        # Zhipu AI official (Z.AI / BigModel)
        # ── GLM-5 series (latest flagship) ──
        "glm-5": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
        },
        "glm-5-plus": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
        },
        # ── GLM-4.7 series ──
        "glm-4.7": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
        },
        # ── GLM-4.6 series ──
        "glm-4.6v": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        # ── GLM-4.5 series ──
        "glm-4.5v": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        # ── GLM-4 series ──
        "glm-4": {"text": True, "vision": False, "video": False, "tools": True, "thinking": False},
        "glm-4-plus": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "glm-4-air": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "glm-4-airx": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "glm-4-long": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "glm-4-flash": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "glm-4-flashx": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "glm-4v": {"text": True, "vision": True, "video": False, "tools": True, "thinking": False},
        "glm-4v-plus": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "glm-4-32b-0414-128k": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
        },
        # ── Special models ──
        "autoglm-phone": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "glm-ocr": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": False,
            "thinking": False,
        },
    },
    "google": {
        # Google Gemini official — supports native video, audio input, and PDF
        "gemini-3-pro": {
            "text": True,
            "vision": True,
            "video": True,
            "tools": True,
            "thinking": False,
            "audio": True,
            "pdf": True,
        },
        "gemini-3-flash": {
            "text": True,
            "vision": True,
            "video": True,
            "tools": True,
            "thinking": False,
            "audio": True,
            "pdf": True,
        },
        "gemini-2.5-pro": {
            "text": True,
            "vision": True,
            "video": True,
            "tools": True,
            "thinking": False,
            "audio": True,
            "pdf": True,
        },
        "gemini-2.5-flash": {
            "text": True,
            "vision": True,
            "video": True,
            "tools": True,
            "thinking": False,
            "audio": True,
            "pdf": True,
        },
        "gemini-2.0-flash": {
            "text": True,
            "vision": True,
            "video": True,
            "tools": True,
            "thinking": False,
            "audio": True,
            "pdf": True,
        },
        "gemini-2.0-flash-lite": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": False,
            "thinking": False,
            "audio": False,
            "pdf": False,
        },
        "gemini-1.5-pro": {
            "text": True,
            "vision": True,
            "video": True,
            "tools": True,
            "thinking": False,
            "audio": True,
            "pdf": True,
        },
        "gemini-1.5-flash": {
            "text": True,
            "vision": True,
            "video": True,
            "tools": True,
            "thinking": False,
            "audio": True,
            "pdf": True,
        },
    },
    # ============================================================
    # Third-party Providers
    # Third-party providers may offer different capabilities than the official ones; maintained separately
    # ============================================================
    "openrouter": {
        # OpenRouter returns capability info from the API; this is a fallback
    },
    "siliconflow": {
        # SiliconFlow - mainly provides open-source models
        # Native thinking models (always thinking, does not support enable_thinking toggle)
        "moonshotai/Kimi-K2-Thinking": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
            "thinking_only": True,
        },
        "deepseek-ai/DeepSeek-R1": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": False,
            "thinking": True,
            "thinking_only": True,
        },
        "Qwen/QwQ-32B": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
            "thinking_only": True,
        },
        # Models with toggleable thinking mode (supports enable_thinking)
        "Qwen/Qwen3-235B-A22B": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
        },
        "Qwen/Qwen3-32B": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
        },
        "Qwen/Qwen3-14B": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
        },
        "Qwen/Qwen3-8B": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
        },
        "deepseek-ai/DeepSeek-V3": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "deepseek-ai/DeepSeek-V3.1": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
        },
        "deepseek-ai/DeepSeek-V3.2": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
        },
        "moonshotai/Kimi-K2-Instruct": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "moonshotai/Kimi-K2.5": {
            "text": True,
            "vision": True,
            "video": True,
            "tools": True,
            "thinking": False,
        },
    },
    "volcengine": {
        # Volcengine (Volcengine / Volcano Ark) - ByteDance
        "doubao-seed-1-6": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": True,
        },
        "doubao-seed-code": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "doubao-1-5-pro-256k": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "doubao-1-5-pro-32k": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "doubao-1-5-lite-32k": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "doubao-1-5-vision-pro-32k": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "doubao-pro-256k": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "doubao-pro-32k": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "doubao-pro-4k": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "doubao-lite-128k": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "doubao-lite-32k": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "doubao-lite-4k": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "doubao-vision-pro-32k": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "doubao-vision-lite-32k": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": False,
            "thinking": False,
        },
        "deepseek-r1": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": False,
            "thinking": True,
        },
        "deepseek-v3": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
    },
    "yunwu": {
        # Yunwu API - third-party gateway
    },
}


# URL to provider mapping
URL_TO_PROVIDER = {
    "api.openai.com": "openai",
    "api.anthropic.com": "anthropic",
    "dashscope.aliyuncs.com": "dashscope",
    "dashscope-intl.aliyuncs.com": "dashscope",
    "api.deepseek.com": "deepseek",
    "api.moonshot.cn": "moonshot",
    "api.minimax.chat": "minimax",
    "open.bigmodel.cn": "zhipu",
    "bigmodel.cn": "zhipu",
    "api.z.ai": "zhipu",
    "generativelanguage.googleapis.com": "google",
    "openrouter.ai": "openrouter",
    "api.siliconflow.cn": "siliconflow",
    "api.siliconflow.com": "siliconflow",
    "yunwu.ai": "yunwu",
    "api.longcat.chat": "longcat",
    "apis.iflow.cn": "iflow",
    "ark.cn-beijing.volces.com": "volcengine",
}


def infer_capabilities(
    model_name: str, provider_slug: str | None = None, user_config: dict | None = None
) -> dict:
    """
    Infer model capabilities

    Args:
        model_name: model name
        provider_slug: provider identifier (e.g. "dashscope", "openai", "openrouter")
        user_config: capabilities declared by the user in config (optional)

    Returns:
        {"text": bool, "vision": bool, "video": bool, "tools": bool, "thinking": bool, "audio": bool, "pdf": bool}

    ⚠ Maintenance note: the frontend has a simplified version of this function
    (apps/setup-center/src/App.tsx → inferCapabilities), used in bundled mode
    when the frontend directly calls provider APIs to fetch model lists and infer capabilities.
    If you modify the keyword rules below (step 4 "Infer capabilities from model-name keywords"),
    update the frontend inferCapabilities function accordingly.
    """
    # Helper to ensure the result always contains all capability fields
    _ALL_CAPS = {
        "text": False,
        "vision": False,
        "video": False,
        "tools": False,
        "thinking": False,
        "audio": False,
        "pdf": False,
    }

    def _normalize(caps: dict) -> dict:
        result = _ALL_CAPS.copy()
        result.update(caps)
        return result

    # 1. Prefer user config
    if user_config:
        return _normalize(user_config)

    model_lower = model_name.lower()

    # 2. Exact match by provider + model name
    if provider_slug and provider_slug in MODEL_CAPABILITIES:
        provider_models = MODEL_CAPABILITIES[provider_slug]

        # Exact match
        if model_name in provider_models:
            return _normalize(provider_models[model_name])

        # Prefix match (handles version numbers, etc.)
        for model_key, caps in provider_models.items():
            if model_lower.startswith(model_key.lower()):
                return _normalize(caps)

    # 3. Cross-provider fuzzy match (used for third-party gateways, etc.)
    # Model names for local inference services (Ollama/LMStudio, etc.) often carry `:NB`
    # variant suffixes (like deepseek-r1:8b). These distilled/quantized versions typically
    # have different capabilities than the same-name official large model, so
    # cross-provider prefix matching would make small models incorrectly inherit the
    # capability flags of big models.
    _is_local = provider_slug in ("ollama", "local", "lmstudio")
    _is_variant = ":" in model_name
    if not (_is_local and _is_variant):
        for _provider, models in MODEL_CAPABILITIES.items():
            for model_key, caps in models.items():
                if model_lower.startswith(model_key.lower()):
                    return _normalize(caps)

    # 4. Infer capabilities from model-name keywords
    caps = {
        "text": True,
        "vision": False,
        "video": False,
        "tools": False,
        "thinking": False,
        "audio": False,
        "pdf": False,
    }

    # Vision inference (images)
    if any(kw in model_lower for kw in ["vl", "vision", "visual", "image", "-v-", "4v"]):
        caps["vision"] = True

    # Video inference - conservative strategy; only kimi/gemini/qwen-vl are explicitly supported
    if any(kw in model_lower for kw in ["kimi", "gemini"]):
        caps["video"] = True
    # Qwen-VL series explicitly supports video
    if "vl" in model_lower and any(kw in model_lower for kw in ["qwen", "dashscope"]):
        caps["video"] = True

    # Audio inference (native audio input) - very conservative
    if any(kw in model_lower for kw in ["audio", "gemini"]):
        caps["audio"] = True

    # PDF inference (native document input) - conservative strategy
    if any(kw in model_lower for kw in ["claude", "gemini"]):
        caps["pdf"] = True

    # Thinking inference
    if any(kw in model_lower for kw in ["thinking", "r1", "qwq", "qvq", "o1", "reasoner"]):
        caps["thinking"] = True
        # Native thinking models: names containing -Thinking suffix, R1, QwQ, Reasoner, etc.
        # are always in thinking mode. These models do not support toggling thinking via
        # API parameters (such as SiliconFlow's enable_thinking).
        if any(
            kw in model_lower
            for kw in ["-thinking", "-r1", "/r1", "qwq", "qvq", "o1-", "o3-", "reasoner"]
        ):
            caps["thinking_only"] = True

    # Tools inference (supported by most mainstream models)
    if any(
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
            "doubao",
            "minimax",
        ]
    ):
        caps["tools"] = True

    return caps


def get_provider_slug_from_base_url(base_url: str) -> str | None:
    """
    Infer provider identifier from base_url

    Examples:
        "https://api.openai.com/v1" -> "openai"
        "https://dashscope.aliyuncs.com/..." -> "dashscope"
        "https://openrouter.ai/api/v1" -> "openrouter"
        "http://localhost:11434/v1" -> "ollama"
        "http://127.0.0.1:1234/v1" -> "lmstudio"
    """
    for domain, slug in URL_TO_PROVIDER.items():
        if domain in base_url:
            return slug

    # Local endpoint detection: distinguish Ollama / LM Studio by port number
    url_lower = base_url.lower()
    local_hosts = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]")
    if any(host in url_lower for host in local_hosts):
        if ":11434" in url_lower:
            return "ollama"
        if ":1234" in url_lower:
            return "lmstudio"
        # Other local ports → generic local identifier
        return "local"

    return None


def get_all_providers() -> list[str]:
    """Get all known providers"""
    return list(MODEL_CAPABILITIES.keys())


def get_models_by_provider(provider_slug: str) -> list[str]:
    """Get all known models for the given provider"""
    return list(MODEL_CAPABILITIES.get(provider_slug, {}).keys())


def supports_capability(model_name: str, capability: str, provider_slug: str | None = None) -> bool:
    """Check whether the model supports a given capability"""
    caps = infer_capabilities(model_name, provider_slug)
    return caps.get(capability, False)


def is_thinking_only(model_name: str, provider_slug: str | None = None) -> bool:
    """Check whether the model only supports thinking mode"""
    caps = infer_capabilities(model_name, provider_slug)
    return caps.get("thinking_only", False)
