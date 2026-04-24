#!/usr/bin/env python3
"""淘宝客工具脚本（折淘客 API）— 纯 stdlib 实现"""

import argparse
import json
import os
import re
import sys
import urllib.request
import urllib.error
import urllib.parse

API_BASE = "https://api.zhetaoke.com:10001/api"


def _creds():
    key = os.environ.get("ZHETAOKE_APP_KEY", "")
    sid = os.environ.get("ZHETAOKE_SID", "")
    if not key or not sid:
        print(json.dumps(
            {"error": "请设置环境变量 ZHETAOKE_APP_KEY 和 ZHETAOKE_SID"},
            ensure_ascii=False,
        ))
        sys.exit(1)
    return key, sid


def _get(path: str, params: dict) -> dict:
    qs = urllib.parse.urlencode(params)
    url = f"{API_BASE}/{path}?{qs}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "detail": e.read().decode()}
    except urllib.error.URLError as e:
        return {"error": str(e.reason)}


def _extract_item_id(url_or_id: str) -> str:
    """从淘宝链接中提取商品 ID，或直接返回纯数字 ID"""
    m = re.search(r"[?&]id=(\d+)", url_or_id)
    if m:
        return m.group(1)
    if url_or_id.isdigit():
        return url_or_id
    return url_or_id


def cmd_convert(args):
    key, sid = _creds()
    item_id = _extract_item_id(args.item)
    params = {
        "appkey": key,
        "sid": sid,
        "pid": args.pid,
        "num_iid": item_id,
    }
    result = _get("open_gaoyongzhuanlian.ashx", params)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_search(args):
    key, _ = _creds()
    params = {
        "appkey": key,
        "keyword": args.query,
        "page_no": args.page,
        "page_size": args.size,
    }
    result = _get("open_shangpin_query.ashx", params)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_compare(args):
    key, _ = _creds()
    params = {
        "appkey": key,
        "keyword": args.query,
        "page_no": 1,
        "page_size": args.top,
        "sort": "price_asc",
    }
    result = _get("open_shangpin_query.ashx", params)

    if isinstance(result, dict) and "error" not in result:
        items = []
        for item_list in result.values():
            if isinstance(item_list, list):
                items = item_list
                break
        if items:
            summary = []
            for item in items[:args.top]:
                summary.append({
                    "title": item.get("tao_title", item.get("title", "")),
                    "price": item.get("size", item.get("quanhou_jiage", "")),
                    "shop": item.get("shop_title", ""),
                    "sales": item.get("volume", ""),
                })
            print(json.dumps({"compare": summary, "count": len(summary)}, ensure_ascii=False, indent=2))
            return

    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="淘宝客工具（折淘客 API）")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("convert", help="商品转链（高佣）")
    p.add_argument("item", help="淘宝商品链接或 ID")
    p.add_argument("--pid", default="", help="推广位 PID")

    p = sub.add_parser("search", help="搜索商品")
    p.add_argument("query", help="搜索关键词")
    p.add_argument("--page", type=int, default=1, help="页码 (default: 1)")
    p.add_argument("--size", type=int, default=20, help="每页数量 (default: 20)")

    p = sub.add_parser("compare", help="比价（按价格排序）")
    p.add_argument("query", help="搜索关键词")
    p.add_argument("--top", type=int, default=5, help="显示前 N 个结果 (default: 5)")

    args = parser.parse_args()
    dispatch = {
        "convert": cmd_convert,
        "search": cmd_search,
        "compare": cmd_compare,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()

