#!/usr/bin/env python3
"""Moltbook 发帖脚本

凭据加载顺序（与 SKILL.md 一致）：
  1. 环境变量 MOLTBOOK_API_KEY
  2. ~/.config/moltbook/credentials.json 里的 "api_key"
  3. 都没有则报错退出（NEVER 把 key 硬编码到源码里）
"""

import json
import os
import sys
from pathlib import Path

import requests

BASE_URL = "https://www.moltbook.com/api/v1"


def _load_api_key() -> str:
    key = os.environ.get("MOLTBOOK_API_KEY", "").strip()
    if key:
        return key

    cred_path = Path.home() / ".config" / "moltbook" / "credentials.json"
    if cred_path.exists():
        try:
            data = json.loads(cred_path.read_text(encoding="utf-8"))
            key = str(data.get("api_key", "")).strip()
            if key:
                return key
        except (json.JSONDecodeError, OSError) as exc:
            print(f"⚠️ 读取 {cred_path} 失败: {exc}", file=sys.stderr)

    print(
        "❌ 未找到 Moltbook API Key。请设置环境变量 MOLTBOOK_API_KEY，\n"
        "   或在 ~/.config/moltbook/credentials.json 里写入 {\"api_key\": \"...\"}。",
        file=sys.stderr,
    )
    sys.exit(2)


def _auth_headers(api_key: str, *, json_body: bool = False) -> dict:
    headers = {"Authorization": f"Bearer {api_key}"}
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def post(submolt: str, title: str, content: str):
    """发布帖子到 Moltbook"""
    api_key = _load_api_key()
    data = {"submolt": submolt, "title": title, "content": content}

    response = requests.post(
        f"{BASE_URL}/posts",
        headers=_auth_headers(api_key, json_body=True),
        json=data,
    )

    if response.status_code in (200, 201):
        result = response.json()
        post_id = result.get("post", {}).get("id", "")
        print("✅ 发帖成功!")
        print(f"帖子 ID: {post_id or 'N/A'}")
        print(f"链接: https://moltbook.com/post/{post_id}")
        return result

    print(f"❌ 发帖失败: {response.status_code}")
    print(response.text)
    return None


def check_status():
    """检查账号状态"""
    api_key = _load_api_key()
    response = requests.get(
        f"{BASE_URL}/agents/status",
        headers=_auth_headers(api_key),
    )
    return response.json()


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("用法: python post.py <submolt> <title> <content>")
        print("示例: python post.py general '标题' '内容'")
        sys.exit(1)

    post(sys.argv[1], sys.argv[2], sys.argv[3])
