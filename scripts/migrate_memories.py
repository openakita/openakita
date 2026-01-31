#!/usr/bin/env python3
"""
记忆迁移脚本

功能:
1. 将现有 memories.json 批量向量化到 ChromaDB
2. 重置 MEMORY.md 为精华摘要格式
3. 验证迁移结果

使用方法:
    python scripts/migrate_memories.py
    python scripts/migrate_memories.py --dry-run  # 仅检查，不执行
    python scripts/migrate_memories.py --reset-memory-md  # 只重置 MEMORY.md
"""

import json
import sys
import argparse
from pathlib import Path
from datetime import datetime

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))


def load_memories(memories_file: Path) -> list[dict]:
    """加载现有记忆"""
    if not memories_file.exists():
        print(f"❌ 记忆文件不存在: {memories_file}")
        return []
    
    try:
        with open(memories_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"✅ 加载了 {len(data)} 条记忆")
        return data
    except Exception as e:
        print(f"❌ 加载记忆失败: {e}")
        return []


def migrate_to_vector_store(memories: list[dict], data_dir: Path) -> int:
    """将记忆迁移到向量库"""
    try:
        from openakita.memory.vector_store import VectorStore
    except ImportError as e:
        print(f"❌ 导入 VectorStore 失败: {e}")
        print("   请确保已安装依赖: pip install sentence-transformers chromadb")
        return 0
    
    print("\n📦 初始化向量存储...")
    vector_store = VectorStore(data_dir=data_dir)
    
    if not vector_store.enabled:
        print("❌ 向量存储初始化失败")
        return 0
    
    print(f"✅ 向量存储已启用 (模型: {vector_store.model_name})")
    
    # 批量添加
    print(f"\n🔄 开始迁移 {len(memories)} 条记忆...")
    
    batch_data = []
    for m in memories:
        batch_data.append({
            "id": m.get("id", ""),
            "content": m.get("content", ""),
            "type": m.get("type", "fact"),
            "priority": m.get("priority", "short_term"),
            "importance": m.get("importance_score", 0.5),
            "tags": m.get("tags", []),
        })
    
    added = vector_store.batch_add(batch_data)
    print(f"✅ 成功迁移 {added} 条记忆到向量库")
    
    # 验证
    stats = vector_store.get_stats()
    print(f"\n📊 向量库统计:")
    print(f"   - 总记忆数: {stats['count']}")
    print(f"   - 模型: {stats['model']}")
    print(f"   - 设备: {stats['device']}")
    
    return added


def reset_memory_md(memory_md_path: Path, memories: list[dict]) -> bool:
    """重置 MEMORY.md 为精华摘要格式"""
    print(f"\n📝 重置 MEMORY.md: {memory_md_path}")
    
    # 按类型分组
    by_type = {
        "preference": [],
        "rule": [],
        "fact": [],
        "skill": [],
    }
    
    for m in memories:
        # 只选取永久或长期记忆
        priority = m.get("priority", "short_term")
        if priority not in ("permanent", "long_term"):
            continue
        
        mem_type = m.get("type", "fact").lower()
        if mem_type in by_type:
            by_type[mem_type].append(m)
    
    # 按重要性排序，每类最多 3-5 条
    for key in by_type:
        by_type[key].sort(key=lambda x: x.get("importance_score", 0), reverse=True)
        by_type[key] = by_type[key][:5 if key == "fact" else 3]
    
    # 生成 Markdown
    lines = [
        "# Core Memory",
        "",
        "> Agent 核心记忆，每次对话都会加载。每日凌晨自动刷新。",
        f"> 最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]
    
    if by_type["preference"]:
        lines.append("## 用户偏好")
        for m in by_type["preference"]:
            lines.append(f"- {m.get('content', '')}")
        lines.append("")
    
    if by_type["rule"]:
        lines.append("## 重要规则")
        for m in by_type["rule"]:
            lines.append(f"- {m.get('content', '')}")
        lines.append("")
    
    if by_type["fact"]:
        lines.append("## 关键事实")
        for m in by_type["fact"]:
            lines.append(f"- {m.get('content', '')}")
        lines.append("")
    
    if by_type["skill"]:
        lines.append("## 成功模式")
        for m in by_type["skill"][:2]:
            lines.append(f"- {m.get('content', '')}")
        lines.append("")
    
    if not any(by_type.values()):
        lines.append("## 记忆")
        lines.append("[暂无核心记忆]")
        lines.append("")
    
    content = "\n".join(lines)
    
    try:
        # 备份旧文件
        if memory_md_path.exists():
            backup_path = memory_md_path.with_suffix(".md.bak")
            memory_md_path.rename(backup_path)
            print(f"   已备份旧文件到: {backup_path}")
        
        # 写入新文件
        memory_md_path.write_text(content, encoding="utf-8")
        print(f"✅ MEMORY.md 已重置 ({len(content)} 字符)")
        return True
        
    except Exception as e:
        print(f"❌ 重置 MEMORY.md 失败: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="记忆迁移脚本")
    parser.add_argument("--dry-run", action="store_true", help="仅检查，不执行")
    parser.add_argument("--reset-memory-md", action="store_true", help="只重置 MEMORY.md")
    args = parser.parse_args()
    
    print("=" * 60)
    print("OpenAkita 记忆系统迁移脚本")
    print("=" * 60)
    
    # 路径配置
    data_dir = project_root / "data" / "memory"
    memories_file = data_dir / "memories.json"
    memory_md_path = project_root / "MEMORY.md"
    
    print(f"\n📂 数据目录: {data_dir}")
    print(f"📂 记忆文件: {memories_file}")
    print(f"📂 MEMORY.md: {memory_md_path}")
    
    # 加载记忆
    memories = load_memories(memories_file)
    
    if not memories:
        print("\n⚠️ 没有记忆需要迁移")
        return
    
    if args.dry_run:
        print("\n🔍 [DRY RUN] 仅检查，不执行迁移")
        print(f"   将迁移 {len(memories)} 条记忆到向量库")
        print(f"   将重置 MEMORY.md")
        return
    
    if args.reset_memory_md:
        # 只重置 MEMORY.md
        reset_memory_md(memory_md_path, memories)
        return
    
    # 完整迁移
    print("\n" + "=" * 60)
    print("开始迁移...")
    print("=" * 60)
    
    # 1. 迁移到向量库
    migrated = migrate_to_vector_store(memories, data_dir)
    
    # 2. 重置 MEMORY.md
    reset_memory_md(memory_md_path, memories)
    
    # 完成
    print("\n" + "=" * 60)
    print("迁移完成!")
    print("=" * 60)
    print(f"✅ 向量化记忆: {migrated} 条")
    print(f"✅ MEMORY.md 已重置")
    print("\n下一步:")
    print("1. 启动 OpenAkita 验证功能正常")
    print("2. 测试向量搜索: 在对话中提问相关问题")
    print("3. 等待凌晨自动归纳，或手动执行 consolidate_memories")


if __name__ == "__main__":
    main()
