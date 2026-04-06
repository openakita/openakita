#!/usr/bin/env python3
"""Moltbook 发帖脚本 - 使用 OpenAkita 账号"""

import json
import requests
import sys
import os

# OpenAkita 的 API Key
API_KEY = "moltbook_sk_mKGncVylK4FEG2P4Lqc0Z1Litmt5INMF"
BASE_URL = "https://www.moltbook.com/api/v1"

def post(submolt: str, title: str, content: str):
    """发布帖子到 Moltbook"""
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    
    data = {
        "submolt": submolt,
        "title": title,
        "content": content
    }
    
    response = requests.post(
        f"{BASE_URL}/posts",
        headers=headers,
        json=data
    )
    
    if response.status_code == 200 or response.status_code == 201:
        result = response.json()
        print(f"✅ 发帖成功!")
        print(f"帖子 ID: {result.get('post', {}).get('id', 'N/A')}")
        print(f"链接: https://moltbook.com/post/{result.get('post', {}).get('id', '')}")
        return result
    else:
        print(f"❌ 发帖失败: {response.status_code}")
        print(response.text)
        return None

def check_status():
    """检查账号状态"""
    headers = {"Authorization": f"Bearer {API_KEY}"}
    response = requests.get(f"{BASE_URL}/agents/status", headers=headers)
    return response.json()

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("用法: python post.py <submolt> <title> <content>")
        print("示例: python post.py general '标题' '内容'")
        sys.exit(1)
    
    submolt = sys.argv[1]
    title = sys.argv[2]
    content = sys.argv[3]
    
    post(submolt, title, content)
