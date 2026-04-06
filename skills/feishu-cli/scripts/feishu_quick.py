#!/usr/bin/env python3
"""飞书 lark-cli 快速命令封装。

通过 subprocess 调用 lark-cli 完成常用操作。
"""

import argparse
import shutil
import subprocess
import sys


def ensure_cli():
    if not shutil.which("lark-cli"):
        print("错误: lark-cli 未安装，请先运行 setup.py", file=sys.stderr)
        sys.exit(1)


def run_cli(args: list[str]):
    cmd = ["lark-cli"] + args
    print(f"→ {' '.join(cmd)}")
    result = subprocess.run(cmd)
    sys.exit(result.returncode)


def cmd_send_msg(args):
    ensure_cli()
    run_cli(["im", "send-message",
             "--receive-id", args.receive_id,
             "--msg-type", "text",
             "--content", args.content])


def cmd_list_chats(args):
    ensure_cli()
    run_cli(["im", "list-chat"])


def cmd_create_doc(args):
    cli_args = ["doc", "create-document", "--title", args.title]
    if args.folder_token:
        cli_args += ["--folder-token", args.folder_token]
    ensure_cli()
    run_cli(cli_args)


def cmd_list_events(args):
    ensure_cli()
    run_cli(["calendar", "list-event", "--calendar-id", args.calendar_id])


def cmd_create_task(args):
    ensure_cli()
    run_cli(["task", "create-task", "--summary", args.summary])


def main():
    parser = argparse.ArgumentParser(description="飞书 lark-cli 快速命令")
    sub = parser.add_subparsers(dest="command", required=True)

    p_send = sub.add_parser("send-msg", help="发送消息")
    p_send.add_argument("--receive-id", required=True, help="接收者 ID")
    p_send.add_argument("--content", required=True, help="消息内容")

    sub.add_parser("list-chats", help="列出会话")

    p_doc = sub.add_parser("create-doc", help="创建文档")
    p_doc.add_argument("--title", required=True, help="文档标题")
    p_doc.add_argument("--folder-token", help="文件夹 token")

    p_evt = sub.add_parser("list-events", help="列出日历事件")
    p_evt.add_argument("--calendar-id", required=True, help="日历 ID")

    p_task = sub.add_parser("create-task", help="创建任务")
    p_task.add_argument("--summary", required=True, help="任务摘要")

    args = parser.parse_args()
    {"send-msg": cmd_send_msg, "list-chats": cmd_list_chats,
     "create-doc": cmd_create_doc, "list-events": cmd_list_events,
     "create-task": cmd_create_task}[args.command](args)


if __name__ == "__main__":
    main()
