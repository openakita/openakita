#!/usr/bin/env python3
"""百度视频笔记 - 百度千帆 AppBuilder 封装

视频解析、笔记生成。

用法:
    python3 video_notes.py analyze "https://example.com/video.mp4"
    python3 video_notes.py notes "https://example.com/lecture.mp4"
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '_shared'))
from baidu_appbuilder import parse_common_args, run_skill_query


def build_analyze_query(video_url: str) -> str:
    return (
        f"请解析以下视频内容：{video_url}\n"
        "请提供视频的主题概述、关键时间节点、核心内容摘要和主要观点。"
    )


def build_notes_query(video_url: str, style: str = "") -> str:
    query = f"请为以下视频生成结构化笔记：{video_url}"
    if style == "outline":
        query += "\n请以大纲形式组织，使用多级标题和要点。"
    elif style == "cornell":
        query += "\n请使用康奈尔笔记法格式，包含笔记栏、线索栏和总结栏。"
    elif style == "mindmap":
        query += "\n请以思维导图文本格式呈现，展示核心概念和分支关系。"
    else:
        query += "\n请包含时间戳标注、核心知识点、重要引用和个人思考留白。"
    return query


def main() -> None:
    parser = parse_common_args("百度视频笔记 - 视频解析与笔记生成")
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser("analyze", help="分析视频内容")
    p_analyze.add_argument("video_url", help="视频 URL")

    p_notes = sub.add_parser("notes", help="生成视频笔记")
    p_notes.add_argument("video_url", help="视频 URL")
    p_notes.add_argument("--style", default="",
                         choices=["outline", "cornell", "mindmap", ""],
                         help="笔记风格")

    args = parser.parse_args()

    if args.command == "analyze":
        query = build_analyze_query(args.video_url)
    else:
        query = build_notes_query(args.video_url, args.style)

    run_skill_query(args, query)


if __name__ == "__main__":
    main()
