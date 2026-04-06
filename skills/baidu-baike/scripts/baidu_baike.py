#!/usr/bin/env python3
"""百度百科 API 封装 CLI 工具。

查询百度百科词条摘要信息。

环境变量:
    BAIDU_API_KEY  （可选，当前公开接口无需认证）

示例:
    python3 baidu_baike.py search "量子计算"
    python3 baidu_baike.py search "人工智能" --length 1000
"""

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BAIKE_API = "https://baike.baidu.com/api/openapi/BaikeLemmaCardApi"


def search_baike(keyword: str, bk_length: int = 600) -> dict:
    params = urllib.parse.urlencode({
        "scope": "103",
        "format": "json",
        "appid": "379020",
        "bk_key": keyword,
        "bk_length": bk_length,
    })
    url = f"{BAIKE_API}?{params}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; BaikeCLI/1.0)",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw_response": raw}


def main():
    parser = argparse.ArgumentParser(
        description="百度百科 API CLI — 查询词条摘要",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               "  python3 baidu_baike.py search \"量子计算\"\n"
               "  python3 baidu_baike.py search \"人工智能\" --length 1000",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_search = sub.add_parser("search", help="搜索百科词条")
    p_search.add_argument("keyword", help="搜索关键词")
    p_search.add_argument("--length", type=int, default=600,
                          help="返回摘要长度 (默认 600)")

    args = parser.parse_args()

    try:
        if args.command == "search":
            result = search_baike(args.keyword, args.length)
        else:
            parser.print_help()
            sys.exit(1)

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
