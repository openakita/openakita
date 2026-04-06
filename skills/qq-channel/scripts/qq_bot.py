#!/usr/bin/env python3
"""QQ 频道机器人 REST API 封装。

认证: QQ_BOT_APPID + QQ_BOT_TOKEN 环境变量
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error

PROD_BASE = "https://api.sgroup.qq.com"
SANDBOX_BASE = "https://sandbox.api.sgroup.qq.com"


def get_config(sandbox: bool = False):
    appid = os.environ.get("QQ_BOT_APPID", "")
    token = os.environ.get("QQ_BOT_TOKEN", "")
    if not appid or not token:
        print("错误: 请设置环境变量 QQ_BOT_APPID 和 QQ_BOT_TOKEN", file=sys.stderr)
        sys.exit(1)
    base = SANDBOX_BASE if sandbox else PROD_BASE
    return appid, token, base


def api_request(method: str, path: str, appid: str, token: str, base: str,
                body: dict | None = None) -> dict:
    url = f"{base}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"QQBot {token}")
    req.add_header("X-Union-Appid", appid)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode() if e.fp else ""
        print(f"HTTP {e.code}: {err_body}", file=sys.stderr)
        sys.exit(1)


def cmd_guilds(args):
    appid, token, base = get_config(args.sandbox)
    data = api_request("GET", "/users/@me/guilds", appid, token, base)
    if isinstance(data, list):
        for g in data:
            print(f"  {g.get('id', '?'):>20s}  {g.get('name', '')}")
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_channels(args):
    appid, token, base = get_config(args.sandbox)
    data = api_request("GET", f"/guilds/{args.guild_id}/channels", appid, token, base)
    if isinstance(data, list):
        for ch in data:
            kind = ch.get("type", "?")
            print(f"  {ch.get('id', '?'):>20s}  [type={kind}]  {ch.get('name', '')}")
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_send(args):
    appid, token, base = get_config(args.sandbox)
    body = {"content": args.content}
    data = api_request("POST", f"/channels/{args.channel_id}/messages", appid, token, base,
                       body=body)
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_members(args):
    appid, token, base = get_config(args.sandbox)
    data = api_request("GET", f"/guilds/{args.guild_id}/members?limit=100", appid, token, base)
    if isinstance(data, list):
        for m in data:
            user = m.get("user", {})
            print(f"  {user.get('id', '?'):>20s}  {user.get('username', '')}")
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description="QQ 频道机器人 API (env: QQ_BOT_APPID, QQ_BOT_TOKEN)")
    parser.add_argument("--sandbox", action="store_true", help="使用沙箱环境")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("guilds", help="列出频道列表")

    p_ch = sub.add_parser("channels", help="列出子频道")
    p_ch.add_argument("guild_id", help="频道 ID")

    p_send = sub.add_parser("send", help="发送消息到子频道")
    p_send.add_argument("channel_id", help="子频道 ID")
    p_send.add_argument("content", help="消息内容")

    p_mem = sub.add_parser("members", help="频道成员列表")
    p_mem.add_argument("guild_id", help="频道 ID")

    args = parser.parse_args()
    {"guilds": cmd_guilds, "channels": cmd_channels,
     "send": cmd_send, "members": cmd_members}[args.command](args)


if __name__ == "__main__":
    main()
