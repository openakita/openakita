"""
预置模型能力表

五种能力:
- text: 是否支持文本输入/输出（所有模型都支持）
- vision: 是否支持图片输入（image_url 类型，OpenAI 标准格式）
- video: 是否支持视频输入（video_url 类型，Kimi 私有扩展）
- tools: 是否支持工具调用 (function calling)
- thinking: 是否支持思考模式 (深度推理)

注意：不同服务商提供的相同模型可能能力不同
结构: MODEL_CAPABILITIES[provider_slug][model_name] = {...}
"""

# 预置模型能力表
MODEL_CAPABILITIES = {
    # ============================================================
    # 官方服务商 (Official Providers)
    # ============================================================
    "openai": {
        # OpenAI 官方
        "gpt-5": {"text": True, "vision": True, "video": False, "tools": True, "thinking": False},
        "gpt-5.2": {"text": True, "vision": True, "video": False, "tools": True, "thinking": False},
        "gpt-4o": {"text": True, "vision": True, "video": False, "tools": True, "thinking": False},
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
        # Anthropic 官方
        "claude-opus-4.5": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "claude-sonnet-4.5": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "claude-haiku-4.5": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "claude-3-opus": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "claude-3-sonnet": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "claude-3-haiku": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "claude-3-5-sonnet": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "claude-3-5-haiku": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
        },
    },
    "deepseek": {
        # DeepSeek 官方
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
    },
    "moonshot": {
        # Kimi / Moonshot AI 官方
        # 注意：Kimi 是目前少数支持视频理解的模型，视频请求优先路由到这里
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
        # 阿里云 DashScope (通义千问官方)
        "qwen3-vl": {"text": True, "vision": True, "video": False, "tools": True, "thinking": True},
        "qwen2.5-vl": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "qwen3": {"text": True, "vision": False, "video": False, "tools": True, "thinking": True},
        # 商业版
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
        # Qwen3 开源 - 仅思考模式
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
        # Qwen3 开源 - 仅非思考模式
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
        # 视觉模型
        "qwen-vl-max": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "qwen-vl-max-latest": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "qwen-vl-plus": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "qwen-vl-plus-latest": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "qwen3-vl-plus": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": True,
        },
        "qwen3-vl-flash": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": True,
        },
        # QwQ 推理模型
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
    },
    "minimax": {
        # MiniMax 官方
        "minimax-m2.1": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        "minimax-m2": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": False,
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
        # 智谱 AI 官方 (Z.AI / BigModel)
        # ── GLM-5 系列（最新旗舰） ──
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
        # ── GLM-4.7 系列 ──
        "glm-4.7": {
            "text": True,
            "vision": False,
            "video": False,
            "tools": True,
            "thinking": True,
        },
        # ── GLM-4.6 系列 ──
        "glm-4.6v": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        # ── GLM-4.5 系列 ──
        "glm-4.5v": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": True,
            "thinking": False,
        },
        # ── GLM-4 系列 ──
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
        # ── 特殊模型 ──
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
        # Google Gemini 官方
        # 注意：Gemini 也支持视频，但 API 格式与 Kimi 不同，需要特殊处理
        "gemini-3-pro": {
            "text": True,
            "vision": True,
            "video": True,
            "tools": True,
            "thinking": False,
        },
        "gemini-3-flash": {
            "text": True,
            "vision": True,
            "video": True,
            "tools": True,
            "thinking": False,
        },
        "gemini-2.5-pro": {
            "text": True,
            "vision": True,
            "video": True,
            "tools": True,
            "thinking": False,
        },
        "gemini-2.5-flash": {
            "text": True,
            "vision": True,
            "video": True,
            "tools": True,
            "thinking": False,
        },
        "gemini-2.0-flash": {
            "text": True,
            "vision": True,
            "video": True,
            "tools": True,
            "thinking": False,
        },
        "gemini-2.0-flash-lite": {
            "text": True,
            "vision": True,
            "video": False,
            "tools": False,
            "thinking": False,
        },
        "gemini-1.5-pro": {
            "text": True,
            "vision": True,
            "video": True,
            "tools": True,
            "thinking": False,
        },
        "gemini-1.5-flash": {
            "text": True,
            "vision": True,
            "video": True,
            "tools": True,
            "thinking": False,
        },
    },
    # ============================================================
    # 中转服务商 (Third-party Providers)
    # 中转服务商的模型能力可能与官方不同，需单独维护
    # ============================================================
    "openrouter": {
        # OpenRouter 会从 API 返回能力信息，此处为备用
    },
    "siliconflow": {
        # 硅基流动 - 主要提供开源模型
    },
    "volcengine": {
        # 火山引擎 (Volcengine / 火山方舟 Ark) - 字节跳动
        "doubao-seed-1-6": {"text": True, "vision": True, "video": False, "tools": True, "thinking": True},
        "doubao-1-5-pro-256k": {"text": True, "vision": False, "video": False, "tools": True, "thinking": False},
        "doubao-1-5-pro-32k": {"text": True, "vision": False, "video": False, "tools": True, "thinking": False},
        "doubao-1-5-lite-32k": {"text": True, "vision": False, "video": False, "tools": True, "thinking": False},
        "doubao-1-5-vision-pro-32k": {"text": True, "vision": True, "video": False, "tools": True, "thinking": False},
        "doubao-pro-256k": {"text": True, "vision": False, "video": False, "tools": True, "thinking": False},
        "doubao-pro-32k": {"text": True, "vision": False, "video": False, "tools": True, "thinking": False},
        "doubao-pro-4k": {"text": True, "vision": False, "video": False, "tools": True, "thinking": False},
        "doubao-lite-128k": {"text": True, "vision": False, "video": False, "tools": True, "thinking": False},
        "doubao-lite-32k": {"text": True, "vision": False, "video": False, "tools": True, "thinking": False},
        "doubao-lite-4k": {"text": True, "vision": False, "video": False, "tools": True, "thinking": False},
        "doubao-vision-pro-32k": {"text": True, "vision": True, "video": False, "tools": True, "thinking": False},
        "doubao-vision-lite-32k": {"text": True, "vision": True, "video": False, "tools": False, "thinking": False},
        "deepseek-r1": {"text": True, "vision": False, "video": False, "tools": False, "thinking": True},
        "deepseek-v3": {"text": True, "vision": False, "video": False, "tools": True, "thinking": False},
    },
    "yunwu": {
        # 云雾 API - 中转服务
    },
}


# URL 到服务商的映射
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
    "ark.cn-beijing.volces.com": "volcengine",
}


def infer_capabilities(
    model_name: str, provider_slug: str | None = None, user_config: dict | None = None
) -> dict:
    """
    推断模型能力

    Args:
        model_name: 模型名称
        provider_slug: 服务商标识（如 "dashscope", "openai", "openrouter"）
        user_config: 用户在配置中声明的能力（可选）

    Returns:
        {"text": bool, "vision": bool, "video": bool, "tools": bool, "thinking": bool}

    ⚠ 维护提示：前端有此函数的简化版 (apps/setup-center/src/App.tsx → inferCapabilities)，
    用于打包模式下前端直连服务商 API 拉取模型列表时的 capability 推断。
    如果修改了下方的关键词规则（第 4 步"基于模型名关键词智能推断"），
    需要同步更新前端的 inferCapabilities 函数。
    """
    # 1. 优先使用用户配置
    if user_config:
        return user_config

    model_lower = model_name.lower()

    # 2. 按服务商+模型名精确匹配
    if provider_slug and provider_slug in MODEL_CAPABILITIES:
        provider_models = MODEL_CAPABILITIES[provider_slug]

        # 精确匹配
        if model_name in provider_models:
            return provider_models[model_name].copy()

        # 前缀匹配（处理版本号等）
        for model_key, caps in provider_models.items():
            if model_lower.startswith(model_key.lower()):
                return caps.copy()

    # 3. 跨服务商模糊匹配（用于中转服务商等场景）
    for _provider, models in MODEL_CAPABILITIES.items():
        for model_key, caps in models.items():
            if model_lower.startswith(model_key.lower()):
                return caps.copy()

    # 4. 基于模型名关键词智能推断
    caps = {"text": True, "vision": False, "video": False, "tools": False, "thinking": False}

    # Vision 推断（图片）
    if any(kw in model_lower for kw in ["vl", "vision", "visual", "image", "-v-", "4v"]):
        caps["vision"] = True

    # Video 推断（视频）- 非常保守，仅 kimi 和 gemini 明确支持
    if any(kw in model_lower for kw in ["kimi", "gemini"]):
        caps["video"] = True

    # Thinking 推断
    if any(kw in model_lower for kw in ["thinking", "r1", "qwq", "qvq", "o1"]):
        caps["thinking"] = True

    # Tools 推断 (大部分主流模型都支持)
    if any(
        kw in model_lower
        for kw in ["qwen", "gpt", "claude", "deepseek", "kimi", "glm", "gemini", "moonshot"]
    ):
        caps["tools"] = True

    return caps


def get_provider_slug_from_base_url(base_url: str) -> str | None:
    """
    从 base_url 推断服务商标识

    Examples:
        "https://api.openai.com/v1" -> "openai"
        "https://dashscope.aliyuncs.com/..." -> "dashscope"
        "https://openrouter.ai/api/v1" -> "openrouter"
    """
    for domain, slug in URL_TO_PROVIDER.items():
        if domain in base_url:
            return slug

    return None


def get_all_providers() -> list[str]:
    """获取所有已知的服务商"""
    return list(MODEL_CAPABILITIES.keys())


def get_models_by_provider(provider_slug: str) -> list[str]:
    """获取指定服务商的所有已知模型"""
    return list(MODEL_CAPABILITIES.get(provider_slug, {}).keys())


def supports_capability(model_name: str, capability: str, provider_slug: str | None = None) -> bool:
    """检查模型是否支持某种能力"""
    caps = infer_capabilities(model_name, provider_slug)
    return caps.get(capability, False)


def is_thinking_only(model_name: str, provider_slug: str | None = None) -> bool:
    """检查模型是否仅支持思考模式"""
    caps = infer_capabilities(model_name, provider_slug)
    return caps.get("thinking_only", False)
