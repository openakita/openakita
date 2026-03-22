"""IM 通道可选依赖映射（纯数据，无业务导入）。

被以下模块引用:
- openakita.main (_ensure_channel_deps)
- openakita.setup.wizard (_check_channel_deps)
- openakita.setup_center.bridge (ensure-channel-deps)
"""

# 通道名 → [(import_name, pip_package), ...]
CHANNEL_DEPS: dict[str, list[tuple[str, str]]] = {
    "feishu": [("lark_oapi", "lark-oapi")],
    "dingtalk": [("dingtalk_stream", "dingtalk-stream")],
    "wework": [("aiohttp", "aiohttp"), ("Crypto", "pycryptodome")],
    "wework_ws": [("websockets", "websockets"), ("cryptography", "cryptography")],
    "onebot": [("websockets", "websockets")],
    "onebot_reverse": [("websockets", "websockets")],
    "qqbot": [("botpy", "qq-botpy"), ("pilk", "pilk")],
    "wechat": [("httpx", "httpx"), ("Crypto", "pycryptodome")],
}

# 通道名 → pyproject.toml extras 名称（用于 pip install openakita[xxx] 提示）
CHANNEL_EXTRAS: dict[str, str] = {
    "feishu": "feishu",
    "dingtalk": "dingtalk",
    "wework": "wework",
    "wework_ws": "wework_ws",
    "onebot": "onebot",
    "onebot_reverse": "onebot",
    "qqbot": "qqbot",
    "wechat": "wechat",
}
