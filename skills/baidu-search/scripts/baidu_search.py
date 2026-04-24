#!/usr/bin/env python3
"""百度搜索 API 封装 CLI 工具。

使用百度千帆 AK/SK 认证，支持网页搜索和图片搜索。

环境变量:
    BAIDU_QIANFAN_AK  百度千帆 API Key
    BAIDU_QIANFAN_SK  百度千帆 Secret Key

示例:
    python3 baidu_search.py web "Python 异步编程"
    python3 baidu_search.py image "风景壁纸" --page-size 5
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
SEARCH_URL = "https://aip.baidubce.com/rest/2.0/search/v1/resource/search"


def get_access_token(ak: str, sk: str) -> str:
    params = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": ak,
        "client_secret": sk,
    })
    url = f"{TOKEN_URL}?{params}"
    req = urllib.request.Request(url, method="POST", data=b"")
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    if "access_token" not in data:
        raise RuntimeError(f"获取 access_token 失败: {data}")
    return data["access_token"]


def search(token: str, query: str, search_type: str = "web",
           page_no: int = 1, page_size: int = 10) -> dict:
    url = f"{SEARCH_URL}?access_token={token}"
    body = json.dumps({
        "query": query,
        "search_type": search_type,
        "page_no": page_no,
        "page_size": page_size,
    }).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def main():
    parser = argparse.ArgumentParser(
        description="百度搜索 API CLI — 支持网页搜索和图片搜索",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               "  python3 baidu_search.py web \"Python 异步编程\"\n"
               "  python3 baidu_search.py image \"风景壁纸\" --page-size 5",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    for cmd, stype, desc in [
        ("web", "web", "网页搜索"),
        ("image", "image", "图片搜索"),
    ]:
        p = sub.add_parser(cmd, help=desc)
        p.add_argument("query", help="搜索关键词")
        p.add_argument("--page-no", type=int, default=1, help="页码 (默认 1)")
        p.add_argument("--page-size", type=int, default=10, help="每页数量 (默认 10)")
        p.set_defaults(search_type=stype)

    args = parser.parse_args()

    ak = os.environ.get("BAIDU_QIANFAN_AK", "")
    sk = os.environ.get("BAIDU_QIANFAN_SK", "")
    if not ak or not sk:
        print("错误: 请设置环境变量 BAIDU_QIANFAN_AK 和 BAIDU_QIANFAN_SK", file=sys.stderr)
        sys.exit(1)

    try:
        token = get_access_token(ak, sk)
        result = search(token, args.query, args.search_type, args.page_no, args.page_size)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        print(json.dumps({"error": str(e), "detail": body}, ensure_ascii=False, indent=2),
              file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

