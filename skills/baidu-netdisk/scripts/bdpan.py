#!/usr/bin/env python3
"""百度网盘 Open API 封装 CLI 工具。

支持文件列表、搜索、配额信息、文件元信息查询。

环境变量:
    BAIDU_NETDISK_TOKEN  百度网盘 OAuth access_token

示例:
    python3 bdpan.py ls /apps/mydata/
    python3 bdpan.py search "报告" --dir /
    python3 bdpan.py info
    python3 bdpan.py meta 123456789
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

XPAN_FILE = "https://pan.baidu.com/rest/2.0/xpan/file"
XPAN_MULTI = "https://pan.baidu.com/rest/2.0/xpan/multimedia"
QUOTA_URL = "https://pan.baidu.com/api/quota"


def _get(url: str, params: dict) -> dict:
    qs = urllib.parse.urlencode(params)
    full_url = f"{url}?{qs}"
    req = urllib.request.Request(full_url, headers={
        "User-Agent": "pan.baidu.com",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def list_files(token: str, dir_path: str, order: str = "name",
               start: int = 0, limit: int = 100) -> dict:
    return _get(XPAN_FILE, {
        "method": "list",
        "access_token": token,
        "dir": dir_path,
        "order": order,
        "start": start,
        "limit": limit,
    })


def search_files(token: str, keyword: str, dir_path: str = "/",
                 page: int = 1, num: int = 100) -> dict:
    return _get(XPAN_FILE, {
        "method": "search",
        "access_token": token,
        "key": keyword,
        "dir": dir_path,
        "page": page,
        "num": num,
        "recursion": 1,
    })


def get_quota(token: str) -> dict:
    return _get(QUOTA_URL, {
        "access_token": token,
        "checkfree": 1,
        "checkexpire": 1,
    })


def file_metas(token: str, fsids: list) -> dict:
    return _get(XPAN_MULTI, {
        "method": "filemetas",
        "access_token": token,
        "fsids": json.dumps(fsids),
        "dlink": 1,
    })


def main():
    parser = argparse.ArgumentParser(
        description="百度网盘 Open API CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               "  python3 bdpan.py ls /apps/mydata/\n"
               "  python3 bdpan.py search \"报告\" --dir /\n"
               "  python3 bdpan.py info\n"
               "  python3 bdpan.py meta 123456789",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ls
    p_ls = sub.add_parser("ls", help="列出文件")
    p_ls.add_argument("path", nargs="?", default="/", help="目录路径 (默认 /)")
    p_ls.add_argument("--order", default="name",
                      choices=["name", "time", "size"], help="排序方式")
    p_ls.add_argument("--start", type=int, default=0, help="起始索引")
    p_ls.add_argument("--limit", type=int, default=100, help="数量上限")

    # search
    p_search = sub.add_parser("search", help="搜索文件")
    p_search.add_argument("keyword", help="搜索关键词")
    p_search.add_argument("--dir", default="/", help="搜索目录 (默认 /)")
    p_search.add_argument("--page", type=int, default=1, help="页码")
    p_search.add_argument("--num", type=int, default=100, help="每页数量")

    # info
    sub.add_parser("info", help="查看网盘配额信息")

    # meta
    p_meta = sub.add_parser("meta", help="获取文件元信息和下载链接")
    p_meta.add_argument("fsids", nargs="+", type=int, help="文件 fsid（可多个）")

    args = parser.parse_args()

    token = os.environ.get("BAIDU_NETDISK_TOKEN", "")
    if not token:
        print("错误: 请设置环境变量 BAIDU_NETDISK_TOKEN", file=sys.stderr)
        sys.exit(1)

    try:
        if args.command == "ls":
            result = list_files(token, args.path, args.order, args.start, args.limit)
        elif args.command == "search":
            result = search_files(token, args.keyword, args.dir, args.page, args.num)
        elif args.command == "info":
            result = get_quota(token)
        elif args.command == "meta":
            result = file_metas(token, args.fsids)
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
