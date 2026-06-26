"""
Session data recovery tool.

Recovers lost sessions from conversation_history JSONL files into sessions.json.
"""

import json
import hashlib
import sys
from datetime import datetime
from pathlib import Path


def load_jsonl_messages(jsonl_path: Path) -> list[dict]:
    """读取 JSONL 文件中的消息"""
    messages = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                messages.append(msg)
            except json.JSONDecodeError:
                continue
    return messages


def jsonl_to_session_messages(jsonl_messages: list[dict]) -> list[dict]:
    """将 JSONL 格式的消息转换为 session 格式的消息"""
    session_msgs = []
    for msg in jsonl_messages:
        entry = {"role": msg["role"], "content": msg.get("content", "")}
        if msg.get("tool_calls"):
            entry["tool_calls"] = msg["tool_calls"]
        if msg.get("tool_results"):
            entry["tool_results"] = msg["tool_results"]
        session_msgs.append(entry)
    return session_msgs


def extract_chat_id_from_filename(filename: str) -> tuple[str, str, str]:
    """从文件名提取 chat_id, channel, user_id"""
    name = filename.replace(".jsonl", "")
    if name.startswith("desktop__"):
        parts = name.split("__")
        chat_id = parts[1] if len(parts) > 1 else name
        user_id = parts[2] if len(parts) > 2 else "desktop_user"
        return chat_id, "desktop", user_id
    return name, "desktop", "desktop_user"


def generate_session_id(channel: str, chat_id: str, created_at: datetime) -> str:
    """生成 session ID，与系统格式保持一致"""
    ts = created_at.strftime("%Y%m%d%H%M%S")
    uid = hashlib.md5(f"{channel}_{chat_id}_{ts}".encode()).hexdigest()[:8]
    return f"{channel}_{chat_id}_{ts}_{uid}"


def create_session_dict(
    chat_id: str,
    channel: str,
    user_id: str,
    messages: list[dict],
    created_at: datetime,
    last_active: datetime,
) -> dict:
    """创建一个完整的 session 字典"""
    session_id = generate_session_id(channel, chat_id, created_at)
    return {
        "id": session_id,
        "channel": channel,
        "chat_id": chat_id,
        "user_id": user_id,
        "state": "active",
        "created_at": created_at.isoformat(),
        "last_active": last_active.isoformat(),
        "context": {
            "messages": messages,
            "variables": {},
            "current_task": None,
            "memory_scope": None,
            "summary": None,
            "topic_boundaries": [],
            "current_topic_start": 0,
        },
        "config": {
            "max_history": 100,
            "timeout_minutes": 43200,
            "language": "zh",
            "model": None,
            "custom_prompt": None,
            "auto_summarize": True,
        },
        "metadata": {"recovered": True, "recovered_at": datetime.now().isoformat()},
    }


def recover_sessions(workspace_path: str, output_path: str | None = None):
    workspace = Path(workspace_path)
    sessions_file = workspace / "data" / "sessions" / "sessions.json"
    conv_history_dir = workspace / "data" / "memory" / "conversation_history"

    if not sessions_file.exists():
        print(f"[ERROR] sessions.json 不存在: {sessions_file}")
        return
    if not conv_history_dir.exists():
        print(f"[ERROR] conversation_history 目录不存在: {conv_history_dir}")
        return

    with open(sessions_file, "r", encoding="utf-8") as f:
        existing_sessions = json.load(f)

    print(f"现有会话数: {len(existing_sessions)}")

    existing_chat_ids = set()
    for s in existing_sessions:
        existing_chat_ids.add(s.get("chat_id", ""))

    print(f"现有 chat_ids: {existing_chat_ids}")

    recovered_count = 0
    jsonl_files = sorted(conv_history_dir.glob("*.jsonl"))

    for jsonl_file in jsonl_files:
        chat_id, channel, user_id = extract_chat_id_from_filename(jsonl_file.name)

        if chat_id in existing_chat_ids:
            print(f"[SKIP] {jsonl_file.name} - 已在 sessions.json 中")
            continue

        jsonl_messages = load_jsonl_messages(jsonl_file)
        if not jsonl_messages:
            print(f"[SKIP] {jsonl_file.name} - 无消息")
            continue

        session_messages = jsonl_to_session_messages(jsonl_messages)

        timestamps = [m.get("timestamp") for m in jsonl_messages if m.get("timestamp")]
        if timestamps:
            created_at = datetime.fromisoformat(min(timestamps))
            last_active = datetime.fromisoformat(max(timestamps))
        else:
            stat = jsonl_file.stat()
            created_at = datetime.fromtimestamp(stat.st_ctime)
            last_active = datetime.fromtimestamp(stat.st_mtime)

        session = create_session_dict(
            chat_id=chat_id,
            channel=channel,
            user_id=user_id,
            messages=session_messages,
            created_at=created_at,
            last_active=last_active,
        )

        existing_sessions.append(session)
        existing_chat_ids.add(chat_id)
        recovered_count += 1

        user_msgs = [m for m in session_messages if m.get("role") == "user"]
        preview = ""
        if user_msgs:
            content = user_msgs[0].get("content", "")
            if isinstance(content, str):
                preview = content[:60].replace("\n", " ")
        print(
            f"[RECOVERED] {jsonl_file.name}: "
            f"{len(session_messages)} msgs, "
            f"created={created_at.strftime('%m-%d %H:%M')}, "
            f"preview: {preview}"
        )

    existing_sessions.sort(key=lambda s: s.get("last_active", ""), reverse=True)

    if output_path is None:
        output_path = str(workspace / "data" / "sessions" / "sessions_recovered.json")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(existing_sessions, f, ensure_ascii=False, indent=2)

    print(f"\n恢复完成!")
    print(f"  恢复了 {recovered_count} 个会话")
    print(f"  总会话数: {len(existing_sessions)}")
    print(f"  输出文件: {output_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python recover_sessions.py <workspace_path> [output_path]")
        print("示例: python recover_sessions.py C:\\Users\\xxx\\.openakita\\workspaces\\default")
        sys.exit(1)

    workspace = sys.argv[1]
    output = sys.argv[2] if len(sys.argv) > 2 else None
    recover_sessions(workspace, output)
