"""
每日记忆归纳器

功能:
1. 每日凌晨归纳当天的对话历史
2. 使用 LLM 提取精华记忆
3. 刷新 MEMORY.md 精华摘要
4. 清理过期历史文件
"""

import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

from .types import Memory, MemoryType, MemoryPriority
from .extractor import MemoryExtractor
from .consolidator import MemoryConsolidator

logger = logging.getLogger(__name__)


class DailyConsolidator:
    """
    每日记忆归纳器
    
    负责:
    - 读取昨天的所有对话历史
    - 使用 LLM 归纳精华
    - 存入长期记忆
    - 刷新 MEMORY.md
    """
    
    # MEMORY.md 最大字符数
    MEMORY_MD_MAX_CHARS = 800
    
    def __init__(
        self,
        data_dir: Path,
        memory_md_path: Path,
        memory_manager=None,
        brain=None,
    ):
        """
        Args:
            data_dir: 数据目录
            memory_md_path: MEMORY.md 路径
            memory_manager: MemoryManager 实例
            brain: LLM 大脑实例
        """
        self.data_dir = Path(data_dir)
        self.memory_md_path = Path(memory_md_path)
        self.memory_manager = memory_manager
        self.brain = brain
        
        # 子组件
        self.extractor = MemoryExtractor(brain)
        self.consolidator = MemoryConsolidator(data_dir, brain, self.extractor)
        
        # 每日摘要目录
        self.summaries_dir = self.data_dir / "daily_summaries"
        self.summaries_dir.mkdir(parents=True, exist_ok=True)
    
    async def consolidate_daily(self) -> dict:
        """
        执行每日归纳
        
        适合在凌晨 3:00 由定时任务调用
        
        Returns:
            归纳结果统计
        """
        logger.info("Starting daily memory consolidation...")
        
        result = {
            "timestamp": datetime.now().isoformat(),
            "sessions_processed": 0,
            "memories_extracted": 0,
            "memories_added": 0,
            "duplicates_removed": 0,
            "memory_md_refreshed": False,
            "cleanup": {},
        }
        
        try:
            # 1. 整理所有未处理的会话
            summaries, memories = await self.consolidator.consolidate_all_unprocessed()
            result["sessions_processed"] = len(summaries)
            result["memories_extracted"] = len(memories)
            
            # 2. 添加新记忆到 MemoryManager
            if self.memory_manager and memories:
                for memory in memories:
                    if self.memory_manager.add_memory(memory):
                        result["memories_added"] += 1
            
            # 3. 清理重复记忆（使用 LLM 判断语义重复）
            result["duplicates_removed"] = await self._cleanup_duplicate_memories()
            
            # 4. 刷新 MEMORY.md
            await self.refresh_memory_md()
            result["memory_md_refreshed"] = True
            
            # 5. 清理过期历史
            result["cleanup"] = self.consolidator.cleanup_history()
            
            # 6. 保存每日摘要
            self._save_daily_summary(result, summaries)
            
            logger.info(f"Daily consolidation completed: {result}")
            
        except Exception as e:
            logger.error(f"Daily consolidation failed: {e}")
            result["error"] = str(e)
        
        return result
    
    async def refresh_memory_md(self) -> bool:
        """
        刷新 MEMORY.md 精华摘要
        
        从 memories.json 选取最重要的记忆，生成精简的 Markdown
        
        Returns:
            是否成功
        """
        try:
            # 获取所有记忆
            memories = []
            if self.memory_manager:
                memories = list(self.memory_manager._memories.values())
            
            # 按类型和优先级分组
            by_type = {
                "preference": [],
                "rule": [],
                "fact": [],
                "skill": [],
            }
            
            for m in memories:
                # 只选取永久或长期记忆
                if m.priority not in (MemoryPriority.PERMANENT, MemoryPriority.LONG_TERM):
                    continue
                
                type_key = m.type.value.lower()
                if type_key in by_type:
                    by_type[type_key].append(m)
            
            # 按重要性排序，每类最多 3-5 条
            for key in by_type:
                by_type[key].sort(key=lambda x: x.importance_score, reverse=True)
                by_type[key] = by_type[key][:5 if key == "fact" else 3]
            
            # 生成 Markdown
            content = self._generate_memory_md(by_type)
            
            # 检查长度限制
            if len(content) > self.MEMORY_MD_MAX_CHARS:
                # 压缩内容
                content = await self._compress_memory_md(content)
            
            # 写入文件
            self.memory_md_path.write_text(content, encoding="utf-8")
            logger.info("MEMORY.md refreshed")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to refresh MEMORY.md: {e}")
            return False
    
    def _generate_memory_md(self, by_type: dict) -> str:
        """生成 MEMORY.md 内容"""
        lines = [
            "# Core Memory",
            "",
            "> Agent 核心记忆，每次对话都会加载。每日凌晨自动刷新。",
            f"> 最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
        ]
        
        # 用户偏好
        if by_type["preference"]:
            lines.append("## 用户偏好")
            for m in by_type["preference"]:
                lines.append(f"- {m.content}")
            lines.append("")
        
        # 重要规则
        if by_type["rule"]:
            lines.append("## 重要规则")
            for m in by_type["rule"]:
                lines.append(f"- {m.content}")
            lines.append("")
        
        # 关键事实
        if by_type["fact"]:
            lines.append("## 关键事实")
            for m in by_type["fact"]:
                lines.append(f"- {m.content}")
            lines.append("")
        
        # 成功模式（可选）
        if by_type["skill"]:
            lines.append("## 成功模式")
            for m in by_type["skill"][:2]:  # 最多 2 条
                lines.append(f"- {m.content}")
            lines.append("")
        
        # 如果所有分类都为空
        if not any(by_type.values()):
            lines.append("## 记忆")
            lines.append("[暂无核心记忆]")
            lines.append("")
        
        return "\n".join(lines)
    
    async def _compress_memory_md(self, content: str) -> str:
        """
        使用 LLM 压缩 MEMORY.md 内容
        
        当内容超过限制时调用
        """
        if not self.brain:
            # 没有 brain，简单截断
            return content[:self.MEMORY_MD_MAX_CHARS]
        
        try:
            prompt = f"""将以下记忆精简为更短的版本，保留最重要的信息。

当前内容:
{content}

要求:
- 总长度不超过 {self.MEMORY_MD_MAX_CHARS} 字符
- 保持 Markdown 格式
- 保留最重要的 5-10 条记忆
- 每条记忆精简为一句话"""
            
            response = await self.brain.think(
                prompt,
                system="你是内容精简专家。输出精简后的 Markdown 内容。"
            )
            
            return response.strip()
            
        except Exception as e:
            logger.error(f"Failed to compress MEMORY.md: {e}")
            return content[:self.MEMORY_MD_MAX_CHARS]
    
    def _save_daily_summary(self, result: dict, summaries: list) -> None:
        """保存每日摘要"""
        today = datetime.now().strftime("%Y-%m-%d")
        summary_file = self.summaries_dir / f"{today}.json"
        
        data = {
            "date": today,
            "result": result,
            "sessions": [s.to_dict() for s in summaries] if summaries else [],
        }
        
        try:
            with open(summary_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.debug(f"Saved daily summary: {summary_file}")
        except Exception as e:
            logger.error(f"Failed to save daily summary: {e}")
    
    def get_yesterday_summary(self) -> Optional[dict]:
        """获取昨天的归纳摘要"""
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        summary_file = self.summaries_dir / f"{yesterday}.json"
        
        if summary_file.exists():
            try:
                with open(summary_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        
        return None
    
    def get_recent_summaries(self, days: int = 7) -> list[dict]:
        """获取最近几天的归纳摘要"""
        summaries = []
        
        for i in range(days):
            date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            summary_file = self.summaries_dir / f"{date}.json"
            
            if summary_file.exists():
                try:
                    with open(summary_file, "r", encoding="utf-8") as f:
                        summaries.append(json.load(f))
                except Exception:
                    pass
        
        return summaries
    
    # ==================== 去重清理 ====================
    
    # 向量相似度阈值（用于初筛可能的重复）
    DUPLICATE_DISTANCE_THRESHOLD = 0.3
    
    async def _cleanup_duplicate_memories(self) -> int:
        """
        清理重复记忆
        
        策略:
        1. 按类型分组遍历所有记忆
        2. 用向量搜索找相似的记忆对
        3. 用 LLM 判断是否真的重复
        4. 如果重复，保留更重要/更新的那条，删除另一条
        
        Returns:
            删除的重复记忆数量
        """
        if not self.memory_manager:
            return 0
        
        memories = list(self.memory_manager._memories.values())
        if len(memories) < 2:
            return 0
        
        logger.info(f"Checking {len(memories)} memories for duplicates...")
        
        deleted_ids = set()
        checked_pairs = set()  # 避免重复检查同一对
        
        for memory in memories:
            if memory.id in deleted_ids:
                continue
            
            # 向量搜索找相似记忆
            if not self.memory_manager.vector_store.enabled:
                continue
            
            similar = self.memory_manager.vector_store.search(
                memory.content, 
                limit=5,
                filter_type=memory.type.value,  # 只在同类型中查找
            )
            
            for other_id, distance in similar:
                if other_id == memory.id or other_id in deleted_ids:
                    continue
                
                # 避免重复检查
                pair_key = tuple(sorted([memory.id, other_id]))
                if pair_key in checked_pairs:
                    continue
                checked_pairs.add(pair_key)
                
                # 距离太远，跳过
                if distance > self.DUPLICATE_DISTANCE_THRESHOLD:
                    continue
                
                other_memory = self.memory_manager._memories.get(other_id)
                if not other_memory:
                    continue
                
                # 用 LLM 判断是否真的重复
                is_dup = await self.memory_manager.check_duplicate_with_llm(
                    memory.content, 
                    other_memory.content
                )
                
                if is_dup:
                    # 决定保留哪一条
                    # 规则: 保留更重要的，同等重要保留更新的
                    keep, remove = self._decide_which_to_keep(memory, other_memory)
                    
                    logger.info(f"Duplicate found: '{remove.content[:30]}...' -> keeping '{keep.content[:30]}...'")
                    
                    # 删除重复的
                    self.memory_manager.delete_memory(remove.id)
                    deleted_ids.add(remove.id)
        
        if deleted_ids:
            logger.info(f"Removed {len(deleted_ids)} duplicate memories")
        
        return len(deleted_ids)
    
    def _decide_which_to_keep(self, mem1: Memory, mem2: Memory) -> tuple[Memory, Memory]:
        """
        决定保留哪条记忆
        
        规则:
        1. 优先级: PERMANENT > LONG_TERM > SHORT_TERM > TRANSIENT
        2. 重要性: importance_score 高的优先
        3. 时间: 更新的优先
        
        Returns:
            (保留的记忆, 删除的记忆)
        """
        priority_order = {
            MemoryPriority.PERMANENT: 4,
            MemoryPriority.LONG_TERM: 3,
            MemoryPriority.SHORT_TERM: 2,
            MemoryPriority.TRANSIENT: 1,
        }
        
        # 比较优先级
        p1 = priority_order.get(mem1.priority, 0)
        p2 = priority_order.get(mem2.priority, 0)
        
        if p1 != p2:
            return (mem1, mem2) if p1 > p2 else (mem2, mem1)
        
        # 比较重要性
        if abs(mem1.importance_score - mem2.importance_score) > 0.1:
            return (mem1, mem2) if mem1.importance_score > mem2.importance_score else (mem2, mem1)
        
        # 比较更新时间
        return (mem1, mem2) if mem1.updated_at > mem2.updated_at else (mem2, mem1)
