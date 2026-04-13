#!/usr/bin/env python3
"""验证 iLink ``ilink/bot/sendmessage`` 是否接受 ``ITEM_VOICE`` 载荷。

在启用适配器 ``send_voice`` 前，用真实 Token 与目标用户跑一次探测：

1. 设置环境变量 ``WECHAT_TOKEN``（Bearer，扫码登录）、``WECHAT_TO_USER_ID``（ilink 用户 ID）。
2. 可选 ``WECHAT_CONTEXT_TOKEN``：若会话需要，填最近一次收消息里的 ``context_token``。
3. 准备本地 ``.silk`` 语音文件（与接收侧下载格式一致）。
4. 从仓库根目录执行::

     python scripts/verify_wechat_ilink_voice.py path/to/voice.silk

退出码：0 表示响应 ``ret == 0``；非 0 表示失败或参数缺失。打印完整 JSON 便于对照 errmsg。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import os
import sys
import uuid
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_sys_path() -> None:
    root = str(_repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)


async def _run(voice_path: Path) -> int:
    _ensure_sys_path()

    import httpx

    from openakita.channels.adapters.wechat import (
        ITEM_VOICE,
        MSG_STATE_FINISH,
        MSG_TYPE_BOT,
        WeChatAdapter,
    )

    token = os.environ.get("WECHAT_TOKEN", "").strip()
    to_user = os.environ.get("WECHAT_TO_USER_ID", "").strip()
    ctx = os.environ.get("WECHAT_CONTEXT_TOKEN", "").strip() or ""

    if not token or not to_user:
        print("缺少环境变量：WECHAT_TOKEN、WECHAT_TO_USER_ID", file=sys.stderr)
        return 2

    if not voice_path.is_file():
        print(f"语音文件不存在: {voice_path}", file=sys.stderr)
        return 2

    adapter = WeChatAdapter(token=token)
    adapter._http = httpx.AsyncClient(timeout=30.0)
    adapter.media_dir.mkdir(parents=True, exist_ok=True)

    uploaded = await adapter._cdn_upload(
        str(voice_path),
        to_user,
        "audio/silk",
        context_token=ctx,
    )
    aeskey_hex = uploaded["aeskey"]
    media_ref = {
        "encrypt_query_param": uploaded["download_param"],
        "aes_key": base64.b64encode(aeskey_hex.encode()).decode(),
        "encrypt_type": 1,
    }
    client_id = f"openakita-voice-probe-{uuid.uuid4().hex[:12]}"
    item = {
        "type": ITEM_VOICE,
        "voice_item": {
            "aeskey": aeskey_hex,
            "media": media_ref,
            "mid_size": uploaded["filesize_cipher"],
        },
    }
    body = {
        "msg": {
            "from_user_id": "",
            "to_user_id": to_user,
            "client_id": client_id,
            "message_type": MSG_TYPE_BOT,
            "message_state": MSG_STATE_FINISH,
            "item_list": [item],
            "context_token": ctx or None,
        }
    }

    await adapter._rate_limit_wait(to_user)
    resp = await adapter._api_post("ilink/bot/sendmessage", body)
    print(resp)

    if resp.get("ret") == 0:
        print("OK: ret=0，服务端未拒绝 ITEM_VOICE（仍请在微信客户端确认是否可播放）。")
        return 0

    print(
        f"FAIL: ret={resp.get('ret')!r} errmsg={resp.get('errmsg', '')!r}",
        file=sys.stderr,
    )
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="探测 iLink ITEM_VOICE sendmessage")
    parser.add_argument(
        "voice_file",
        type=Path,
        help="本地语音路径（建议 .silk）",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args.voice_file)))


if __name__ == "__main__":
    main()
