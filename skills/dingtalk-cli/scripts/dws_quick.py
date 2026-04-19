#!/usr/bin/env python3
"""钉钉 CLI 快速操作封装 — 通过 subprocess 调用官方 dws 命令。

注意：本脚本只是 ``dws`` 的薄封装，不是替代品。命令路径以
``dws schema`` 输出为准（钉钉官方 CLI 一切以 schema 为唯一事实源）。

用法示例：

    # 1. 通过机器人发消息到群（--text 支持 @filename 引用本地文件，与 dws CLI 一致）
    python dws_quick.py send --robot-code <BOT> --group <GID> --text "你好" --title "通知"

    # 2. 搜索联系人
    python dws_quick.py contacts --keyword "engineering"

    # 3. 列日程
    python dws_quick.py calendar

    # 4. 创建待办
    python dws_quick.py todo --title "季度报告" --executors "userId1,userId2"

    # 5. 任意 dws 子命令透传（attendance/approval 等命名空间快速发现）
    python dws_quick.py raw -- attendance --help
    python dws_quick.py raw -- approval instance list --help
"""

import argparse
import json
import shutil
import subprocess
import sys


def _ensure_dws() -> None:
    if not shutil.which("dws"):
        print(
            json.dumps(
                {"error": "dws 未安装，请先运行 scripts/dws_setup.py install"},
                ensure_ascii=False,
                indent=2,
            )
        )
        sys.exit(1)


def _run_dws(cmd_parts: list[str]) -> dict:
    _ensure_dws()
    full_cmd = ["dws", *cmd_parts]
    r = subprocess.run(full_cmd, capture_output=True, text=True)
    result: dict = {
        "command": " ".join(full_cmd),
        "exit_code": r.returncode,
        "stdout": r.stdout.strip(),
    }
    if r.stderr.strip():
        result["stderr"] = r.stderr.strip()
    return result


def cmd_send(args) -> dict:
    parts = [
        "chat",
        "message",
        "send-by-bot",
        "--robot-code",
        args.robot_code,
        "--group",
        args.group,
        "--text",
        args.text,
    ]
    if args.title:
        parts += ["--title", args.title]
    return _run_dws(parts)


def cmd_contacts(args) -> dict:
    return _run_dws(["contact", "user", "search", "--keyword", args.keyword])


def cmd_calendar(_args) -> dict:
    return _run_dws(["calendar", "event", "list"])


def cmd_todo(args) -> dict:
    parts = ["todo", "task", "create", "--title", args.title, "--executors", args.executors]
    return _run_dws(parts)


def cmd_raw(args) -> dict:
    """透传任意 dws 子命令。

    使用 ``--`` 分隔符把后续参数原样传给 dws，例如：

        python dws_quick.py raw -- attendance --help
    """
    if not args.dws_args:
        return {
            "error": "raw 子命令需要在 -- 之后提供具体的 dws 参数，"
            "例如：python dws_quick.py raw -- attendance --help"
        }
    return _run_dws(list(args.dws_args))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="钉钉 CLI 快速操作（dws 薄封装）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_send = sub.add_parser("send", help="通过机器人发消息到群")
    p_send.add_argument("--robot-code", required=True, help="机器人 robotCode")
    p_send.add_argument("--group", required=True, help="群 openConversationId")
    p_send.add_argument(
        "--text",
        required=True,
        help="消息文本（支持 @filename 从文件读取，与 dws CLI 一致）",
    )
    p_send.add_argument("--title", default="", help="可选标题（机器人卡片标题）")

    p_contacts = sub.add_parser("contacts", help="搜索联系人")
    p_contacts.add_argument("--keyword", required=True, help="搜索关键字")

    sub.add_parser("calendar", help="列出日历事件")

    p_todo = sub.add_parser("todo", help="创建待办")
    p_todo.add_argument("--title", required=True, help="待办标题")
    p_todo.add_argument(
        "--executors",
        required=True,
        help="执行人 userId（多个用英文逗号分隔）",
    )

    p_raw = sub.add_parser(
        "raw",
        help="透传任意 dws 子命令（用 -- 分隔，例如：raw -- attendance --help）",
    )
    p_raw.add_argument("dws_args", nargs=argparse.REMAINDER, help="原样传给 dws 的参数")

    args = parser.parse_args()
    dispatch = {
        "send": cmd_send,
        "contacts": cmd_contacts,
        "calendar": cmd_calendar,
        "todo": cmd_todo,
        "raw": cmd_raw,
    }
    result = dispatch[args.command](args)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
