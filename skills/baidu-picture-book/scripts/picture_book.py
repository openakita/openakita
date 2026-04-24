#!/usr/bin/env python3
"""百度绘本生成器 - 百度千帆 AppBuilder 封装

文字转绘本、画面构思。

用法:
    python3 picture_book.py generate "小兔子找妈妈的故事"
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '_shared'))
from baidu_appbuilder import parse_common_args, run_skill_query


def build_generate_query(
    story: str, style: str = "", pages: int = 0, audience: str = ""
) -> str:
    query = f"请根据以下故事创作一本绘本：{story}"
    if style:
        query += f"，绘画风格：{style}"
    if pages > 0:
        query += f"，共 {pages} 页"
    if audience:
        query += f"，适合 {audience} 阅读"
    query += "。请为每一页提供文字内容和配图描述（画面构思）。"
    return query


def main() -> None:
    parser = parse_common_args("百度绘本生成器 - 文字转绘本")
    sub = parser.add_subparsers(dest="command", required=True)

    p_gen = sub.add_parser("generate", help="生成绘本")
    p_gen.add_argument("story", help="故事描述或主题")
    p_gen.add_argument("--style", default="",
                       choices=["watercolor", "cartoon", "flat", "realistic", ""],
                       help="绘画风格")
    p_gen.add_argument("--pages", type=int, default=0, help="页数")
    p_gen.add_argument("--audience", default="",
                       choices=["toddler", "preschool", "elementary", ""],
                       help="目标年龄段")

    args = parser.parse_args()
    query = build_generate_query(args.story, args.style, args.pages, args.audience)
    run_skill_query(args, query)


if __name__ == "__main__":
    main()

