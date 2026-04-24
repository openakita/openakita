#!/usr/bin/env python3
"""腾讯会议 REST API 封装。

认证: TENCENT_MEETING_APPID + TENCENT_MEETING_SECRET_KEY + TENCENT_MEETING_SDK_ID
签名算法: HMAC-SHA256
"""

import argparse
import hashlib
import hmac
import json
import os
import random
import string
import sys
import time
import urllib.request
import urllib.error

BASE_URL = "https://api.meeting.qq.com/v1"


def get_config():
    appid = os.environ.get("TENCENT_MEETING_APPID", "")
    secret = os.environ.get("TENCENT_MEETING_SECRET_KEY", "")
    sdk_id = os.environ.get("TENCENT_MEETING_SDK_ID", "")
    if not all([appid, secret, sdk_id]):
        print("错误: 请设置 TENCENT_MEETING_APPID, TENCENT_MEETING_SECRET_KEY, "
              "TENCENT_MEETING_SDK_ID", file=sys.stderr)
        sys.exit(1)
    return appid, secret, sdk_id


def sign_request(method: str, uri: str, body: str, secret: str) -> tuple[str, str, str]:
    timestamp = str(int(time.time()))
    nonce = "".join(random.choices(string.ascii_letters + string.digits, k=16))
    sign_str = f"{method}\n{uri}\n{body}\n{timestamp}\n{nonce}"
    signature = hmac.new(secret.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
    return timestamp, nonce, signature


def api_request(method: str, uri: str, body: dict | None = None) -> dict:
    appid, secret, sdk_id = get_config()
    body_str = json.dumps(body) if body else ""
    timestamp, nonce, signature = sign_request(method, uri, body_str, secret)

    url = f"{BASE_URL}{uri}"
    data = body_str.encode() if body_str else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("AppId", appid)
    req.add_header("X-TC-Key", sdk_id)
    req.add_header("X-TC-Timestamp", timestamp)
    req.add_header("X-TC-Nonce", nonce)
    req.add_header("X-TC-Signature", signature)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode() if e.fp else ""
        print(f"HTTP {e.code}: {err_body}", file=sys.stderr)
        sys.exit(1)


def cmd_create(args):
    body = {
        "subject": args.subject,
        "type": 0,
        "start_time": args.start_time,
        "end_time": args.end_time,
    }
    if args.userid:
        body["userid"] = args.userid
    data = api_request("POST", "/meetings", body)
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_get(args):
    data = api_request("GET", f"/meetings/{args.meeting_id}")
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_cancel(args):
    body = {"meeting_id": args.meeting_id, "reason_code": 1}
    if args.reason:
        body["reason_detail"] = args.reason
    data = api_request("POST", f"/meetings/{args.meeting_id}/cancel", body)
    print("会议已取消" if not data else json.dumps(data, ensure_ascii=False, indent=2))


def cmd_list(args):
    params = f"?userid={args.userid}" if args.userid else ""
    data = api_request("GET", f"/meetings{params}")
    if isinstance(data, dict) and "meeting_info_list" in data:
        for m in data["meeting_info_list"]:
            print(f"  {m.get('meeting_id', '?'):>15s}  {m.get('subject', '')}")
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description="腾讯会议 REST API (env: TENCENT_MEETING_APPID, "
                    "TENCENT_MEETING_SECRET_KEY, TENCENT_MEETING_SDK_ID)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="创建会议")
    p_create.add_argument("--subject", required=True, help="会议主题")
    p_create.add_argument("--start-time", dest="start_time", required=True,
                          help="开始时间 (Unix 时间戳)")
    p_create.add_argument("--end-time", dest="end_time", required=True,
                          help="结束时间 (Unix 时间戳)")
    p_create.add_argument("--userid", help="发起人用户 ID")

    p_get = sub.add_parser("get", help="查询会议")
    p_get.add_argument("meeting_id", help="会议 ID")

    p_cancel = sub.add_parser("cancel", help="取消会议")
    p_cancel.add_argument("meeting_id", help="会议 ID")
    p_cancel.add_argument("--reason", help="取消原因")

    p_list = sub.add_parser("list", help="列出会议")
    p_list.add_argument("--userid", help="用户 ID (筛选)")

    args = parser.parse_args()
    {"create": cmd_create, "get": cmd_get,
     "cancel": cmd_cancel, "list": cmd_list}[args.command](args)


if __name__ == "__main__":
    main()

