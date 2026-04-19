"""IM channel optional dependency mapping (pure data, no business imports).

Referenced by:
- openakita.main (_ensure_channel_deps)
- openakita.setup.wizard (_check_channel_deps)
- openakita.setup_center.bridge (ensure-channel-deps)
"""

# channel name -> [(import_name, pip_package), ...]
CHANNEL_DEPS: dict[str, list[tuple[str, str]]] = {
    "feishu": [("lark_oapi", "lark-oapi")],
    "lark": [("lark_oapi", "lark-oapi")],
    "dingtalk": [("dingtalk_stream", "dingtalk-stream")],
    "wework": [("aiohttp", "aiohttp"), ("Crypto", "pycryptodome")],
    "wework_ws": [("websockets", "websockets"), ("cryptography", "cryptography")],
    "onebot": [("websockets", "websockets")],
    "onebot_reverse": [("websockets", "websockets")],
    "qqbot": [("websockets", "websockets")],
    "wechat": [("httpx", "httpx"), ("Crypto", "pycryptodome")],
}

# channel name -> pyproject.toml extras name (used for pip install openakita[xxx] hints)
CHANNEL_EXTRAS: dict[str, str] = {
    "feishu": "feishu",
    "lark": "feishu",
    "dingtalk": "dingtalk",
    "wework": "wework",
    "wework_ws": "wework_ws",
    "onebot": "onebot",
    "onebot_reverse": "onebot",
    "qqbot": "qqbot",
    "wechat": "wechat",
}
