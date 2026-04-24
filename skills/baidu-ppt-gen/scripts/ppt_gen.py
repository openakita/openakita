#!/usr/bin/env python3
"""百度 PPT 生成器 - 百度千帆 AppBuilder 封装

主题 PPT 大纲与内容生成。

用法:
    python3 ppt_gen.py generate "Q2季度销售报告"
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '_shared'))
from baidu_appbuilder import parse_common_args, run_skill_query


def build_generate_query(
    topic: str, slides: int = 0, style: str = "", language: str = ""
) -> str:
    query = f"请为以下主题生成 PPT 内容：{topic}"
    if slides > 0:
        query += f"，共 {slides} 页幻灯片"
    if style:
        query += f"，风格：{style}"
    if language:
        query += f"，语言：{language}"
    query += (
        "。请为每页提供标题、要点内容和演讲者备注，"
        "并在开头给出整体大纲。"
    )
    return query


def main() -> None:
    parser = parse_common_args("百度 PPT 生成器 - 主题 PPT 内容生成")
    sub = parser.add_subparsers(dest="command", required=True)

    p_gen = sub.add_parser("generate", help="生成 PPT 大纲与内容")
    p_gen.add_argument("topic", help="PPT 主题")
    p_gen.add_argument("--slides", type=int, default=0, help="幻灯片页数")
    p_gen.add_argument("--style", default="",
                       choices=["business", "academic", "creative", "minimal", ""],
                       help="PPT 风格")
    p_gen.add_argument("--language", default="", choices=["zh", "en", ""],
                       help="语言")

    args = parser.parse_args()
    query = build_generate_query(args.topic, args.slides, args.style, args.language)
    run_skill_query(args, query)


if __name__ == "__main__":
    main()

