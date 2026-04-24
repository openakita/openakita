"""
清理 openakita.db 中的垃圾记忆数据。

1. 删除 "成功完成:" / "任务 '" 前缀的垃圾 skill
2. persona_trait 同 dimension 只保留最新一条
3. fact 去重，保留最完整的一条
"""

import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "memory" / "openakita.db"


def cleanup():
    if not DB_PATH.exists():
        print(f"ERROR: {DB_PATH} not found")
        return

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    total_deleted = 0

    # ── 1. 删除垃圾 skill 记忆 ──
    print("=== 1. 清理垃圾 skill 记忆 ===")
    cur.execute("""
        SELECT id, content FROM memories
        WHERE type = 'skill'
        AND (content LIKE '成功完成:%' OR content LIKE '任务 ''%使用工具组合%')
    """)
    garbage_skills = cur.fetchall()
    print(f"  找到 {len(garbage_skills)} 条垃圾 skill")
    for row in garbage_skills:
        cur.execute("DELETE FROM memories WHERE id = ?", (row["id"],))
        cur.execute("DELETE FROM memories_fts WHERE rowid IN (SELECT rowid FROM memories_fts WHERE content MATCH ?)", (row["id"],))
    total_deleted += len(garbage_skills)

    # ── 2. persona_trait 同 dimension 去重 ──
    print("\n=== 2. persona_trait 同 dimension 去重 ===")
    cur.execute("""
        SELECT id, content, created_at FROM memories
        WHERE type = 'persona_trait'
        ORDER BY created_at DESC
    """)
    traits = cur.fetchall()

    seen_dims = {}
    dup_ids = []
    for row in traits:
        content = row["content"]
        dim = content.split("=")[0].strip() if "=" in content else content[:30]
        if dim in seen_dims:
            dup_ids.append(row["id"])
        else:
            seen_dims[dim] = row["id"]

    print(f"  总计 {len(traits)} 条 persona_trait，去重后保留 {len(seen_dims)} 条，删除 {len(dup_ids)} 条")
    for dup_id in dup_ids:
        cur.execute("DELETE FROM memories WHERE id = ?", (dup_id,))
    total_deleted += len(dup_ids)

    # ── 3. fact 去重 ──
    print("\n=== 3. fact 去重 ===")
    cur.execute("""
        SELECT id, content, created_at, LENGTH(content) as clen FROM memories
        WHERE type = 'fact'
        ORDER BY clen DESC, created_at DESC
    """)
    facts = cur.fetchall()

    kept_contents = []
    fact_dup_ids = []
    for row in facts:
        content = row["content"].strip()
        is_dup = False
        for kept in kept_contents:
            if content in kept or kept in content:
                is_dup = True
                break
            words_a = set(content.lower().split())
            words_b = set(kept.lower().split())
            if words_a and words_b:
                overlap = len(words_a & words_b) / max(len(words_a), len(words_b))
                if overlap > 0.7:
                    is_dup = True
                    break
        if is_dup:
            fact_dup_ids.append(row["id"])
        else:
            kept_contents.append(content)

    print(f"  总计 {len(facts)} 条 fact，去重后保留 {len(facts) - len(fact_dup_ids)} 条，删除 {len(fact_dup_ids)} 条")
    for dup_id in fact_dup_ids:
        cur.execute("DELETE FROM memories WHERE id = ?", (dup_id,))
    total_deleted += len(fact_dup_ids)

    # ── 4. 重建 FTS 索引 ──
    print("\n=== 4. 重建 FTS 索引 ===")
    try:
        cur.execute("DELETE FROM memories_fts")
        cur.execute("""
            INSERT INTO memories_fts(rowid, content, subject, predicate, tags)
            SELECT rowid, content, subject, predicate, tags FROM memories
        """)
        print("  FTS 索引重建完成")
    except Exception as e:
        print(f"  FTS 重建失败（可跳过）: {e}")

    conn.commit()
    conn.close()

    print(f"\n总计删除 {total_deleted} 条垃圾记忆")


if __name__ == "__main__":
    cleanup()

