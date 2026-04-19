"""Heuristic detection for multi-step tasks."""

import re as _re

__all__ = ["should_require_todo"]


def should_require_todo(user_message: str) -> bool:
    """
    Detect whether a user request should use Todo mode (multi-step task detection).

    Recommendation 18: raise the threshold so Todo is only enabled for
    multi-tool collaboration or clearly multi-step tasks. Execute simple
    tasks directly without over-planning.

    Trigger conditions:
    1. Contains 5+ action words (clearly a complex task)
    2. Contains 3+ action words + a connector (clearly multi-step)
    3. Contains 3+ action words + comma separation (clearly multi-step)
    """
    if not user_message:
        return False

    msg = user_message.lower()

    zh_action_words = [
        "打开",
        "搜索",
        "截图",
        "发给",
        "发送",
        "写",
        "创建",
        "执行",
        "运行",
        "读取",
        "查看",
        "保存",
        "下载",
        "上传",
        "复制",
        "粘贴",
        "删除",
        "编辑",
        "修改",
        "更新",
        "安装",
        "配置",
        "设置",
        "启动",
        "关闭",
    ]
    en_action_words = [
        "open",
        "search",
        "screenshot",
        "send",
        "write",
        "create",
        "execute",
        "run",
        "read",
        "view",
        "save",
        "download",
        "upload",
        "copy",
        "paste",
        "delete",
        "edit",
        "modify",
        "update",
        "install",
        "configure",
        "setup",
        "start",
        "stop",
        "close",
        "deploy",
        "build",
        "test",
        "refactor",
        "migrate",
        "fix",
        "implement",
        "add",
        "remove",
    ]

    zh_connectors = ["然后", "接着", "之后", "并且", "再", "最后"]
    en_connectors = ["then", "after that", "next", "finally", "and then", "followed by", "also"]

    action_count = sum(1 for w in zh_action_words if w in msg)
    for w in en_action_words:
        if _re.search(r"\b" + _re.escape(w), msg):
            action_count += 1

    has_connector = any(w in msg for w in zh_connectors) or any(
        _re.search(r"\b" + _re.escape(w) + r"\b", msg) for w in en_connectors
    )

    comma_separated = "，" in msg or "," in msg

    if action_count >= 5:
        return True
    if action_count >= 3 and has_connector:
        return True
    return bool(action_count >= 3 and comma_separated)
