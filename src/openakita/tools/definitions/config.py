"""
系统配置工具定义

统一的 system_config 工具，用户通过聊天即可查看/修改所有系统配置。
支持：查看配置、修改设置、LLM 端点管理、UI 偏好、动态配置发现。
"""

CONFIG_TOOLS = [
    {
        "name": "system_config",
        "category": "Config",
        "description": (
            "Unified system configuration tool. When user wants to: "
            "(1) view or change any system setting (log level, thinking mode, proxy, IM channel, etc.), "
            "(2) add/remove/test LLM endpoints, "
            "(3) switch UI theme or language, "
            "(4) discover what settings are available. "
            "IMPORTANT: Before calling action=set or action=add_endpoint, "
            "ALWAYS use ask_user first to confirm the changes with the user. "
            "If unsure which config key to use, call action=discover first."
        ),
        "detail": """统一系统配置工具，覆盖所有配置操作。

## action 说明

### discover -- 发现可配置项
列出所有可配置项及其元信息（描述、类型、当前值、默认值）。
系统新增的配置项会自动出现，无需修改工具代码。
可通过 category 参数过滤特定分类。

### get -- 查看当前配置
读取当前配置值，支持按分类或指定 key 查看。
敏感字段（API Key 等）自动脱敏。

### set -- 修改配置
更新 .env 文件并热重载到内存。
- updates 使用**大写环境变量名**作为 key，如 {"LOG_LEVEL": "DEBUG"}
- 自动做类型校验
- 只读字段（路径/数据库）会被拒绝
- 某些字段修改后需重启才能生效，会在响应中标注

### add_endpoint -- 添加 LLM 端点
根据 provider 自动补全默认 base_url 和 api_type。
API Key 存入 .env，JSON 中只引用环境变量名。
添加后自动热重载。

### remove_endpoint -- 删除 LLM 端点
按名称删除并热重载。

### test_endpoint -- 测试端点连通性
发送轻量请求验证 API 可达性，返回延迟和状态。

### set_ui -- 设置 UI 偏好
切换桌面客户端的主题和语言。非 Desktop 通道会提示仅影响桌面端。

## 使用流程
1. 不确定 key 名 → 先 discover
2. 查看当前值 → get
3. 修改前 → 用 ask_user 确认
4. 确认后 → set / add_endpoint / remove_endpoint
""",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "discover",
                        "get",
                        "set",
                        "add_endpoint",
                        "remove_endpoint",
                        "test_endpoint",
                        "set_ui",
                    ],
                    "description": "操作类型",
                },
                "category": {
                    "type": "string",
                    "description": (
                        "配置分类过滤（discover/get 时可选）。"
                        "常见分类: Agent, LLM, 日志, 代理, IM/Telegram, IM/飞书, "
                        "会话, 定时任务, 人格, 活人感, 桌面通知, Embedding/记忆搜索, 语音识别 等。"
                        "调用 discover 不带 category 可查看所有分类。"
                    ),
                },
                "keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "指定查询的配置字段名列表（get 时可选，如 ['log_level', 'thinking_mode']）",
                },
                "updates": {
                    "type": "object",
                    "description": (
                        "要修改的配置键值对（set 时必填）。"
                        "key 使用大写环境变量名，如 {\"LOG_LEVEL\": \"DEBUG\", \"PROACTIVE_ENABLED\": \"true\"}"
                    ),
                },
                "endpoint": {
                    "type": "object",
                    "description": (
                        "LLM 端点配置（add_endpoint 时必填）。"
                        "字段: name(必填), provider(必填), model(必填), "
                        "api_key(可选,存入.env), api_type(可选,自动推断), "
                        "base_url(可选,自动补全), priority(可选,默认10), "
                        "max_tokens(可选), context_window(可选), timeout(可选), "
                        "capabilities(可选,如['text','tools','vision'])"
                    ),
                    "properties": {
                        "name": {"type": "string", "description": "端点唯一名称"},
                        "provider": {
                            "type": "string",
                            "description": "服务商 slug（如 openai, anthropic, deepseek, dashscope, ollama 等）",
                        },
                        "model": {"type": "string", "description": "模型名称"},
                        "api_key": {
                            "type": "string",
                            "description": "API Key（会自动存入 .env，不存入 JSON）",
                        },
                        "api_type": {
                            "type": "string",
                            "enum": ["openai", "anthropic"],
                            "description": "API 协议类型（不填则根据 provider 自动推断）",
                        },
                        "base_url": {
                            "type": "string",
                            "description": "API 地址（不填则根据 provider 自动补全）",
                        },
                        "priority": {
                            "type": "integer",
                            "description": "优先级，数字越小越优先（默认 10）",
                        },
                        "max_tokens": {"type": "integer", "description": "最大输出 token 数"},
                        "context_window": {"type": "integer", "description": "上下文窗口大小"},
                        "timeout": {"type": "integer", "description": "请求超时（秒）"},
                        "capabilities": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "模型能力列表，如 ['text','tools','vision','thinking']",
                        },
                    },
                    "required": ["name", "provider", "model"],
                },
                "endpoint_name": {
                    "type": "string",
                    "description": "端点名称（remove_endpoint / test_endpoint 时必填）",
                },
                "target": {
                    "type": "string",
                    "enum": ["main", "compiler", "stt"],
                    "description": "端点类型（默认 main）: main=主端点, compiler=Prompt编译, stt=语音识别",
                },
                "theme": {
                    "type": "string",
                    "enum": ["light", "dark", "system"],
                    "description": "UI 主题（set_ui 时）",
                },
                "language": {
                    "type": "string",
                    "enum": ["zh", "en"],
                    "description": "UI 语言（set_ui 时）",
                },
            },
            "required": ["action"],
        },
        "triggers": [
            "User wants to view or change system settings",
            "User asks about available configuration options",
            "User wants to add, remove, or test LLM endpoints",
            "User wants to switch theme or language",
        ],
        "examples": [
            {
                "scenario": "查看所有可配置项",
                "params": {"action": "discover"},
            },
            {
                "scenario": "查看 Agent 相关配置",
                "params": {"action": "get", "category": "Agent"},
            },
            {
                "scenario": "修改日志级别",
                "params": {"action": "set", "updates": {"LOG_LEVEL": "DEBUG"}},
            },
            {
                "scenario": "添加 DeepSeek 端点",
                "params": {
                    "action": "add_endpoint",
                    "endpoint": {
                        "name": "deepseek-chat",
                        "provider": "deepseek",
                        "model": "deepseek-chat",
                        "api_key": "sk-xxx",
                    },
                },
            },
            {
                "scenario": "切换暗色主题",
                "params": {"action": "set_ui", "theme": "dark"},
            },
        ],
    },
]
