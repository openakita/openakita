#!/usr/bin/env python3
"""百度学术 - 百度千帆 AppBuilder 封装

学术论文搜索、引用关系查询。

用法:
    python3 baidu_scholar.py search "transformer attention mechanism"
    python3 baidu_scholar.py cite "Attention Is All You Need"
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '_shared'))
from baidu_appbuilder import parse_common_args, run_skill_query


def build_search_query(keywords: str, year: str = "", sort: str = "") -> str:
    query = f"请在百度学术中搜索以下关键词的相关论文：{keywords}"
    if year:
        query += f"，限定年份：{year}"
    if sort:
        query += f"，排序方式：{sort}"
    query += "。请返回论文标题、作者、发表时间、摘要和引用量。"
    return query


def build_cite_query(paper_title: str) -> str:
    return (
        f"请查询论文《{paper_title}》的引用关系，"
        "包括引用该论文的文献列表和该论文引用的参考文献。"
    )


def main() -> None:
    parser = parse_common_args("百度学术 - 论文搜索与引用查询")
    sub = parser.add_subparsers(dest="command", required=True)

    p_search = sub.add_parser("search", help="搜索学术论文")
    p_search.add_argument("keywords", help="搜索关键词")
    p_search.add_argument("--year", default="", help="限定年份，如 2024")
    p_search.add_argument("--sort", default="", choices=["relevance", "citation", "date"],
                          help="排序方式")

    p_cite = sub.add_parser("cite", help="查询论文引用关系")
    p_cite.add_argument("paper_title", help="论文标题")

    args = parser.parse_args()

    if args.command == "search":
        query = build_search_query(args.keywords, args.year, args.sort)
    else:
        query = build_cite_query(args.paper_title)

    run_skill_query(args, query)


if __name__ == "__main__":
    main()

