#!/usr/bin/env python3
"""秒答应用构建器 - 百度千帆 AppBuilder 封装

智能应用生成、对话式构建。

用法:
    python3 miaoda.py create "一个待办事项应用"
    python3 miaoda.py chat "给应用加上日历提醒功能"
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '_shared'))
from baidu_appbuilder import parse_common_args, run_skill_query


def build_create_query(description: str, platform: str = "") -> str:
    query = f"请帮我创建一个应用：{description}"
    if platform:
        query += f"，目标平台：{platform}"
    query += "。请给出应用架构、功能模块和实现方案。"
    return query


def build_chat_query(message: str) -> str:
    return f"基于当前应用构建上下文，请处理以下需求：{message}"


def main() -> None:
    parser = parse_common_args("秒答应用构建器 - 智能应用生成与部署")
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="创建新应用")
    p_create.add_argument("description", help="应用描述")
    p_create.add_argument("--platform", default="", choices=["web", "mobile", "mini-program"],
                          help="目标平台")

    p_chat = sub.add_parser("chat", help="对话式构建")
    p_chat.add_argument("message", help="构建指令或需求描述")

    args = parser.parse_args()

    if args.command == "create":
        query = build_create_query(args.description, args.platform)
    else:
        query = build_chat_query(args.message)

    run_skill_query(args, query)


if __name__ == "__main__":
    main()

