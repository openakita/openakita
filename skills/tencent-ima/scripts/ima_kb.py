#!/usr/bin/env python3
"""IMA 知识库 API 封装 — 纯 stdlib 实现"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error

BASE_URL = "https://ima.qq.com"


def _headers():
    cid = os.environ.get("IMA_OPENAPI_CLIENTID", "")
    key = os.environ.get("IMA_OPENAPI_APIKEY", "")
    if not cid or not key:
        print(
            json.dumps(
                {"error": "请设置环境变量 IMA_OPENAPI_CLIENTID 和 IMA_OPENAPI_APIKEY"},
                ensure_ascii=False,
            )
        )
        sys.exit(1)
    return {
        "Content-Type": "application/json",
        "ima-openapi-clientid": cid,
        "ima-openapi-apikey": key,
    }


def _post(path: str, body: dict) -> dict:
    url = BASE_URL + path
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=_headers(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "detail": e.read().decode()}
    except urllib.error.URLError as e:
        return {"error": str(e.reason)}


def cmd_get(args):
    ids = [i.strip() for i in args.ids.split(",")]
    return _post("/openapi/wiki/v1/get_knowledge_base", {"ids": ids})


def cmd_browse(args):
    body = {"cursor": "", "limit": 20, "knowledge_base_id": args.kb_id}
    return _post("/openapi/wiki/v1/get_knowledge_list", body)


def cmd_search(args):
    body = {"query": args.query, "cursor": "", "knowledge_base_id": args.kb_id}
    return _post("/openapi/wiki/v1/search_knowledge", body)


def cmd_search_kb(args):
    body = {"query": args.query, "cursor": "", "limit": 20}
    return _post("/openapi/wiki/v1/search_knowledge_base", body)


def cmd_import_url(args):
    urls = [u.strip() for u in args.urls.split(",")]
    body = {
        "knowledge_base_id": args.kb_id,
        "folder_id": args.folder_id,
        "urls": urls,
    }
    return _post("/openapi/wiki/v1/import_urls", body)


def cmd_addable(_args):
    return _post("/openapi/wiki/v1/get_addable_knowledge_base_list", {"cursor": "", "limit": 20})


def main():
    parser = argparse.ArgumentParser(description="IMA 知识库 API CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("get", help="获取知识库详情")
    p.add_argument("ids", help="知识库 ID（逗号分隔多个）")

    p = sub.add_parser("browse", help="浏览知识库内容")
    p.add_argument("--kb-id", required=True, help="知识库 ID")

    p = sub.add_parser("search", help="搜索知识")
    p.add_argument("query", help="搜索关键词")
    p.add_argument("--kb-id", required=True, help="知识库 ID")

    p = sub.add_parser("search-kb", help="搜索知识库")
    p.add_argument("query", help="搜索关键词")

    p = sub.add_parser("import-url", help="导入 URL 到知识库")
    p.add_argument("urls", help="URL（逗号分隔多个）")
    p.add_argument("--kb-id", required=True, help="知识库 ID")
    p.add_argument("--folder-id", required=True, dest="folder_id", help="文件夹 ID")

    sub.add_parser("addable", help="列出可添加的知识库")

    args = parser.parse_args()
    dispatch = {
        "get": cmd_get,
        "browse": cmd_browse,
        "search": cmd_search,
        "search-kb": cmd_search_kb,
        "import-url": cmd_import_url,
        "addable": cmd_addable,
    }
    result = dispatch[args.command](args)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
