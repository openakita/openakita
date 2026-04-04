"""多步骤任务启发式检测"""

import re as _re

__all__ = ["should_require_todo"]


def should_require_todo(user_message: str) -> bool:
    """
    检测用户请求是否需要 Todo 模式（多步骤任务检测）

    建议 18：提高阈值，只在"多工具协作或明显多步"时启用
    简单任务直接执行，不要过度计划

    触发条件：
    1. 包含 5+ 个动作词（明显的复杂任务）
    2. 包含 3+ 个动作词 + 连接词（明确的多步骤）
    3. 包含 3+ 个动作词 + 逗号分隔（明确的多步骤）
    """
    if not user_message:
        return False

    msg = user_message.lower()

    zh_action_words = [
        "打开", "搜索", "截图", "发给", "发送", "写", "创建",
        "执行", "运行", "读取", "查看", "保存", "下载", "上传",
        "复制", "粘贴", "删除", "编辑", "修改", "更新", "安装",
        "配置", "设置", "启动", "关闭",
    ]
    en_action_words = [
        "open", "search", "screenshot", "send", "write", "create",
        "execute", "run", "read", "view", "save", "download", "upload",
        "copy", "paste", "delete", "edit", "modify", "update", "install",
        "configure", "setup", "start", "stop", "close", "deploy", "build",
        "test", "refactor", "migrate", "fix", "implement", "add", "remove",
    ]

    zh_connectors = ["然后", "接着", "之后", "并且", "再", "最后"]
    en_connectors = ["then", "after that", "next", "finally", "and then", "followed by", "also"]

    action_count = sum(1 for w in zh_action_words if w in msg)
    for w in en_action_words:
        if _re.search(r'\b' + _re.escape(w), msg):
            action_count += 1

    has_connector = (
        any(w in msg for w in zh_connectors)
        or any(_re.search(r'\b' + _re.escape(w) + r'\b', msg) for w in en_connectors)
    )

    comma_separated = "，" in msg or "," in msg

    if action_count >= 5:
        return True
    if action_count >= 3 and has_connector:
        return True
    return bool(action_count >= 3 and comma_separated)
