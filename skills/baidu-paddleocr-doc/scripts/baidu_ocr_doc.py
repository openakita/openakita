#!/usr/bin/env python3
"""百度 OCR 文档识别 API 封装 CLI 工具。

支持通用文档识别和表格识别，输入可为本地图片或 URL。

环境变量:
    BAIDU_OCR_AK  百度 OCR API Key
    BAIDU_OCR_SK  百度 OCR Secret Key

示例:
    python3 baidu_ocr_doc.py doc /path/to/document.jpg
    python3 baidu_ocr_doc.py doc https://example.com/doc.png
    python3 baidu_ocr_doc.py table /path/to/table.jpg
"""

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
DOC_ANALYSIS_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1/doc_analysis"
TABLE_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1/table"
DOC_OFFICE_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1/doc_analysis_office"


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


def _is_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


def _read_image_base64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _build_body(input_path: str) -> str:
    if _is_url(input_path):
        return urllib.parse.urlencode({"url": input_path})
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"文件不存在: {input_path}")
    img_b64 = _read_image_base64(input_path)
    return urllib.parse.urlencode({"image": img_b64})


def ocr_request(token: str, api_url: str, input_path: str) -> dict:
    url = f"{api_url}?access_token={token}"
    body = _build_body(input_path).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def main():
    parser = argparse.ArgumentParser(
        description="百度 OCR 文档识别 CLI — 支持文档识别和表格识别",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               "  python3 baidu_ocr_doc.py doc /path/to/document.jpg\n"
               "  python3 baidu_ocr_doc.py doc https://example.com/doc.png\n"
               "  python3 baidu_ocr_doc.py table /path/to/table.jpg",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_doc = sub.add_parser("doc", help="通用文档识别")
    p_doc.add_argument("input", help="图片文件路径或 URL")
    p_doc.add_argument("--office", action="store_true",
                       help="使用办公文档识别接口")

    p_table = sub.add_parser("table", help="表格识别")
    p_table.add_argument("input", help="图片文件路径或 URL")

    args = parser.parse_args()

    ak = os.environ.get("BAIDU_OCR_AK", "")
    sk = os.environ.get("BAIDU_OCR_SK", "")
    if not ak or not sk:
        print("错误: 请设置环境变量 BAIDU_OCR_AK 和 BAIDU_OCR_SK", file=sys.stderr)
        sys.exit(1)

    try:
        token = get_access_token(ak, sk)

        if args.command == "doc":
            api_url = DOC_OFFICE_URL if args.office else DOC_ANALYSIS_URL
            result = ocr_request(token, api_url, args.input)
        elif args.command == "table":
            result = ocr_request(token, TABLE_URL, args.input)
        else:
            parser.print_help()
            sys.exit(1)

        print(json.dumps(result, ensure_ascii=False, indent=2))
    except FileNotFoundError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)
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

