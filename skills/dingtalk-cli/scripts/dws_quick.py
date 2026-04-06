#!/usr/bin/env python3
"""钉钉 CLI 快速操作封装 — 通过 subprocess 调用 dws 命令"""

import argparse
import json
import shutil
import subprocess
import sys


def _ensure_dws():
    if not shutil.which("dws"):
        print(json.dumps({"error": "dws 未安装，请先运行 dws_setup.py install"}, ensure_ascii=False, indent=2))
        sys.exit(1)


def _run_dws(cmd_parts: list[str]) -> dict:
    _ensure_dws()
    full_cmd = ["dws"] + cmd_parts
    r = subprocess.run(full_cmd, capture_output=True, text=True)
    result = {
        "command": " ".join(full_cmd),
        "exit_code": r.returncode,
        "stdout": r.stdout.strip(),
    }
    if r.stderr.strip():
        result["stderr"] = r.stderr.strip()
    return result


def cmd_send(args):
    parts = ["im", "send-message", "--conversation-id", args.conversation_id, "--content", args.content]
    return _run_dws(parts)


def cmd_contacts(_args):
    return _run_dws(["contact", "list"])


def cmd_calendar(_args):
    return _run_dws(["calendar", "list-events"])


def cmd_todo(args):
    parts = ["todo", "create", "--title", args.title]
    return _run_dws(parts)


def cmd_attendance(_args):
    return _run_dws(["attendance", "get-records"])


def cmd_approve(_args):
    return _run_dws(["approval", "list"])


def main():
    parser = argparse.ArgumentParser(description="钉钉 CLI 快速操作")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("send", help="发送消息")
    p.add_argument("--conversation-id", required=True, help="会话 ID")
    p.add_argument("content", help="消息内容")

    sub.add_parser("contacts", help="获取联系人列表")
    sub.add_parser("calendar", help="获取日程列表")

    p = sub.add_parser("todo", help="创建待办")
    p.add_argument("title", help="待办标题")

    sub.add_parser("attendance", help="获取考勤记录")
    sub.add_parser("approve", help="获取审批列表")

    args = parser.parse_args()
    dispatch = {
        "send": cmd_send,
        "contacts": cmd_contacts,
        "calendar": cmd_calendar,
        "todo": cmd_todo,
        "attendance": cmd_attendance,
        "approve": cmd_approve,
    }
    result = dispatch[args.command](args)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
