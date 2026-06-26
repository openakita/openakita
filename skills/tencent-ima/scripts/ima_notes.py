#!/usr/bin/env python3
"""IMA 笔记 API 封装 — 纯 stdlib 实现"""

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


def cmd_search(args):
    body = {
        "search_type": 0,
        "query_info": {"title": args.query},
        "start": 0,
        "end": 20,
    }
    return _post("/openapi/note/v1/search_note_book", body)


def cmd_folders(_args):
    return _post("/openapi/note/v1/list_note_folder_by_cursor", {"cursor": "0", "limit": 20})


def cmd_list(args):
    body = {"folder_id": args.folder_id, "cursor": "", "limit": 20}
    return _post("/openapi/note/v1/list_note_by_folder_id", body)


def cmd_create(args):
    body = {"content_format": 1, "content": args.content}
    if args.folder_id:
        body["folder_id"] = args.folder_id
    return _post("/openapi/note/v1/import_doc", body)


def cmd_append(args):
    body = {"doc_id": args.doc_id, "content_format": 1, "content": args.content}
    return _post("/openapi/note/v1/append_doc", body)


def cmd_read(args):
    body = {"doc_id": args.doc_id, "target_content_format": 0}
    return _post("/openapi/note/v1/get_doc_content", body)


def main():
    parser = argparse.ArgumentParser(description="IMA 笔记 API CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("search", help="搜索笔记")
    p.add_argument("query", help="搜索关键词")

    sub.add_parser("folders", help="列出所有笔记本")

    p = sub.add_parser("list", help="列出笔记本中的笔记")
    p.add_argument("folder_id", help="笔记本 ID")

    p = sub.add_parser("create", help="新建笔记")
    p.add_argument("content", help="Markdown 内容")
    p.add_argument("--folder-id", default=None, help="目标笔记本 ID（可选）")

    p = sub.add_parser("append", help="向笔记追加内容")
    p.add_argument("doc_id", help="笔记 ID")
    p.add_argument("content", help="追加的文本")

    p = sub.add_parser("read", help="读取笔记内容")
    p.add_argument("doc_id", help="笔记 ID")

    args = parser.parse_args()
    dispatch = {
        "search": cmd_search,
        "folders": cmd_folders,
        "list": cmd_list,
        "create": cmd_create,
        "append": cmd_append,
        "read": cmd_read,
    }
    result = dispatch[args.command](args)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
