#!/usr/bin/env python3
"""百度百科词条查询 CLI 工具。

通过百度百科页面获取词条摘要信息。

示例:
    python3 baidu_baike.py search "量子计算"
    python3 baidu_baike.py search "人工智能"
"""

import argparse
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def search_baike(keyword: str) -> dict:
    encoded = urllib.parse.quote(keyword)
    url = f"https://baike.baidu.com/search/word?word={encoded}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        final_url = resp.url
        body = resp.read().decode("utf-8", errors="replace")

    result = {"keyword": keyword, "url": final_url}

    m = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', body)
    if m:
        result["summary"] = html.unescape(m.group(1)).strip()

    m = re.search(r"<title>([^<]+)</title>", body)
    if m:
        raw_title = html.unescape(m.group(1)).replace("_百度百科", "").strip()
        if "（" in raw_title:
            parts = raw_title.split("（", 1)
            result["title"] = parts[0].strip()
            result["subtitle"] = parts[1].rstrip("）").strip()
        else:
            result["title"] = raw_title

    for pattern, key in [
        (r'"abstract":"((?:[^"\\]|\\.)*)"', "abstract"),
        (r'"card_name":"((?:[^"\\]|\\.)*)"', "card_name"),
    ]:
        m = re.search(pattern, body)
        if m:
            try:
                result[key] = json.loads(f'"{m.group(1)}"')
            except json.JSONDecodeError:
                result[key] = m.group(1)

    if "抱歉，百度百科尚未收录" in body or "search/none" in final_url:
        result["found"] = False
        result["message"] = f"百度百科未收录「{keyword}」"
    else:
        result["found"] = True

    return result


def main():
    parser = argparse.ArgumentParser(
        description="百度百科 API CLI — 查询词条摘要",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='示例:\n  python3 baidu_baike.py search "量子计算"',
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_search = sub.add_parser("search", help="搜索百科词条")
    p_search.add_argument("keyword", help="搜索关键词")

    args = parser.parse_args()
    try:
        result = search_baike(args.keyword)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        print(json.dumps({"error": str(e), "detail": body}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

