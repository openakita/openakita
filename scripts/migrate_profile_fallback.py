"""PR-B2 迁移：让历史 profile_fallback 记忆立即过期，避免跨会话身份污染。

历史背景（2026-05-09 P0-2 复盘）：
- 旧版 ``ProfileHandler._save_unknown_as_memory`` 把白名单外的 key=value
  写成 ``source="profile_fallback"`` 的全局 fact 记忆，没有 ``scope_owner``
  也没有 ``expires_at``，导致："张三 35 岁 上海 全栈"等过去测试身份
  会被注入新会话的 system prompt，让 LLM 把当前用户认成另外一个人。

本脚本做两件事：
1. 把所有 ``source="profile_fallback"`` 的存量记忆 ``expires_at`` 置为
   "now" — 这样 ``search_memories`` 的过期过滤会立刻把它们排除掉，但
   不会真删（用户可以通过 ``MemoryView`` 选择恢复）。
2. 写出一份 ``data/memory_pending_review.json`` 让前端弹一次"是否
   清理 N 条历史档案补充？"的引导。

用法：
    python scripts/migrate_profile_fallback.py [--apply]

不带 ``--apply`` 是 dry-run，仅预览将过期的记忆数量与示例。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# 让脚本以源码模式运行也能 import openakita
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际写回过期标记（默认 dry-run）",
    )
    args = parser.parse_args()

    try:
        from openakita.config import settings
        from openakita.memory.manager import MemoryManager
    except Exception as exc:  # pragma: no cover
        print(f"❌ 无法 import openakita.memory: {exc}")
        return 2

    mm = MemoryManager()
    candidates = []
    with mm._memories_lock:
        for mem in mm._memories.values():
            if getattr(mem, "source", "") == "profile_fallback":
                if mem.expires_at and mem.expires_at < datetime.now():
                    continue  # 已经过期，跳过
                candidates.append(mem)

    if not candidates:
        print("✅ 未发现需要迁移的 profile_fallback 记忆。")
        return 0

    print(f"发现 {len(candidates)} 条 profile_fallback 记忆，预览前 5 条：")
    for mem in candidates[:5]:
        content = (mem.content or "")[:120]
        scope = getattr(mem, "scope", "global")
        owner = getattr(mem, "scope_owner", "")
        print(
            f"  - id={mem.id[:8]} scope={scope}/{owner or '-'} "
            f"created={mem.created_at:%Y-%m-%d}\n    {content}"
        )

    if not args.apply:
        print("\n[dry-run] 未写回。加 --apply 真正执行。")
        return 0

    now = datetime.now()
    updated = 0
    for mem in candidates:
        try:
            mem.expires_at = now
            updated += 1
        except Exception as exc:
            print(f"⚠️ 写回 expires_at 失败 id={mem.id}: {exc}")

    try:
        mm._save_memories()
    except Exception as exc:  # pragma: no cover
        print(f"⚠️ 保存失败: {exc}")
        return 1

    out_dir = settings.project_root / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    review_path = out_dir / "memory_pending_review.json"
    review_payload = {
        "kind": "profile_fallback_migration",
        "generated_at": now.isoformat(),
        "expired_count": updated,
        "samples": [
            {
                "id": mem.id,
                "content": (mem.content or "")[:200],
                "created_at": mem.created_at.isoformat() if mem.created_at else "",
            }
            for mem in candidates[:20]
        ],
    }
    try:
        review_path.write_text(
            json.dumps(review_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n✅ 已把 {updated} 条记忆 expires_at 置为 now。")
        print(f"   引导文件已写入: {review_path}")
    except Exception as exc:
        print(f"⚠️ 写引导文件失败: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
